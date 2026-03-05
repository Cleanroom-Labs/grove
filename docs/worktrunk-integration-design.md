# Design: Worktrunk Integration for Grove

## Context

Grove manages the **submodule graph** (sync, push, merge across nested repos). [Worktrunk](https://github.com/max-sixty/worktrunk) (`wt`) manages the **worktree lifecycle** (create, switch, list, remove, hooks, LLM commits). The two tools complement each other, but users shouldn't need both installed. This design integrates worktrunk's worktree lifecycle features into grove as a first-class capability, with worktrunk as an optional backend that enhances native functionality when available.

**Goals:**
- Grove handles the full worktree lifecycle (switch, list, remove, step commands, hooks) with or without wt
- When wt is installed, grove delegates to it for richer UX (interactive picker, CI status, background operations)
- When wt is NOT installed, grove provides faithful native implementations of core features
- Unsupported native features fail fast with clear error messages
- LLM-powered commit/squash messages via Strands (optional `grove[llm]` extra)
- Configuration mirrors wt's file layout (`~/.config/grove/config.toml`, `.config/grove.toml`)

---

## Architecture: Backend Strategy Pattern

### `src/grove/worktree_backend.py`

Abstract `WorktreeBackend` with two implementations:

- **`NativeBackend`** — Pure Python using git subprocess calls. Core grove stays zero external dependencies.
- **`WtBackend`** — Shells out to `wt`. Synthesizes a temporary wt-compatible config via `WORKTRUNK_CONFIG_PATH` env var when grove config includes wt-relevant settings.

**Selection** via config:
```toml
[worktree]
backend = "auto"  # "auto" (default) | "native" | "wt"
```
`"auto"`: try `wt` on PATH, fall back to `native`.

### Backend method interface

```python
class WorktreeBackend(ABC):
    # Lifecycle
    switch(branch, *, create=False, base=None, execute=None, yes=False) -> int
    list_worktrees(*, format="table", full=False, branches=False) -> int
    remove(branches, *, force=False, force_delete=False, no_delete_branch=False) -> int

    # Step commands
    step_commit(*, stage="all", yes=False) -> int
    step_squash(target=None, *, stage="all", yes=False) -> int
    step_push(target=None) -> int
    step_rebase(target=None) -> int
    step_diff(target=None, extra_args=None) -> int
    step_copy_ignored(*, from_branch=None, to_branch=None, dry_run=False, force=False) -> int
    step_for_each(command_args) -> int          # WtBackend only
    step_promote(branch=None) -> int            # WtBackend only
    step_prune(*, dry_run=False, yes=False) -> int
    step_relocate(branches=None, *, dry_run=False) -> int  # WtBackend only

    # Hooks
    run_hook(hook_type, *, name=None, variables=None) -> int
```

Commands that NativeBackend cannot implement raise `UnsupportedWithoutWt("grove worktree step for-each requires the worktrunk backend (wt)")`.

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

**Dry-run for wt-delegated commands**: When grove has `--dry-run` but wt doesn't, report "will run: wt <command>" and stop.

---

## Configuration

### File locations (mirrors wt)

| Level | Path | Precedence |
|---|---|---|
| User | `~/.config/grove/config.toml` | Lowest (defaults) |
| Project | `.config/grove.toml` | Higher (repo-specific) |
| Legacy | `.grove.toml` (repo root) | Merged into project config; deprecation warning |

### User config structure (`~/.config/grove/config.toml`)

```toml
[worktree]
backend = "auto"
copy-venv = true
worktree-path = "{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}"

[worktree.llm]
# Fallback chain — try in order, fall to $EDITOR if all fail
providers = [
  { provider = "claude", model = "haiku" },
  { provider = "ollama", model = "qwen3:4b" },
]

[commit]
stage = "all"          # "all" | "tracked" | "none"

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

### wt config synthesis

When `WtBackend` runs a command, it:
1. Reads grove's config (user + project)
2. Maps relevant settings to wt's config format
3. Writes a temp config file
4. Sets `WORKTRUNK_CONFIG_PATH` env var before calling `wt`

---

## LLM Integration

### Module: `src/grove/llm.py`

**Optional dependency**: `pip install grove[llm]` installs `strands-agents[ollama]`, `strands-agents-tools`, `claude-agent-sdk`.

**Fallback chain for commit/squash messages:**
1. wt backend available → delegate to wt (it has its own LLM config)
2. Strands installed + providers configured → try providers in order
3. `$EDITOR` → open editor like `git commit` without `-m`
4. Error with install instructions

**Provider fallback** (`[worktree.llm].providers`):
```python
for provider_config in llm_config.providers:
    try:
        return generate_with_strands(provider_config, prompt)
    except (LLMUnavailableError, LLMTimeoutError):
        continue
# All providers failed
return open_editor(template)
```

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

### Strands integration pattern

```python
from grove.llm import generate_message  # lazy import of strands

def generate_message(prompt: str, config: LLMConfig) -> str | None:
    """Try each provider in order. Returns None if all fail."""
    for entry in config.providers:
        try:
            Agent, _ = require_strands()
            bundle = build_provider_bundle(
                resolve_provider(entry.provider),
                model_id=entry.model,
                allowed_tools=[],
                max_turns=1,
                web_enabled=False,
                session_key="grove-commit",
                disable_thinking=True,
                mode="local",
            )
            agent = Agent(model=bundle.model, tools=[])
            response = agent(prompt)
            return getattr(response, "text", str(response)).strip()
        except Exception:
            continue
    return None
```

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
| pre-merge | Before merge | Yes | Yes | Yes |
| post-merge | After merge | Yes | No | Yes |
| pre-remove | Before worktree removed | Yes | Yes | Yes |
| post-remove | After removal | Background | No | Yes (foreground fallback) |
| pre-switch | Before switch | Yes | Yes | Warn + skip (requires shell integration) |
| post-switch | After switch | Background | No | Warn + skip (requires shell integration) |

Background hooks in native mode run in foreground with a warning (true background requires shell integration).

**Unsupported native hooks**: If `pre-switch` or `post-switch` are configured and native backend is active, emit a warning: "pre-switch/post-switch hooks require the worktrunk backend. Install wt for full hook support."

---

## `grove worktree switch` — Native Implementation

**Native mode:**
1. List worktrees via `git worktree list --porcelain`
2. If branch arg provided: find matching worktree, print path (or cd via shell function)
3. If `-c` flag: create worktree at configured path template, init submodules, run post-create hooks
4. If no args: print numbered list, prompt for selection (or pipe through `fzf` if available)
5. Branch shortcuts: `^` → default branch, `-` → previous worktree (stored in state file)

**Shell integration** for native `cd`:
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
[post-create]
submodules = "grove worktree init-submodules {{ worktree_path }} --reference {{ primary_worktree_path }}"
```

---

## Files to Create/Modify

### New files

| File | Purpose | Est. lines |
|---|---|---|
| `src/grove/worktree_backend.py` | Abstract backend + NativeBackend + WtBackend | ~400 |
| `src/grove/worktree_step.py` | Step subcommands (commit, squash, push, rebase, diff, copy-ignored, prune) | ~500 |
| `src/grove/worktree_list.py` | Native worktree list (moderate features) | ~200 |
| `src/grove/worktree_switch.py` | Native switch + shell integration | ~250 |
| `src/grove/hooks.py` | Hook execution with template expansion | ~200 |
| `src/grove/llm.py` | LLM integration via Strands (lazy import) | ~150 |
| `src/grove/user_config.py` | User config at `~/.config/grove/config.toml` | ~150 |
| `tests/test_worktree_backend.py` | Backend strategy tests | ~300 |
| `tests/test_worktree_step.py` | Step command tests | ~400 |
| `tests/test_worktree_list.py` | List command tests | ~200 |
| `tests/test_worktree_switch.py` | Switch command tests | ~200 |
| `tests/test_hooks.py` | Hook execution tests | ~200 |
| `tests/test_llm.py` | LLM integration tests (mocked strands) | ~150 |
| `tests/test_user_config.py` | User config loading tests | ~150 |

### Modified files

| File | Changes |
|---|---|
| `src/grove/cli.py` | Add subparsers: `switch`, `list`, `init-submodules`, `step *`, `hook *`; shell init command |
| `src/grove/worktree.py` | Extract init/checkout functions; add `--exclude-sync-group`; refactor `add` to use backend |
| `src/grove/config.py` | Add user config loading, merge user + project, hooks/llm sections; backward compat for `.grove.toml` |
| `src/grove/completion.py` | Dynamic branch/path completion; new subcommand completions |
| `pyproject.toml` | Add `[project.optional-dependencies] llm = ["strands-agents[ollama]", "strands-agents-tools", "claude-agent-sdk"]` |
| `src/grove/claude_skills/grove-add.md` | Update for new flags |
| `README.md` | Document new commands, config, wt integration |

### New skills

| File | Purpose |
|---|---|
| `src/grove/claude_skills/grove-switch.md` | Switch/create worktree |
| `src/grove/claude_skills/grove-list.md` | List worktrees |
| `src/grove/claude_skills/grove-step.md` | Step commands |

---

## Implementation Order

### Phase 1: Foundation
1. User config system (`user_config.py` + config.py changes)
2. Backend abstraction (`worktree_backend.py`)
3. `grove worktree init-submodules` (extract from `worktree.py`)
4. Revise `grove worktree add` branch checkout (default include sync-group, `--exclude-sync-group`)
5. Tests for phase 1

### Phase 2: Core Lifecycle Commands
6. `grove worktree list` (native moderate + wt passthrough)
7. `grove worktree switch` (native + shell integration + wt passthrough)
8. `grove worktree remove` (enhanced native + wt passthrough)
9. Hooks system (`hooks.py` + template expansion)
10. Tests for phase 2

### Phase 3: Step Commands
11. `grove worktree step` framework + push, rebase, diff
12. `grove worktree step commit` + `squash` (git operations, $EDITOR fallback)
13. `grove worktree step copy-ignored` + `prune`
14. Tests for phase 3

### Phase 4: LLM Integration
15. `llm.py` — Strands integration with provider fallback chain
16. Wire LLM into step commit/squash
17. `pyproject.toml` optional extras
18. Tests for phase 4 (mocked strands)

### Phase 5: Polish
19. Tab completion improvements
20. Claude skills (new + updated)
21. README documentation (commands, config, wt integration guide)

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
- **Native notes**: `--foreground` is always true natively (no background removal). Just ignore the flag silently.

### `grove worktree step commit`
- `-y, --yes` — skip approval
- `--no-verify` — skip hooks
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
- **Native notes**: `--min-age` → best effort (parse simple durations like `1h`, `2d`). `--foreground` → always true natively (ignored silently).

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

### Error/Warning Strategy for Unsupported Features

| Situation | Behavior |
|---|---|
| Command not implemented natively | `UnsupportedWithoutWt` error with install instructions |
| Flag not applicable natively | Silently ignore (e.g., `--foreground` when already foreground) |
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

**Phase 1:**
```bash
pytest tests/test_user_config.py tests/test_worktree_backend.py
grove worktree init-submodules /path/to/worktree --reference /path/to/main
grove worktree add --exclude-sync-group feature-x ../feature-x-wt
```

**Phase 2:**
```bash
pytest tests/test_worktree_list.py tests/test_worktree_switch.py tests/test_hooks.py
grove worktree list
grove worktree switch -c test-branch
grove worktree remove test-branch
```

**Phase 3:**
```bash
pytest tests/test_worktree_step.py
grove worktree step diff
grove worktree step push
grove worktree step rebase
grove worktree step commit  # opens $EDITOR
grove worktree step squash  # opens $EDITOR
```

**Phase 4:**
```bash
pytest tests/test_llm.py
pip install -e ".[llm]"
grove worktree step commit  # uses LLM
grove worktree step squash  # uses LLM
```

**Phase 5:**
```bash
grove completion zsh | head -20  # check new completions
pytest  # full suite
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
