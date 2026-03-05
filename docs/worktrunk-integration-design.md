# Design: Worktrunk Integration for Grove

## Context

Grove manages the **submodule graph** (sync, push, merge across nested repos). [Worktrunk](https://github.com/max-sixty/worktrunk) (`wt`) manages the **worktree lifecycle** (create, switch, list, remove, hooks, LLM commits). The two tools complement each other, but users shouldn't need both installed. This design integrates worktrunk's worktree lifecycle features into grove as a first-class capability, with worktrunk as an optional backend that enhances native functionality when available.

**Goals:**
- Grove handles the full worktree lifecycle (switch, list, remove, step commands, hooks) with or without wt
- When wt is installed, grove delegates to it for richer UX (interactive picker, CI status, background operations)
- When wt is NOT installed, grove provides faithful native implementations of core features
- Unsupported native features fail fast with clear error messages
- LLM-powered commit/squash messages via shell command or Strands (optional `grove[llm]` extra)
- Configuration mirrors wt's file layout (`~/.config/grove/config.toml`, `.config/grove.toml`)

**Non-Goals:**
- Re-implement all wt UX in native mode (CI integrations, advanced interactive flows)
- Introduce unrelated features beyond integration scope
- Remove legacy config support immediately — provide migration path instead

---

## Architecture: Command-Local Delegation

Each worktree command module owns its complete flow: resolve context, attempt delegation, run native implementation. There is no class hierarchy or centralized backend dispatcher.

### Delegation model

Standalone functions in `src/grove/worktree_backend.py`:

```python
def maybe_delegate_switch(repo_root, args) -> int | None
def maybe_delegate_list(repo_root, args) -> int | None
def maybe_delegate_remove(repo_root, args) -> int | None
def maybe_delegate_step(repo_root, args) -> int | None
def maybe_delegate_hook(repo_root, args) -> int | None
```

Semantics:
- Returns `None` → native path should continue
- Returns `int` → delegated execution finished; return that exit code

Each command module calls its delegation function first:

```python
# Example: worktree_switch.py
def run(args):
    repo_root = find_repo_root()
    rc = maybe_delegate_switch(repo_root, args)
    if rc is not None:
        return rc
    return switch_native(args, repo_root)
```

### Why not an ABC hierarchy

An abstract `WorktreeBackend` class with `NativeBackend` and `WtBackend` subclasses was considered and rejected:
- Creates impedance mismatch — argparse `Namespace` objects must be reconstructed via `type("Args", ...)()` or `SimpleNamespace` to call backend methods
- Double dispatch (CLI → backend → implementation) adds indirection without value
- wt-only commands that should delegate first end up blocked before backend resolution
- Each new flag requires updating the ABC interface, not just the command module that uses it

The delegation function pattern avoids all of these: each module owns its args, delegates when appropriate, and falls through to native logic.

### Backend contract (documentation only)

A `Protocol` type documents the delegation surface for reference. It is not used for runtime dispatch:

```python
# worktree_backend.py — documentation only
class BackendContract(Protocol):
    """Documents the full set of delegation-capable operations."""
    def switch(self, repo_root, args) -> int | None: ...
    def list_worktrees(self, repo_root, args) -> int | None: ...
    def remove(self, repo_root, args) -> int | None: ...
    def step(self, repo_root, args) -> int | None: ...
    def hook(self, repo_root, args) -> int | None: ...
```

### Backend resolution

Config:
```toml
[worktree]
backend = "auto"  # "auto" (default) | "native" | "wt"
```

Resolution rules:
1. `"native"` → never delegate
2. `"wt"` → always delegate; error if `wt` not found on PATH
3. `"auto"` → delegate when `wt` exists on PATH; else native

### Delegation environment

When delegating to wt, grove must synthesize a wt-compatible config:

```python
@contextmanager
def _delegation_env(repo_root):
    """Synthesize wt config, set WORKTRUNK_CONFIG_PATH, clean up on exit."""
    raw = _load_merged_raw_config(repo_root)
    wt_config = _synthesize_wt_config(raw)
    tmp = _write_temp_config(wt_config)
    try:
        env = os.environ.copy()
        env["WORKTRUNK_CONFIG_PATH"] = str(tmp)
        yield env
    finally:
        tmp.unlink(missing_ok=True)
```

`_synthesize_wt_config(raw)` maps all grove config sections to wt equivalents — worktree path template, list defaults, commit settings, merge lifecycle, CI config, hooks.

### Delegation helpers

- `_append_flag(cmd, flag, value)` — conditional flag builder to reduce boilerplate
- `_build_wt_command(subcommand, args, extra_flags)` — constructs the `wt` invocation

**Dry-run for wt-delegated commands**: When grove has `--dry-run` but wt doesn't support it for a given command, report `"will run: wt <command>"` and stop.

---

## CLI Structure

The CLI is split across three files to separate concerns:

| File | Responsibility | Approx size |
|------|---------------|-------------|
| `src/grove/cli.py` | `main()`, alias expansion, `--no-color`, env var activation | ~100 lines |
| `src/grove/cli_parsers.py` | `build_parser()` — full argparse tree | ~1000 lines |
| `src/grove/cli_dispatch.py` | `dispatch_command(args, parser) -> int` — routing via lazy imports | ~100 lines |

### Rationale

The current `cli.py` is ~710 lines and will roughly double with the new subcommands. A monolithic file mixing parser definition, dispatch logic, and entry-point setup is hard to navigate and creates merge conflicts. The split:

- Makes `cli_parsers.py` the single source of truth for the CLI surface (flags, help text, subcommands)
- Keeps `cli_dispatch.py` as a simple routing table with lazy imports
- Reduces `cli.py` to a thin orchestration shim

### Worktree subcommand routing

`cli_dispatch.py` routes `grove worktree <subcommand>` to handler modules:

| Subcommand | Handler |
|-----------|---------|
| `add` | `grove.worktree:add_worktree` |
| `remove` | `grove.worktree:remove_worktree` |
| `init-submodules` | `grove.worktree:init_submodules_command` |
| `checkout-branches` | `grove.worktree:checkout_branches` |
| `merge` | `grove.worktree_merge:run` |
| `switch` | `grove.worktree_switch:run` |
| `list` | `grove.worktree_list:run` |
| `step <sub>` | `grove.worktree_step:run` |
| `hook <sub>` | `grove.hooks:run` |

New top-level commands:

| Command | Handler |
|---------|---------|
| `grove shell init` | `grove.worktree_switch:generate_shell_wrapper` |
| `grove config import-wt` | `grove.config_import:run` |

### Parser-shape stability

A dedicated test (`test_cli_parser_shape.py`) captures the expected CLI surface (commands, subcommands, flags) and fails if the parser shape changes without a corresponding test update. This prevents accidental CLI drift.

---

## Module Ownership

| Module | Owns |
|--------|------|
| `cli.py` | Entry point, alias expansion, color setup |
| `cli_parsers.py` | Argparse tree (all commands, flags, help) |
| `cli_dispatch.py` | Routing to command handlers |
| `config.py` | All config dataclasses, TOML parsing, validation |
| `user_config.py` | Path resolution, TOML load/merge/dump utilities |
| `config_import.py` | `grove config import-wt` |
| `worktree.py` | `add`, `remove`, `init-submodules`, `checkout-branches` |
| `worktree_backend.py` | `maybe_delegate_*` functions, backend resolution, config synthesis |
| `worktree_switch.py` | Native switch + shell integration |
| `worktree_list.py` | Native list with metadata enrichment |
| `worktree_step.py` | `commit`, `squash`, `push`, `rebase`, `diff`, `copy-ignored`, `prune` |
| `worktree_common.py` | Shared helpers across worktree modules |
| `worktree_merge.py` | Existing merge (unchanged) |
| `hooks.py` | Hook execution with template expansion |
| `llm.py` | Dual-path LLM generation (shell command + Strands) |

### `worktree_common.py` shared helpers

Small utility functions used by multiple worktree modules:

- `resolve_default_branch(repo_root, rows=None)` — determine the default branch
- `resolve_target_branch(repo_root, explicit_target)` — resolve merge/push target
- `normalize_remainder_args(extra_args)` — clean up `--` separated args
- `emit_switch_target(args, target_path)` — write cd directive for shell integration

---

## Command Mapping

| grove command | wt equivalent | Native impl? | Notes |
|---|---|---|---|
| `grove worktree add` | — | Yes (existing, enhanced) | Add `--exclude-sync-group` flag; default: checkout ALL submodules on same branch |
| `grove worktree init-submodules` | — | Yes (new standalone) | Extracted from `add` for use as wt hook |
| `grove worktree switch` | `wt switch` | Yes (numbered list or fzf, create with -c, shell function for cd) | wt gets full interactive picker |
| `grove worktree list` | `wt list` | Moderate (branch, path, status, ahead/behind, commit msg, age) | wt adds CI, LLM summary, statusline |
| `grove worktree remove` | `wt remove` | Yes (integration check, branch cleanup) | wt adds background removal |
| `grove worktree merge` | — | Yes (existing, submodule-aware) | No wt equivalent |
| `grove worktree step commit` | `wt step commit` | Yes (stage + LLM or $EDITOR) | |
| `grove worktree step squash` | `wt step squash` | Yes (soft reset + LLM or $EDITOR) | |
| `grove worktree step push` | `wt step push` | Yes (`git push . HEAD:<target>`) | |
| `grove worktree step rebase` | `wt step rebase` | Yes (`git rebase <target>`) | |
| `grove worktree step diff` | `wt step diff` | Yes (merge-base diff) | |
| `grove worktree step copy-ignored` | `wt step copy-ignored` | Yes (gitignored file copy) | wt adds `.worktreeinclude` filtering |
| `grove worktree step for-each` | `wt step for-each` | **No** — UnsupportedWithoutWt | Complex template expansion |
| `grove worktree step promote` | `wt step promote` | **No** — UnsupportedWithoutWt | Worktree swapping |
| `grove worktree step prune` | `wt step prune` | Yes (integration check + remove) | |
| `grove worktree step relocate` | `wt step relocate` | **No** — UnsupportedWithoutWt | Path management |
| `grove worktree hook show` | `wt hook show` | Yes | |
| `grove worktree hook <type>` | `wt hook <type>` | Yes (run hooks with template vars) | wt adds approvals system |

### Capability categories

**Native-required** (grove owns these, never delegates):
- `worktree add`
- `worktree init-submodules`
- `worktree merge`

**Native + wt-delegable**:
- `worktree switch`, `list`, `remove`
- `worktree step` core (`push`, `rebase`, `diff`, `commit`, `squash`, `copy-ignored`, `prune`)
- `worktree hook` operations

**wt-only** (native unsupported):
- `worktree step for-each`, `promote`, `relocate`

### Error/warning strategy

| Situation | Behavior |
|---|---|
| Command not implemented natively | `UnsupportedWithoutWt` error with install instructions |
| Flag not applicable natively | Silently ignore (e.g., `--foreground` when already foreground) |
| Feature partially supported | Warning: "X feature requires wt backend for full support" + best effort |
| Config option not implementable | Warning at config load time: "X option requires wt backend" |

### Delegation ordering for wt-only commands

For commands like `step for-each` that have no native implementation: the delegation check must happen **before** the unsupported-native error. This ensures that when `backend = "auto"` and wt is available, the command succeeds via delegation rather than erroring prematurely.

```python
# Correct: delegate first, reject only in native path
def _run_for_each(repo_root, args):
    rc = maybe_delegate_step(repo_root, args)
    if rc is not None:
        return rc
    raise UnsupportedWithoutWt("grove worktree step for-each requires the worktrunk backend (wt)")
```

---

## Configuration

### File locations and precedence

Lowest → highest precedence:

| # | Level | Path | Notes |
|---|---|---|---|
| 1 | User | `~/.config/grove/config.toml` | Defaults |
| 2 | Project | `.config/grove.toml` | Repo-specific |
| 3 | Legacy | `.grove.toml` (repo root) | **Only** if `.config/grove.toml` absent; emits deprecation warning |
| 4 | Explicit | `$GROVE_CONFIG_PATH` | Highest override |

Environment variables:
- `GROVE_CONFIG_HOME` — overrides user config directory root (default: `~/.config/grove`)
- `GROVE_CONFIG_PATH` — explicit config file override (highest precedence)

### Config loading: raw dict merging

Config loading uses raw-dict deep merge before typed parsing. This means new config fields automatically participate in merging without explicit per-field code:

```python
def iter_grove_config_paths(repo_root) -> tuple[Path, ...]:
    """Return config file paths in precedence order (lowest first)."""

def load_merged_config(repo_root) -> GroveConfig:
    raw = {}
    for path in iter_grove_config_paths(repo_root):
        if path.exists():
            raw = merge_dicts(raw, load_toml_file(path))
    return parse_grove_config(raw)

def merge_dicts(base: dict, override: dict) -> dict:
    """Deep recursive merge. Override values win. Explicit false/empty must override."""
```

Critical: `merge_dicts` must not use truthy checks for precedence decisions. `false` must be able to override `true`, and empty strings/lists must override non-empty values.

### `user_config.py` utilities

| Function | Purpose |
|----------|---------|
| `get_user_config_dir()` | `$GROVE_CONFIG_HOME` or `~/.config/grove` |
| `get_user_config_path()` | `<dir>/config.toml` |
| `get_project_config_path(repo_root)` | `<root>/.config/grove.toml` |
| `get_legacy_config_path(repo_root)` | `<root>/.grove.toml` |
| `get_explicit_grove_config_path()` | `$GROVE_CONFIG_PATH` |
| `iter_grove_config_paths(repo_root)` | Ordered merge paths |
| `load_toml_file(path)` | Read TOML → raw dict |
| `merge_dicts(base, override)` | Deep recursive merge |
| `dump_toml(data)` | stdlib-only TOML serializer (for config synthesis and export) |

### Config dataclasses

Additions to `config.py`:

```python
VALID_BACKENDS = ("auto", "native", "wt")
VALID_LLM_PROVIDERS = ("anthropic", "ollama", "openai", "litellm")
VALID_STAGE_VALUES = ("all", "tracked", "none")
HOOK_TYPES = (
    "post-create", "post-start", "pre-merge", "post-merge",
    "pre-remove", "post-remove", "pre-switch", "post-switch", "pre-commit",
)

@dataclass
class LLMProviderEntry:
    provider: str   # validated against VALID_LLM_PROVIDERS
    model: str

@dataclass
class LLMConfig:
    providers: list[LLMProviderEntry]

@dataclass
class CommitGenerationConfig:
    command: str | None = None  # shell command: prompt on stdin, message on stdout

@dataclass
class CommitConfig:
    stage: str = "all"  # validated against VALID_STAGE_VALUES
    generation: CommitGenerationConfig = field(default_factory=CommitGenerationConfig)

@dataclass
class ListConfig:
    full: bool = False
    branches: bool = False
    remotes: bool = False
    url: bool = False

@dataclass
class LifecycleMergeConfig:
    """Controls for worktree merge lifecycle (distinct from worktree-merge test config)."""
    squash: bool = True
    commit: bool = True
    rebase: bool = True
    remove: bool = True
    verify: bool = True

@dataclass
class CIConfig:
    platform: str | None = None

@dataclass
class HooksConfig:
    """Hook commands by type. Each type maps name → command string."""
    hooks: dict[str, dict[str, str]]  # {hook_type: {name: command}}
```

Updated `WorktreeConfig`:
```python
@dataclass
class WorktreeConfig:
    copy_venv: bool = False
    backend: str = "auto"          # validated against VALID_BACKENDS
    worktree_path: str | None = None  # template with {{ branch | sanitize }}
    llm: LLMConfig | None = None
```

Updated `GroveConfig`:
```python
@dataclass
class GroveConfig:
    sync_groups: dict[str, SyncGroup]
    merge: MergeConfig                # existing worktree-merge test config
    worktree: WorktreeConfig
    cascade: CascadeConfig
    aliases: AliasConfig
    commit: CommitConfig
    list_config: ListConfig
    lifecycle_merge: LifecycleMergeConfig
    ci: CIConfig
    hooks: HooksConfig
```

### LLM config validation

`parse_llm_config(worktree_raw, *, context="worktree")` validates provider names against `VALID_LLM_PROVIDERS` at config load time, not at LLM call time. This gives users early feedback on typos.

### Hooks config: string shorthand

Support both table and string shorthand for hook configuration:

```toml
# String shorthand — wraps as {"default": "command"}
post-create = "npm install"

# Table form
[hooks.post-create]
deps = "npm ci"
env = "cp .env.example .env"
```

### User config structure (`~/.config/grove/config.toml`)

```toml
[worktree]
backend = "auto"
copy-venv = true
worktree-path = "{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}"

[worktree.llm]
providers = [
  { provider = "anthropic", model = "haiku" },
  { provider = "ollama", model = "qwen3:4b" },
]

[commit]
stage = "all"

[commit.generation]
command = "llm generate commit"  # optional shell command

[list]
full = false
branches = false

[merge]
squash = true
commit = true
rebase = true
remove = true
verify = true

[hooks.post-create]
submodules = "grove worktree init-submodules {{ worktree_path }}"
copy-ignored = "grove worktree step copy-ignored"

[hooks.pre-merge]
test = "npm test"
```

### Project config (`.config/grove.toml`)

Extends existing `.grove.toml` format with hooks and worktree sections:

```toml
[sync-groups]
# ... existing sync group config

[worktree-merge]
# ... existing merge config

[hooks.post-create]
deps = "npm ci"
env = "cp .env.example .env"

[hooks.pre-merge]
test = "cargo test"
```

### Config migration/import

`grove config import-wt` imports WorkTrunk user/project config into Grove canonical paths:

- `_translate_wt_to_grove(raw)` — schema mapping from wt format to grove format
- `_import_one(source, target, *, dry_run, force)` — load, merge or replace, write
- Conflict reporting when grove config already has values for imported fields

---

## Safety and Correctness Contracts

### Remove contract

If `git worktree remove` fails due to submodules, the manual deletion fallback must:

1. Detect dirty child repos (uncommitted changes in submodules)
2. Refuse deletion without `--force`
3. Print actionable error with affected paths
4. Only then allow removal

Manual deletion (`shutil.rmtree`) must never silently bypass force semantics. The current implementation has this bug — it must be fixed.

### Delegation contract

For wt-only commands:
1. Attempt delegation first when backend resolves to wt
2. Fail as unsupported only in the native path

No premature command rejection before backend resolution.

### Sync contract

Remote URL resolution for sync groups must search nested `.gitmodules` consistently with sync-group discovery behavior.

### Repo-root contract

Command behavior from nested directories must respect documented repository/root discovery expectations (including submodule boundary cases where applicable).

### `run_git` usage contract

`run_git(path, *args)` uses the `-C` flag to set the working directory. It does **not** accept a `cwd` parameter. All call sites must pass the path as the first argument, not as a keyword argument.

---

## LLM Integration

### Module: `src/grove/llm.py`

**Optional dependency**: `pip install grove[llm]` installs `strands-agents[ollama]`, `strands-agents-tools`, `claude-agent-sdk`.

### Fallback chain

```
1. wt backend available → delegate to wt (wt has its own LLM config)
2. [commit.generation].command configured → run shell command (stdin: prompt, stdout: message)
3. Strands installed + [worktree.llm].providers configured → try providers in order
4. $EDITOR → open editor with prompt template
5. Error with install instructions
```

### Dual-path implementation

```python
def generate_message(repo_root, prompt, llm_config) -> str | None:
    """Try generation command, then Strands providers. Returns None if all fail."""
    # Path 1: shell command
    command = _generation_command(repo_root)
    if command:
        result = _run_command(command, prompt)  # prompt on stdin, message on stdout
        if result:
            return result

    # Path 2: Strands providers
    if llm_config and llm_config.providers:
        result = _try_strands_providers(prompt, llm_config)
        if result:
            return result

    return None
```

Shell command path (~20 lines): runs `[commit.generation].command` with prompt on stdin, reads stdout.

Strands path (~100 lines): lazy import, provider registry, `_build_model()`, try each provider in order. Each provider failure is caught and continues to the next.

### Commit prompt (verbatim from wt)

```
Write a commit message for the staged changes below.

<format>
- Subject line under 50 chars
- For material changes, add a blank line then a body paragraph explaining the change
- Output only the commit message, no quotes or code blocks
</format>

<style>
- Imperative mood: "Add feature" not "Added feature"
- Match recent commit style (conventional commits if used)
- Describe the change, not the intent or benefit
</style>

<diffstat>{diffstat}</diffstat>
<diff>{diff}</diff>
<context>
Branch: {branch}
<recent_commits>{recent_commits}</recent_commits>
</context>
```

### Squash prompt (verbatim from wt)

```
Combine these commits into a single commit message.

<format>
- Subject line under 50 chars
- For material changes, add a blank line then a body paragraph explaining the change
- Output only the commit message, no quotes or code blocks
</format>

<style>
- Imperative mood: "Add feature" not "Added feature"
- Match the style of commits being squashed (conventional commits if used)
- Describe the change, not the intent or benefit
</style>

<commits branch="{branch}" target="{target}">{commits}</commits>
<diffstat>{diffstat}</diffstat>
<diff>{diff}</diff>
```

### Prompt helpers

- `build_commit_prompt(repo_root)` — structured prompt with diffstat/diff/recent commits
- `build_squash_prompt(repo_root, base, target)` — commits since base + diffstat
- `_truncate(text, max_chars)` — prevent oversized diffs from hitting LLM context limits

### Error type

`LLMUnavailableError` — raised when Strands import fails; caught in the fallback chain.

---

## Hooks System

### Module: `src/grove/hooks.py`

### Template variables (matching wt)

- `{{ branch }}`, `{{ worktree_path }}`, `{{ worktree_name }}`, `{{ repo }}`, `{{ repo_path }}`
- `{{ primary_worktree_path }}`, `{{ default_branch }}`, `{{ commit }}`, `{{ short_commit }}`
- `{{ target }}` (merge hooks), `{{ base }}` (creation hooks)

### Filters

`{{ branch | sanitize }}` → replace `/\` with `-`

### Hook types and behavior

| Hook | When | Blocking | Fail-fast | Native? |
|---|---|---|---|---|
| post-create | After worktree created | Yes | No | Yes |
| post-start | After worktree created | Background | No | Yes (foreground fallback) |
| pre-commit | Before commit | Yes | Yes | Yes |
| pre-merge | Before merge | Yes | Yes | Yes |
| post-merge | After merge | Yes | No | Yes |
| pre-remove | Before worktree removed | Yes | Yes | Yes |
| post-remove | After removal | Background | No | Yes (foreground fallback) |
| pre-switch | Before switch | Yes | Yes | Warn + skip (requires shell integration) |
| post-switch | After switch | Background | No | Warn + skip (requires shell integration) |

### Behavior rules

1. `--no-verify` disables relevant hooks
2. Fail-fast for pre-* hooks that gate destructive or mutating actions
3. Post hooks may continue after failures with warning unless explicitly gating
4. Background hooks in native mode run in foreground with a warning (true background requires shell integration)

### Unsupported native hooks

If `pre-switch` or `post-switch` are configured and native backend is active, emit a warning: "pre-switch/post-switch hooks require the worktrunk backend. Install wt for full hook support."

### Hook execution

- `run_configured_hooks(repo_root, hook_type, *, name, variables, yes)` — run all/named hooks with template expansion
- `_render_template(command, variables)` — `{{ var | filter }}` with `sanitize` filter
- `_iter_hook_commands(repo_root, hook_type)` — yields `(name, command)` from config
- `_confirm_hook_execution(hook_id, command)` — interactive TTY approval
- `_show_hooks(repo_root, hook_type, *, expanded)` — display with optional expansion

### Hook show and manual invocation

```bash
grove worktree hook show                    # list all hooks
grove worktree hook show post-create        # list hooks of a type
grove worktree hook show --expanded         # show with resolved template vars
grove worktree hook post-create             # run all post-create hooks
grove worktree hook post-create deps        # run only the "deps" hook
grove worktree hook post-create --var branch=main  # override template variable
```

---

## `grove worktree switch` — Native Implementation

### Flow

1. List worktrees via `git worktree list --porcelain`
2. If branch arg provided: find matching worktree, emit switch target (cd directive)
3. If `--create` flag: create worktree at configured path template, init submodules, run `post-create` hooks
4. If no args: print numbered list, prompt for selection (or pipe through `fzf` if available)
5. Branch shortcuts: `^` → default branch, `-` → previous worktree (stored in state file), `@` → current

### Shortcut handling

- `_resolve_shortcut(branch_arg)` — resolves `^`, `-`, `@`
- `_save_previous_worktree()` / `_get_previous_worktree()` — state persistence in per-worktree git dir
- `pr:N` / `mr:N` patterns → detect and fail-fast with `UnsupportedWithoutWt` in native mode

### Shell integration for cd

```bash
# grove shell init zsh
grove() {
    local directive_file=$(mktemp)
    command grove --directive-file "$directive_file" "$@"
    local exit_code=$?
    if [ -f "$directive_file" ]; then
        local dir=$(cat "$directive_file")
        [ -n "$dir" ] && cd "$dir"
        rm -f "$directive_file"
    fi
    return $exit_code
}
```

`generate_shell_wrapper(shell)` produces the wrapper for bash, zsh, and fish.

### Path computation

Worktree path is computed from the `worktree-path` config template with `{{ branch | sanitize }}` filter.

---

## `grove worktree list` — Native Implementation

### Data model

```python
@dataclass
class WorktreeInfo:
    path: Path
    branch: str | None
    commit: str
    dirty: bool
    ahead: int
    behind: int
    age: str           # human-readable relative time
    subject: str       # commit message subject line
    is_main: bool
    is_current: bool
    kind: str = "worktree"  # "worktree" | "branch" | "remote"
```

### Capabilities

Native mode provides: path, branch, commit, dirty status, ahead/behind, age, subject, is_main, is_current.

With `--full`: shows what native can compute (ahead/behind, diffstat). Warns that CI status and LLM summaries require wt backend.

With `--branches` / `--remotes`: adds rows with `kind="branch"` or `kind="remote"` for branches without worktrees.

### Output formats

- Table output (default): branch, commit, status, ahead/behind, age, subject
- JSON output (`--format json`): all `WorktreeInfo` fields; schema matches wt's for native-populated fields
- `--progressive`: accepted silently in native mode (always synchronous); passed to wt when delegating

---

## `grove worktree init-submodules` — New Standalone Command

Extracts `_init_submodules` + `_checkout_submodule_branches` from `worktree.py` as a CLI command:

```bash
# Initialize submodules in an existing worktree
grove worktree init-submodules /path/to/worktree

# With reference to main worktree for speed
grove worktree init-submodules /path/to/worktree --reference /path/to/main

# Exclude sync-group submodules from branch checkout
grove worktree init-submodules /path/to/worktree --exclude-sync-group

# Specify branch name (defaults to current branch of target worktree)
grove worktree init-submodules /path/to/worktree --branch feature-x
```

Default behavior: checkout ALL submodules (including sync-group) on the same branch.

This makes the worktrunk hook trivial:
```toml
[hooks.post-create]
submodules = "grove worktree init-submodules {{ worktree_path }} --reference {{ primary_worktree_path }}"
```

---

## CLI Surface: Flag Parity with wt

All wt flags must be carried over to grove's CLI. For each command:

### `grove worktree switch`
- `[BRANCH]` — branch name or shortcut (`^`, `-`, `@`, `pr:N`, `mr:N`)
- `--branches` — include branches without worktrees
- `--remotes` — include remote branches
- `-c, --create` — create new branch
- `-b, --base <BASE>` — base branch for creation
- `-x, --execute <EXECUTE>` — command to run after switch (template vars)
- `-y, --yes` — skip approval prompts
- `--clobber` — remove stale paths
- `--no-cd` — skip directory change
- `--no-verify` — skip hooks
- **Native notes**: `pr:N`/`mr:N` shortcuts → UnsupportedWithoutWt. `--branches`/`--remotes` with interactive picker → simpler numbered list natively, full picker with wt.

### `grove worktree list`
- `--format <FORMAT>` — table (default), json
- `--branches` — include branches without worktrees
- `--remotes` — include remote branches
- `--full` — show CI, diff analysis, LLM summaries
- `--progressive` — progressive rendering
- **Native notes**: `--full` shows what native can compute (ahead/behind, diffstat). CI status and LLM summaries → warning that these require wt backend. JSON output matches wt's schema for the fields native can populate.

### `grove worktree remove`
- `[BRANCHES]...` — branch names (default: current)
- `--no-delete-branch` — keep branch
- `-D, --force-delete` — delete unmerged branches
- `--foreground` — run in foreground
- `--no-verify` — skip hooks
- `-y, --yes` — skip approval
- `-f, --force` — force worktree removal
- **Native notes**: `--foreground` is always true natively (no background removal). Silently ignored.

### `grove worktree step commit`
- `-y, --yes` — skip approval
- `--no-verify` — skip hooks
- `--stage <STAGE>` — all, tracked, none
- `--show-prompt` — show LLM prompt without running

### `grove worktree step squash`
- `[TARGET]` — target branch
- `-y, --yes` — skip approval
- `--no-verify` — skip hooks
- `--stage <STAGE>` — all, tracked, none
- `--show-prompt` — show LLM prompt

### `grove worktree step push`
- `[TARGET]` — target branch

### `grove worktree step rebase`
- `[TARGET]` — target branch

### `grove worktree step diff`
- `[TARGET]` — target branch
- `[EXTRA_ARGS]...` — forwarded to git diff (after `--`)

### `grove worktree step copy-ignored`
- `--from <FROM>` — source worktree branch
- `--to <TO>` — destination worktree branch
- `--dry-run` — preview
- `--force` — overwrite existing
- **Native notes**: `.worktreeinclude` filtering → best effort natively, full support with wt.

### `grove worktree step for-each`
- `<ARGS>...` — command template
- **Native**: UnsupportedWithoutWt error.

### `grove worktree step promote`
- `[BRANCH]` — branch to promote
- **Native**: UnsupportedWithoutWt error.

### `grove worktree step prune`
- `--dry-run` — preview
- `-y, --yes` — skip approval
- `--min-age <MIN_AGE>` — skip young worktrees
- `--foreground` — run in foreground
- **Native notes**: `--min-age` → best effort (parse simple durations like `1h`, `2d`). `--foreground` → always true natively (silently ignored).

### `grove worktree step relocate`
- `[BRANCHES]...` — worktrees to relocate
- `--dry-run` — preview
- `--commit` — commit before relocating
- `--clobber` — backup blockers
- **Native**: UnsupportedWithoutWt error.

### `grove worktree hook show`
- `[HOOK_TYPE]` — filter by type
- `--expanded` — show expanded commands

### `grove worktree hook <type>`
- `[NAME]` — filter by command name
- `-y, --yes` — skip approval
- `--var <KEY=VALUE>` — override template variable

### `grove worktree merge` (existing, unchanged)
- Already has full flag set; no changes needed.

---

## Documentation

### README.md updates
- New "Worktree Lifecycle" section documenting switch, list, remove, step, hook commands
- "Configuration" section covering user config and project config
- "Using with Worktrunk" section explaining the optional backend
- "LLM Integration" section covering commit/squash message generation
- Update "Installation" with `pip install grove[llm]` for LLM features
- Update "Project Structure" with new files

### New docs files
- `docs/worktrunk-integration.md` — detailed guide on configuring grove with wt backend
- `docs/worktree-lifecycle.md` — full worktree lifecycle documentation (switch, list, remove, step, hooks)

### Updated docs
- `docs/submodule-workflow.md` — reference new lifecycle commands where relevant
- `docs/best-practices.md` — add lifecycle workflow recommendations

### Claude skills
- Update `grove-add.md` for new flags (`--exclude-sync-group`)
- New `grove-switch.md` — switch/create worktree workflow
- New `grove-list.md` — list worktrees
- New `grove-step.md` — step commands (commit, squash, diff, etc.)

---

## Quality Gates

These checks must pass at all times during development:

1. `ruff check src/ tests/` — no lint issues
2. `ruff format --check src/ tests/` — format clean
3. `pytest -q` — all tests pass
4. Parser-shape stability test — CLI surface matches expected shape
5. Complexity gate — no function exceeds 180 lines

### Test coverage requirements

For each new module:
- Happy path
- Error paths and edge cases
- Delegation behavior (wt available vs not)
- Flag interaction (e.g., `--force` + `--no-delete-branch`)
- Regression tests for all known safety bugs

### End-to-end test scenarios

1. Native mode: full lifecycle smoke test (add → switch → list → step diff → remove)
2. wt mode: delegation behavior and flag passthrough
3. Config migration/import
4. Destructive-path safety regressions (remove with dirty submodules, delegation ordering)

---

## Acceptance Criteria

1. **One coherent architecture** — no duplicate competing flows, no dead code from prior branches
2. **Full design coverage** — every command, flag, and config section is implemented or explicitly documented as wt-only with clear error message
3. **No safety regressions** — remove checks dirty state before manual fallback, step dispatch delegates before rejecting, `run_git` called correctly, config merge uses proper sentinels
4. **Config compatibility** — user/project/legacy/explicit-override precedence works; `grove config import-wt` available for migration
5. **LLM fallback chain functional** — shell command, Strands providers, `$EDITOR`, and error path all work independently
6. **CI green** — `pytest` + `ruff` pass across Python 3.11/3.12/3.13
