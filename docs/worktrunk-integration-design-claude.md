# Design: Worktrunk Integration for Grove (Revised)

This is a revised version of `worktrunk-integration-design.md`, updated to incorporate lessons learned from two prior implementation attempts (`grove.claude` and `grove.codex`). Changes from the original are called out with **[REVISED]** markers. Sections without markers are carried forward unchanged or with minor clarifications.

## Context

Grove manages the **submodule graph** (sync, push, merge across nested repos). [Worktrunk](https://github.com/max-sixty/worktrunk) (`wt`) manages the **worktree lifecycle** (create, switch, list, remove, hooks, LLM commits). The two tools complement each other, but users shouldn't need both installed. This design integrates worktrunk's worktree lifecycle features into grove as a first-class capability, with worktrunk as an optional backend that enhances native functionality when available.

**Goals:**
- Grove handles the full worktree lifecycle (switch, list, remove, step commands, hooks) with or without wt
- When wt is installed, grove delegates to it for richer UX (interactive picker, CI status, background operations)
- When wt is NOT installed, grove provides faithful native implementations of core features
- Unsupported native features fail fast with clear error messages
- LLM-powered commit/squash messages via shell command or Strands (optional `grove[llm]` extra)
- Configuration mirrors wt's file layout (`~/.config/grove/config.toml`, `.config/grove.toml`)

---

## Architecture: Command-Local Delegation **[REVISED]**

The original design specified an abstract `WorktreeBackend` class with `NativeBackend` and `WtBackend` implementations. Both implementations revealed this pattern creates unnecessary indirection: `NativeBackend` must reconstruct args objects to call functions that already accept argparse namespaces, and the CLI dispatcher must check `isinstance` before routing. The revised architecture uses **command-local delegation functions** instead.

### `src/grove/worktree_backend.py`

Standalone delegation functions that each return `int | None`:

```python
def maybe_delegate_switch(repo_root: Path, args) -> int | None: ...
def maybe_delegate_list(repo_root: Path, args) -> int | None: ...
def maybe_delegate_remove(repo_root: Path, args) -> int | None: ...
def maybe_delegate_step(repo_root: Path, args) -> int | None: ...
def maybe_delegate_hook(repo_root: Path, args) -> int | None: ...
```

Each function:
1. Calls `_resolve_backend(repo_root)` → `"wt"` or `"native"`
2. If `"native"`: returns `None` — the calling module proceeds with its own native logic
3. If `"wt"`: enters `_delegation_env(repo_root)` context, builds a `wt` command, runs it, returns the exit code

Each command module owns its full lifecycle:

```python
# worktree_switch.py
def run(args):
    repo_root = find_repo_root()
    rc = maybe_delegate_switch(repo_root, args)
    if rc is not None:
        return rc
    return switch_native(args, repo_root)
```

This eliminates double dispatch — no abstract class, no `isinstance` checks, no args reconstruction.

### Backend contract documentation (Protocol) **[REVISED]**

A `Protocol` class documents the delegation interface without enforcing it at runtime:

```python
class BackendContract(Protocol):
    """Documents the full set of delegation-capable operations.
    Not instantiated — exists for documentation and type-checking only."""
    def switch(self, repo_root: Path, args) -> int | None: ...
    def list_worktrees(self, repo_root: Path, args) -> int | None: ...
    def remove(self, repo_root: Path, args) -> int | None: ...
    def step(self, repo_root: Path, args) -> int | None: ...
    def hook(self, repo_root: Path, args) -> int | None: ...
```

### Backend resolution

```python
def _resolve_backend(repo_root: Path) -> str:
    """Return "wt" or "native" based on config and wt availability."""
    config = _load_config_tolerant(repo_root)  # returns None if config missing
    backend_setting = "auto"
    if config and config.worktree and config.worktree.backend:
        backend_setting = config.worktree.backend

    if backend_setting == "native":
        return "native"
    if backend_setting == "wt":
        if not shutil.which("wt"):
            raise RuntimeError("backend = 'wt' but wt is not installed")
        return "wt"
    # "auto": use wt if on PATH
    return "wt" if shutil.which("wt") else "native"
```

**Selection** via config:
```toml
[worktree]
backend = "auto"  # "auto" (default) | "native" | "wt"
```

### Config synthesis for wt delegation

When delegating to `wt`, grove synthesizes a temporary config file:

```python
@contextmanager
def _delegation_env(repo_root: Path):
    """Context manager: synthesize wt config, set WORKTRUNK_CONFIG_PATH, cleanup."""
    raw = _load_raw_config(repo_root)
    wt_config = _synthesize_wt_config(raw)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False)
    try:
        tmp.write(dump_toml(wt_config))
        tmp.close()
        env = {**os.environ, "WORKTRUNK_CONFIG_PATH": tmp.name}
        yield env
    finally:
        os.unlink(tmp.name)
```

`_synthesize_wt_config(raw)` maps grove config sections to wt equivalents:
- `[worktree]` → `worktree-path`, `copy-venv`
- `[worktree.llm]` → provider chain (if wt supports it)
- `[hooks.*]` → hook definitions
- `[list]` → default list flags
- `[commit]` → stage preference, generation command
- `[merge]` → lifecycle merge defaults (squash, commit, rebase, remove, verify)
- `[ci]` → platform

### Conditional flag building helper

```python
def _append_flag(cmd: list[str], flag: str, value) -> None:
    """Append --flag or --flag=value to cmd list if value is truthy."""
    if value is True:
        cmd.append(flag)
    elif value:
        cmd.extend([flag, str(value)])
```

---

## CLI Structure **[NEW SECTION]**

The original design placed all parsers and dispatch in `cli.py`. Both implementations showed this creates a file too large to maintain (709+ lines for parser alone, 1400+ lines total with dispatch). The revised design splits the CLI into three files.

### `src/grove/cli.py` (~100 lines)

Thin entry point:
- `main(argv=None)` — alias expansion, `--no-color` handling, `GROVE_CONFIG_PATH` activation, calls `build_parser()` and `dispatch_command()`
- `_expand_aliases(argv)` — looks up first token in `config.aliases.mapping`
- `_activate_worktree_config_override(args)` — sets `GROVE_CONFIG_PATH` from `--config` flag

### `src/grove/cli_parsers.py` (~1000 lines)

`build_parser() -> argparse.ArgumentParser` — constructs the full argparse tree. All subcommand parsers, flags, and help text live here. No dispatch logic.

### `src/grove/cli_dispatch.py` (~100 lines)

`dispatch_command(args, parser) -> int` — maps `args.command` to the appropriate module's `run()` function via lazy imports. Worktree subcommands route to `grove.worktree:run(args)`, which sub-dispatches to handler modules.

### `src/grove/worktree.py` dispatch

`run(args) -> int` at the bottom of `worktree.py` routes worktree subcommands:

```python
def run(args):
    if args.worktree_command == "switch":
        from grove.worktree_switch import run
        return run(args)
    if args.worktree_command == "list":
        from grove.worktree_list import run
        return run(args)
    if args.worktree_command == "step":
        from grove.worktree_step import run
        return run(args)
    if args.worktree_command == "hook":
        from grove.hooks import run
        return run(args)
    # ... add, remove, merge, init-submodules, checkout-branches
```

### Parser shape stability test **[NEW]**

`tests/test_cli_parser_shape.py` asserts the complete set of top-level commands and worktree subcommands. This prevents accidental CLI surface drift during refactoring.

---

## Shared Helpers **[NEW SECTION]**

### `src/grove/worktree_common.py` (~80 lines)

Functions used by multiple worktree modules:

- `resolve_default_branch(repo_root, rows=None)` — checks `origin/HEAD`, main worktree row, then current branch. Returns `"main"` or `"master"` (or whatever the repo uses).
- `resolve_target_branch(repo_root, explicit_target)` — returns `explicit_target` if given, otherwise calls `resolve_default_branch()`.
- `normalize_remainder_args(extra_args)` — strips a leading `--` from argparse remainder args.
- `emit_switch_target(args, target_path)` — prints the target path and optionally writes it to `--directive-file` for shell cd integration.

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
| `grove worktree step copy-ignored` | `wt step copy-ignored` | Yes (gitignored file copy + `.worktreeinclude` filtering) | |
| `grove worktree step for-each` | `wt step for-each` | **No** — UnsupportedWithoutWt | Complex template expansion |
| `grove worktree step promote` | `wt step promote` | **No** — UnsupportedWithoutWt | Worktree swapping |
| `grove worktree step prune` | `wt step prune` | Yes (integration check + remove) | |
| `grove worktree step relocate` | `wt step relocate` | **No** — UnsupportedWithoutWt | Path management |
| `grove worktree hook show` | `wt hook show` | Yes | |
| `grove worktree hook <type>` | `wt hook <type>` | Yes (run hooks with template vars + interactive approval) | wt adds approvals system |
| `grove shell init` | — | Yes (shell wrapper generation) | Generates bash/zsh/fish wrapper for cd integration |
| `grove config import-wt` | — | Yes (migration tool) | Imports WorkTrunk config into Grove format |

### Delegation ordering for wt-only commands **[REVISED]**

Commands that are wt-only (`for-each`, `promote`, `relocate`) must **delegate before rejecting**:

```python
# In worktree_step.py:run()
rc = maybe_delegate_step(repo_root, args)
if rc is not None:
    return rc  # wt handled it

# Only NOW check if native supports it
if args.step_command in ("for-each", "promote", "relocate"):
    print(f"grove worktree step {args.step_command} requires the worktrunk backend (wt)")
    return 1
```

The original design specified this intent but both implementations initially got the ordering wrong — checking unsupported before delegating, which blocked wt-only commands even when wt was active.

**Dry-run for wt-delegated commands**: When grove has `--dry-run` but wt doesn't, report "will run: wt \<command\>" and stop.

---

## Configuration

### File locations (mirrors wt)

| Level | Path | Precedence |
|---|---|---|
| User | `~/.config/grove/config.toml` | Lowest (defaults) |
| Project | `.config/grove.toml` | Higher (repo-specific) |
| Legacy | `.grove.toml` (repo root) | Merged into project; deprecation warning |
| Explicit | `$GROVE_CONFIG_PATH` | Highest (overrides all) |

**[REVISED]** Added `$GROVE_CONFIG_PATH` as an explicit override (highest precedence). Also added `$GROVE_CONFIG_HOME` to override the user config directory (defaults to `~/.config/grove`).

### Config merging strategy **[REVISED]**

The original design did not specify the merging algorithm. Both implementations tried field-by-field merging at the dataclass level, which requires each new field to be explicitly handled and creates subtle bugs with boolean values (e.g., project `copy-venv = false` cannot override user `true` when using a truthy check).

**Use raw dict merging before parsing:**

```python
def _load_raw_config(repo_root: Path) -> dict:
    """Load and merge all config files as raw dicts."""
    merged = {}
    for path in iter_grove_config_paths(repo_root):
        if path.exists():
            raw = load_toml_file(path)
            merged = merge_dicts(merged, raw)
    return merged

def merge_dicts(base: dict, override: dict) -> dict:
    """Deep recursive merge. Override values win. New keys are added."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result
```

After merging, the single merged dict is parsed into `GroveConfig` via dedicated `_parse_*` functions. This means:
- New config fields automatically participate in merging
- `false` correctly overrides `true` (no truthy checks — the value is just the value)
- No sentinel/default confusion at merge time

### Config serialization for wt synthesis

Use a proper TOML serializer, not `json.dumps()`:

```python
def dump_toml(data: dict) -> str:
    """Serialize a dict to TOML format. Stdlib only — no external deps."""
    # Handles strings, ints, floats, bools, lists, nested tables
    ...
```

### User config structure (`~/.config/grove/config.toml`)

```toml
[worktree]
backend = "auto"
copy-venv = true
worktree-path = "{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}"

[worktree.llm]
# Fallback chain — try in order, fall to $EDITOR if all fail
providers = [
  { provider = "anthropic", model = "haiku" },
  { provider = "ollama", model = "qwen3:4b" },
]

[commit]
stage = "all"          # "all" | "tracked" | "none"

[commit.generation]
command = "llm-cli generate"   # shell command: prompt on stdin, message on stdout

[list]
full = false
branches = false
remotes = false

[merge]
squash = true
commit = true
rebase = true
remove = true
verify = true

[ci]
platform = "github"   # used by wt for CI status in list --full

# Hook string shorthand
[hooks]
post-create = "npm install"

# Hook table form (multiple named commands)
[hooks.pre-merge]
test = "npm test"
lint = "npm run lint"
```

**[REVISED]** Added sections from the design that both implementations identified as needed:
- `[commit.generation]` — shell command for LLM (complements Strands provider chain)
- `[list]` — defaults for `grove worktree list` flags
- `[merge]` — lifecycle merge defaults (squash, commit, rebase, remove, verify)
- `[ci]` — CI platform for wt integration
- Hook string shorthand — `post-create = "npm install"` is equivalent to `[hooks.post-create]\ndefault = "npm install"`

### Project config (`.config/grove.toml`)

Extends existing `.grove.toml` format with hooks and worktree sections:

```toml
[sync-groups]
# ... existing sync group config

[worktree-merge]
# ... existing merge config

# String shorthand
[hooks]
post-create = "npm ci"

# Table form
[hooks.pre-merge]
test = "cargo test"
```

### Config dataclasses **[REVISED]**

```python
# Provider entry for LLM fallback chain
@dataclass
class LLMProviderEntry:
    provider: str   # "anthropic" | "ollama" | "openai" | "litellm"
    model: str

@dataclass
class LLMConfig:
    providers: list[LLMProviderEntry] = field(default_factory=list)

@dataclass
class CommitGenerationConfig:
    command: str | None = None  # shell command: prompt on stdin, message on stdout

@dataclass
class CommitConfig:
    stage: str = "all"          # "all" | "tracked" | "none"
    generation: CommitGenerationConfig = field(default_factory=CommitGenerationConfig)

@dataclass
class ListConfig:
    full: bool = False
    branches: bool = False
    remotes: bool = False
    url: bool = False

@dataclass
class LifecycleMergeConfig:
    squash: bool = True
    commit: bool = True
    rebase: bool = True
    remove: bool = True
    verify: bool = True

@dataclass
class CIConfig:
    platform: str | None = None

@dataclass
class HookSection:
    commands: dict[str, str] = field(default_factory=dict)
    # String shorthand "npm install" stored as {"default": "npm install"}

@dataclass
class WorktreeConfig:
    copy_venv: bool = False
    backend: str = "auto"
    worktree_path: str | None = None
    llm: LLMConfig = field(default_factory=LLMConfig)

@dataclass
class GroveConfig:
    sync_groups: dict[str, SyncGroup]
    merge: MergeConfig
    worktree: WorktreeConfig
    list: ListConfig
    commit: CommitConfig
    lifecycle_merge: LifecycleMergeConfig
    ci: CIConfig
    hooks: dict[str, HookSection]
    cascade: CascadeConfig
    aliases: AliasConfig
```

### Validation constants

```python
VALID_BACKENDS = ("auto", "native", "wt")
VALID_LLM_PROVIDERS = ("anthropic", "ollama", "openai", "litellm")
VALID_STAGE_VALUES = ("all", "tracked", "none")
HOOK_TYPES = (
    "post-create", "post-start", "pre-merge", "post-merge",
    "pre-remove", "post-remove", "pre-switch", "post-switch",
    "pre-commit",
)
```

**[REVISED]** `parse_llm_config()` validates provider names against `VALID_LLM_PROVIDERS` at config load time, not at LLM call time. This catches misconfiguration early.

### Config path helpers (`user_config.py`) **[REVISED]**

```python
def get_user_config_dir() -> Path:
    """$GROVE_CONFIG_HOME or ~/.config/grove"""

def get_user_config_path() -> Path:
    """<user_config_dir>/config.toml"""

def get_project_config_path(repo_root: Path) -> Path:
    """<repo_root>/.config/grove.toml"""

def get_legacy_config_path(repo_root: Path) -> Path:
    """<repo_root>/.grove.toml"""

def get_explicit_grove_config_path() -> Path | None:
    """$GROVE_CONFIG_PATH or None"""

def iter_grove_config_paths(repo_root: Path) -> tuple[Path, ...]:
    """Ordered paths for merging: user < legacy < project < explicit override."""

def get_wt_user_config_path() -> Path:
    """$WORKTRUNK_CONFIG_PATH or ~/.config/worktrunk/config.toml"""

def get_wt_project_config_path(repo_root: Path) -> Path:
    """<repo_root>/.config/wt.toml"""
```

### Config import/migration tool **[NEW]**

`grove config import-wt` imports WorkTrunk config into Grove canonical config paths:

```bash
grove config import-wt                    # import user + project configs
grove config import-wt --dry-run          # preview without writing
grove config import-wt --force            # overwrite existing grove config
grove config import-wt --user-only        # import only user config
grove config import-wt --project-only     # import only project config
```

Implementation in `src/grove/config_import.py`:
- `_translate_wt_to_grove(raw)` — maps wt config keys to grove equivalents
- `_import_one(source, target, *, dry_run, force)` — load, merge or replace, render TOML, write

---

## LLM Integration **[REVISED]**

### Module: `src/grove/llm.py`

**Optional dependency**: `pip install grove[llm]` installs `strands-agents[ollama]`, `strands-agents-tools`, `claude-agent-sdk`.

**[REVISED]** Dual-path generation: shell command for simplicity, Strands provider chain for resilience. The original design specified only Strands. The revised design adds a shell command option that requires zero external dependencies.

**Fallback chain for commit/squash messages:**
1. wt backend available → delegate to wt (it has its own LLM config)
2. `[commit.generation].command` configured → run shell command (prompt on stdin, message on stdout)
3. Strands installed + `[worktree.llm].providers` configured → try providers in order
4. `$EDITOR` → open editor like `git commit` without `-m`
5. Error with install instructions

### Shell command path

```python
def _run_generation_command(command: str, prompt: str) -> str | None:
    """Run shell command with prompt on stdin, return stdout or None."""
    result = subprocess.run(
        command, shell=True, input=prompt, capture_output=True, text=True
    )
    output = result.stdout.strip()
    return output if result.returncode == 0 and output else None
```

Config:
```toml
[commit.generation]
command = "llm-cli generate"
```

Any tool that reads stdin and writes stdout works — `llm`, `ollama run`, a custom script. This is the unix-composable option.

### Strands provider chain

```python
def _try_strands_providers(prompt: str, llm_config: LLMConfig) -> str | None:
    """Try each configured provider in order. Returns None if all fail."""
    for entry in llm_config.providers:
        try:
            _ensure_strands()  # lazy import
            model = _build_model(entry.provider, entry.model)
            agent = Agent(model=model, tools=[])
            response = agent(prompt)
            return getattr(response, "text", str(response)).strip()
        except Exception:
            continue
    return None
```

Provider validation: `parse_llm_config()` checks provider names against `VALID_LLM_PROVIDERS` = `("anthropic", "ollama", "openai", "litellm")` at config load time.

### Prompt templates

**Commit prompt** (verbatim from wt):
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

**Squash prompt** (verbatim from wt):
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

### Prompt building helpers

```python
def build_commit_prompt(repo_root: Path) -> str:
    """Gather branch, diffstat, diff (truncated), recent commits. Fill template."""

def build_squash_prompt(repo_root: Path, base: str, target: str) -> str:
    """Gather commits since base, diffstat. Fill template."""

def _truncate(text: str, max_chars: int = 4000) -> str:
    """Truncate diff at max_chars with '... (truncated)' marker."""
```

### Interaction flow for LLM messages

When LLM returns a message:
1. Display the generated message
2. Prompt: `Use this message? [Y/n/e] ` — Yes (default), No (abort), Edit (open in `$EDITOR`)
3. If `--yes` flag: skip prompt, use message directly
4. If `--show-prompt`: print the prompt template and exit without generating

---

## Hooks System

### Module: `src/grove/hooks.py`

**Template variables** (matching wt):
- `{{ branch }}`, `{{ worktree_path }}`, `{{ worktree_name }}`, `{{ repo }}`, `{{ repo_path }}`
- `{{ primary_worktree_path }}`, `{{ default_branch }}`, `{{ commit }}`, `{{ short_commit }}`
- `{{ target }}` (merge hooks), `{{ base }}` (creation hooks)

**Filters**: `{{ branch | sanitize }}` → replace `/\` with `-`

**Hook types and behavior:**

| Hook | When | Blocking | Fail-fast | Native? |
|---|---|---|---|---|
| post-create | After worktree created | Yes | No | Yes |
| post-start | After worktree created | Background | No | Yes (foreground fallback) |
| pre-commit | Before commit in step commit | Yes | Yes | Yes |
| pre-merge | Before merge | Yes | Yes | Yes |
| post-merge | After merge | Yes | No | Yes |
| pre-remove | Before worktree removed | Yes | Yes | Yes |
| post-remove | After removal | Background | No | Yes (foreground fallback) |
| pre-switch | Before switch | Yes | Yes | Warn + skip (requires shell integration) |
| post-switch | After switch | Background | No | Warn + skip (requires shell integration) |

**[REVISED]** Added `pre-commit` hook type (9 types total, up from 8). This runs before commit in `step commit` and aborts the commit if it fails. Useful for linting, formatting, etc.

Background hooks in native mode run in foreground with a warning (true background requires shell integration).

**Unsupported native hooks**: If `pre-switch` or `post-switch` are configured and native backend is active, emit a warning: "pre-switch/post-switch hooks require the worktrunk backend. Install wt for full hook support."

### Hook config forms **[REVISED]**

Both table and string shorthand are supported:

```toml
# String shorthand — wraps internally as {"default": "command"}
[hooks]
post-create = "npm install"

# Table form — multiple named commands
[hooks.post-create]
deps = "npm ci"
env = "cp .env.example .env"
```

The string shorthand is syntactic sugar. Internally, `post-create = "npm install"` is stored as `HookSection(commands={"default": "npm install"})`.

### Hook execution

```python
def run_configured_hooks(
    repo_root: Path,
    hook_type: str,
    *,
    name: str | None = None,
    variables: dict[str, str] | None = None,
    yes: bool = False,
) -> int:
    """Run all (or named) hooks for a type with template expansion."""
```

For each hook command:
1. Expand template variables (`{{ branch }}` → actual branch name)
2. If interactive (not `--yes`): show expanded command, prompt for approval
3. Run via `subprocess.run(command, shell=True)`
4. For fail-fast hooks: abort on first failure. For non-fail-fast: continue and report.

### Hook show

```bash
grove worktree hook show                  # list all configured hooks
grove worktree hook show post-create      # list hooks for a type
grove worktree hook show --expanded       # show with template variables resolved
```

### `--no-verify` support

When `--no-verify` is passed to switch, remove, or step commit, all hooks for that operation are skipped. This matches git's convention.

---

## `grove worktree switch` — Native Implementation

**Native mode:**
1. List worktrees via `git worktree list --porcelain`
2. If branch arg provided: find matching worktree, emit switch target (print path or cd via shell function)
3. If `-c` flag: create worktree at configured path template, init submodules, run post-create hooks
4. If no args: print numbered list, prompt for selection
5. Branch shortcuts: `^` → default branch, `-` → previous worktree (stored in state file), `@` → current worktree
6. **[REVISED]** `pr:N`/`mr:N` shortcuts → detect and fail fast: "PR/MR shortcuts require the worktrunk backend"
7. `--branches` / `--remotes` → include local branches without worktrees and/or remote branches in the interactive list
8. `--clobber` → if the target path already exists but is a stale worktree, remove it before creation

### Switch state tracking **[REVISED]**

State file at `~/.local/state/grove/<repo-hash>` stores:
- `previous_branch` — for the `-` shortcut
- `previous_path` — for cd to previous worktree

```python
def _save_previous_worktree(repo_root: Path, branch: str, path: Path) -> None:
    """Persist current worktree as 'previous' before switching."""

def _get_previous_worktree(repo_root: Path) -> tuple[str, Path] | None:
    """Load the previous worktree for the `-` shortcut."""
```

### Path computation

```python
def _render_worktree_path(repo_root: Path, branch: str) -> Path:
    """Render worktree-path template from config.
    Default: ../repo-name.branch-name (sibling directory).
    Template vars: {{ repo_path }}, {{ repo }}, {{ branch | sanitize }}
    """
```

### Shell integration for native `cd`

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

`grove shell init {bash|zsh|fish}` outputs the appropriate shell wrapper. Users add `eval "$(grove shell init zsh)"` to their shell rc file.

---

## `grove worktree remove` — Safety Contract **[REVISED]**

The original design specified `git worktree remove` with branch cleanup. Both implementations revealed a safety issue: when `git worktree remove` fails due to submodules, the fallback to `shutil.rmtree()` can delete uncommitted work.

### Safe remove fallback

```python
def _remove_single_target(repo_root, args, *, target, worktrees, default_branch):
    # ... resolve target, run pre-remove hooks ...

    result = run_git(repo_root, "worktree", "remove", worktree_path, check=False)
    if result.returncode != 0:
        if "submodules" in result.stderr:
            # Check for dirty state before manual removal
            if not args.force and _has_dirty_children(worktree_path):
                print(Colors.red("Worktree has uncommitted changes in submodules."))
                print("Use --force to remove anyway.")
                return 1
            print(f"  {Colors.yellow('Worktree contains submodules')}; removing manually...")
            shutil.rmtree(worktree_path)
        else:
            print(result.stderr.rstrip())
            return 1

    # ... prune, delete branch, run post-remove hooks ...
```

`_has_dirty_children(path)` walks submodule directories checking `git status --porcelain`. Returns `True` if any submodule has uncommitted changes.

---

## `grove worktree init-submodules` — New Standalone Command

Extracts `_init_submodules` + `_checkout_submodule_branches` from `worktree.py` as a CLI command:

```bash
grove worktree init-submodules /path/to/worktree
grove worktree init-submodules /path/to/worktree --reference /path/to/main
grove worktree init-submodules /path/to/worktree --exclude-sync-group
grove worktree init-submodules /path/to/worktree --branch feature-x
```

Default behavior: checkout ALL submodules (including sync-group) on the same branch.

This makes the worktrunk hook trivial:
```toml
[hooks.post-create]
submodules = "grove worktree init-submodules {{ worktree_path }} --reference {{ primary_worktree_path }}"
```

---

## `grove worktree list` — Native Implementation

### Row model **[REVISED]**

Both implementations used different data models (dataclass vs raw dict). The revised design uses a typed dataclass for native list output but supports additional row kinds for branches and remotes:

```python
@dataclass
class WorktreeRow:
    kind: str           # "worktree" | "branch" | "remote"
    path: Path | None   # None for branch/remote rows
    branch: str | None
    head: str | None
    head_short: str | None
    is_main: bool
    is_current: bool
    is_bare: bool
    is_detached: bool
    exists: bool        # False for branch/remote rows (no worktree)
    dirty: bool | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    timestamp: int | None
    age: str | None
    subject: str | None
```

### Discovery and enrichment

```python
def discover_worktrees(repo_root: Path) -> list[WorktreeRow]:
    """Parse `git worktree list --porcelain`, enrich each row with metadata."""

def collect_worktree_rows(
    repo_root: Path, *, include_branches: bool = False, include_remotes: bool = False
) -> list[WorktreeRow]:
    """Add local branch and remote branch rows alongside worktree rows."""
```

### Output formats

- **Table** (default): branch, commit, status, ahead/behind, age, subject
- **JSON**: all fields, matching wt's schema for the fields native can populate
- `--full`: shows what native can compute (ahead/behind, dirty, age, subject). Warns that CI status and LLM summaries require wt backend.
- `--progressive`: accepted silently (always synchronous natively), passed to wt when delegating

---

## `grove worktree step copy-ignored` — `.worktreeinclude` Support **[REVISED]**

When a `.worktreeinclude` file exists in the worktree root, only copy gitignored files matching its patterns:

```
# .worktreeinclude — glob patterns for ignored files to copy between worktrees
.env
.env.local
*.sqlite
data/fixtures/**
```

```python
def _filter_ignored_files_by_worktreeinclude(
    worktree_path: Path, ignored_files: list[Path]
) -> list[Path]:
    """Filter ignored files by .worktreeinclude patterns if present."""
```

If `.worktreeinclude` doesn't exist, all gitignored files are copied (current behavior).

---

## `grove worktree step prune` — Duration Parsing

```python
def _parse_duration(duration: str) -> int:
    """Parse duration string to seconds. Supports: 30s, 5m, 2h, 1d, 1w."""
```

`--min-age 2d` means "only prune worktrees older than 2 days."

---

## `grove shell init` — Shell Wrapper Generation **[NEW SECTION]**

```bash
grove shell init bash    # output bash wrapper
grove shell init zsh     # output zsh wrapper
grove shell init fish    # output fish wrapper
```

Each wrapper:
1. Creates a temporary directive file
2. Runs `command grove --directive-file "$directive_file" "$@"`
3. If the directive file contains a path, `cd` to it
4. Cleans up the temp file

Users add to their shell rc:
```bash
eval "$(grove shell init zsh)"
```

---

## `run_git` Usage Contract **[NEW SECTION]**

Both implementations had bugs where `run_git` was called with `cwd=` parameter, but `run_git` uses git's `-C` flag:

```python
def run_git(path: Path, *args, check: bool = True, capture: bool = True):
    """Run git -C <path> <args>."""
```

**Never pass `cwd=` to `run_git`.** Always pass the repository path as the first positional argument. The function handles directory context via `git -C`.

---

## Files to Create/Modify

### New files

| File | Purpose | Est. lines |
|---|---|---|
| `src/grove/cli_parsers.py` | Parser construction (extracted from cli.py) | ~1000 |
| `src/grove/cli_dispatch.py` | Command dispatch routing | ~100 |
| `src/grove/worktree_backend.py` | Delegation functions + config synthesis | ~300 |
| `src/grove/worktree_step.py` | Step subcommands | ~720 |
| `src/grove/worktree_list.py` | Native worktree list with metadata | ~470 |
| `src/grove/worktree_switch.py` | Native switch + shell integration | ~480 |
| `src/grove/worktree_common.py` | Shared helpers (resolve_default_branch, etc.) | ~80 |
| `src/grove/hooks.py` | Hook execution with template expansion | ~185 |
| `src/grove/llm.py` | Dual-path LLM (shell command + Strands) | ~220 |
| `src/grove/user_config.py` | Config path resolution, TOML utilities | ~160 |
| `src/grove/config_import.py` | WorkTrunk config import/migration | ~100 |
| `src/grove/shell.py` | Shell wrapper generation | ~85 |
| `tests/test_worktree_backend.py` | Delegation function tests | ~420 |
| `tests/test_worktree_step.py` | Step command tests | ~650 |
| `tests/test_worktree_list.py` | List command tests | ~150 |
| `tests/test_worktree_switch.py` | Switch command tests | ~550 |
| `tests/test_hooks.py` | Hook execution tests | ~155 |
| `tests/test_llm.py` | LLM integration tests (mocked) | ~70 |
| `tests/test_user_config.py` | Config path/merge/dump tests | ~50 |
| `tests/test_config_import.py` | Config migration tests | ~75 |
| `tests/test_shell.py` | Shell wrapper tests | ~40 |
| `tests/test_cli_parser_shape.py` | Parser stability test | ~55 |

### Modified files

| File | Changes |
|---|---|
| `src/grove/cli.py` | Slim down to ~100 lines: main(), aliases, color |
| `src/grove/worktree.py` | Add `run()` dispatch, `init_submodules_command()`, dirty-child check on remove, `--exclude-sync-group` |
| `src/grove/config.py` | Add new dataclasses, raw dict merging, `parse_llm_config()`, hook string shorthand |
| `src/grove/completion.py` | New subcommand completions |
| `pyproject.toml` | Add `[project.optional-dependencies] llm = [...]` |
| `src/grove/claude_skills/grove-add.md` | Update for new flags |
| `README.md` | Document new commands, config, wt integration |

### New skills

| File | Purpose |
|---|---|
| `src/grove/claude_skills/grove-switch.md` | Switch/create worktree |
| `src/grove/claude_skills/grove-list.md` | List worktrees |
| `src/grove/claude_skills/grove-step.md` | Step commands |

---

## Flag Parity with wt

All wt flags must be carried over to grove's CLI. For each command, this means:

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
- **Native notes**: `pr:N`/`mr:N` shortcuts → fail fast with clear error. `--branches`/`--remotes` → simpler numbered list natively, full picker with wt.

### `grove worktree list`
- `--format <FORMAT>` — table (default), json
- `--branches` — include branches without worktrees
- `--remotes` — include remote branches
- `--full` — show CI, diff analysis, LLM summaries
- `--progressive` — progressive rendering
- **Native notes**: `--full` shows what native can compute (ahead/behind, diffstat). CI status and LLM summaries → warning that these require wt backend. `--progressive` → ignored silently (always synchronous). JSON output matches wt's schema for the fields native can populate.

### `grove worktree remove`
- `[BRANCHES]...` — branch names (default: current)
- `--no-delete-branch` — keep branch
- `-D, --force-delete` — delete unmerged branches
- `--foreground` — run in foreground
- `--no-verify` — skip hooks
- `-y, --yes` — skip approval
- `-f, --force` — force worktree removal
- **Native notes**: `--foreground` is always true natively (no background removal). Just ignore the flag silently.

### `grove worktree step commit`
- `-y, --yes` — skip approval
- `--no-verify` — skip pre-commit hooks
- `--stage <STAGE>` — all, tracked, none
- `--show-prompt` — show LLM prompt without running
- **Native notes**: `--show-prompt` works natively (just prints the prompt template).

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
- **Native notes**: `.worktreeinclude` filtering is supported natively.

### `grove worktree step for-each`
- `<ARGS>...` — command template
- **Native**: UnsupportedWithoutWt error.

### `grove worktree step promote`
- `[BRANCH]` — branch to promote
- **Native**: UnsupportedWithoutWt error.

### `grove worktree step prune`
- `--dry-run` — preview
- `-y, --yes` — skip approval
- `--min-age <MIN_AGE>` — skip young worktrees (parsed: `1h`, `2d`, `1w`)
- `--foreground` — run in foreground (ignored natively)

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

### `grove worktree merge` (existing, unchanged flags)
- Already has full flag set; no changes needed.

### `grove config import-wt` **[NEW]**
- `--dry-run` — preview
- `--force` — overwrite existing
- `--user-only` — import only user config
- `--project-only` — import only project config

### `grove shell init` **[NEW]**
- `{bash|zsh|fish}` — shell type (required)

### Error/Warning Strategy for Unsupported Features

| Situation | Behavior |
|---|---|
| Command not implemented natively | `UnsupportedWithoutWt` error with install instructions |
| Flag not applicable natively | Silently ignore (e.g., `--foreground` when always foreground) |
| Feature partially supported | Warning: "X feature requires wt backend for full support" + best effort |
| Config option not implementable | Warning at config load time: "X option requires wt backend" |

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

## Verification

### Per-phase testing

**Phase 1 (Config):**
```bash
pytest tests/test_user_config.py tests/test_config.py -v
```

**Phase 2 (CLI + Backend):**
```bash
pytest tests/test_cli.py tests/test_worktree_backend.py tests/test_cli_parser_shape.py -v
```

**Phase 3 (Lifecycle):**
```bash
pytest tests/test_worktree_switch.py tests/test_worktree_list.py tests/test_hooks.py -v
grove worktree list
grove worktree switch -c test-branch
grove worktree remove test-branch
```

**Phase 4 (Step):**
```bash
pytest tests/test_worktree_step.py -v
grove worktree step diff
grove worktree step push
grove worktree step rebase
grove worktree step commit  # opens $EDITOR
grove worktree step squash  # opens $EDITOR
```

**Phase 5 (LLM):**
```bash
pytest tests/test_llm.py -v
pip install -e ".[llm]"
grove worktree step commit  # uses LLM
grove worktree step squash  # uses LLM
```

**Phase 6 (Extras):**
```bash
pytest tests/test_config_import.py tests/test_shell.py tests/test_completion.py -v
```

**Phase 7 (Final):**
```bash
pytest                              # full suite
ruff check src/ tests/              # lint
ruff format --check src/ tests/     # format
```

### Integration test with wt backend
```bash
# With wt installed
grove worktree switch -c wt-test
grove worktree list --full
grove worktree step commit
grove worktree step for-each -- git status
grove worktree remove wt-test

# Without wt (test native fallback)
PATH_WITHOUT_WT=$PATH grove worktree switch -c native-test
PATH_WITHOUT_WT=$PATH grove worktree list
PATH_WITHOUT_WT=$PATH grove worktree step for-each -- git status  # should error clearly
```
