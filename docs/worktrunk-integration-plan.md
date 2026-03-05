# Worktrunk Integration — Implementation Plan

Target architecture: `docs/worktrunk-integration-design.md`

---

## Strategy

1. Build from scratch on `grove.merge`, informed by the `grove.codex` and `grove.claude` spikes. Neither implementation is copied wholesale.
2. Patch-port in small batches by subsystem, not giant branch merges. Per batch: port code, port/adjust tests, run gates, commit.
3. Safety fixes land with the modules they belong to, not as a separate upfront phase.
4. Every phase must pass validation gates before proceeding.

## In/Out of Scope

**In scope:**
- Worktree lifecycle integration (switch, list, remove, step, hook, init-submodules)
- Backend delegation behavior
- Config convergence and migration/import path
- LLM flow consolidation
- CI and test quality gates

**Out of scope:**
- New product features beyond integration design intent
- Broad repo-wide refactors unrelated to worktrunk integration
- Non-essential docs sweeps outside touched areas

## Non-Negotiable Invariants

1. No unsafe worktree deletion path may bypass force semantics.
2. Unsupported native commands must delegate when wt backend is active.
3. No CLI/config interface drift without explicit migration behavior and tests.
4. Legacy config fallback semantics remain intact unless intentionally changed.

---

## Pattern Sourcing Reference

When implementing each module, use the best pattern from the spike that demonstrated it. This table is a reference — not a checklist of things to copy.

### From grove.codex

| Pattern | Why |
|---------|-----|
| `maybe_delegate_*(repo_root, args) -> int \| None` | Simpler than ABC hierarchy; each module owns its full lifecycle |
| `cli.py` + `cli_parsers.py` + `cli_dispatch.py` | Separation of concerns; parser is ~1000 lines and deserves its own file |
| `_delegation_env(repo_root)` context manager | Cleaner resource management for temp config than try/finally |
| `_synthesize_wt_config(raw)` with full section mapping | Maps list, commit, merge lifecycle, ci, hooks — not just worktree.path and copy_venv |
| `merge_dicts(base, override)` raw dict merging | New config fields automatically participate in merging; no field-by-field maintenance |
| `dump_toml(data)` custom serializer | Proper TOML output for config synthesis; replaces `json.dumps()` |
| `GROVE_CONFIG_PATH` / `GROVE_CONFIG_HOME` env vars | Explicit override mechanism the design calls for |
| `iter_grove_config_paths(repo_root)` ordered path tuple | Clean precedence model |
| `config import-wt` migration command | Practical tool for wt-to-grove migration |
| `worktree_common.py` shared helpers | `resolve_default_branch`, `resolve_target_branch`, `normalize_remainder_args`, `emit_switch_target` |
| Hook string shorthand (`post-create = "npm install"`) | Better UX for single-command hooks |
| `hook show --expanded` | Users can see resolved template variables |
| `pr:N`/`mr:N` detection with fail-fast | Clear error in native mode |
| `cli_parser_shape` stability test | Prevents accidental CLI surface drift |
| `_append_flag()` helper for conditional flag building | Reduces boilerplate in delegation functions |
| Dirty submodule check before manual delete fallback | Safety: never delete uncommitted work without `--force` |

### From grove.claude

| Pattern | Why |
|---------|-----|
| Strands LLM provider fallback chain | Design spec; more resilient than single shell command |
| `parse_llm_config()` with `VALID_LLM_PROVIDERS` validation | Catches misconfiguration at load time, not at LLM call time |
| `LLMProviderEntry` typed dataclass | Clear provider/model pairing |
| `COMMIT_PROMPT` / `SQUASH_PROMPT` templates | Verbatim from design spec |
| Interactive hook approval flow | Safety for hooks that modify state |
| Test fixtures with real git repos + submodules | More realistic than mocked subprocess |
| `_resolve_shortcut()` for `^`, `-`, `@` | Clean shortcut resolution |
| `_save_previous_worktree` / `_get_previous_worktree` state | Enables `-` shortcut |
| `generate_shell_wrapper(shell)` | Shell-specific function generation for cd integration |
| `WorktreeInfo` dataclass for list output | Typed data model vs raw dicts |

### Patterns to avoid

| Pattern | Source | Why avoid |
|---------|--------|-----------|
| `WorktreeBackend` ABC + `NativeBackend` + `WtBackend` | grove.claude | Double dispatch, impedance mismatch with `_make_args()` |
| `type("Args", ...)()` / `SimpleNamespace` arg reconstruction | grove.claude | Symptom of the ABC pattern; unnecessary with delegation functions |
| Monolithic `cli.py` with parser + dispatch + backend routing | grove.claude | Too many concerns in one file |
| Field-by-field `merge_configs()` | grove.claude | Each new field requires explicit merge logic |
| `json.dumps()` for TOML serialization | grove.claude | Not correct TOML for complex values |
| Step dispatch blocking wt-only commands before backend check | grove.claude | Prevents delegation even when wt is available |
| `run_git(..., cwd=...)` calls | grove.claude | `run_git` uses `-C` flag, doesn't accept `cwd` |
| `shutil.rmtree` without dirty check | grove.claude | Deletes uncommitted work silently |
| Truthy check for boolean config merge | grove.claude | `false` cannot override `true` |
| `setattr()` loops in config parsing | grove.codex | Less explicit than named field assignment |

---

## Phase 0: Baseline

Verify the starting point is clean and record baseline metrics.

### Tasks

1. Run and record:
   ```bash
   pytest -q
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```
2. Commit this plan document and the updated design doc.

### Exit criteria

- All checks pass. Baseline metrics recorded.

### Commit

`integration: baseline verification and plan`

---

## Phase 1: Config Foundation

Build the config infrastructure that all other modules depend on.

### 1.1 Create `user_config.py`

New file with path resolution, TOML load/merge/dump utilities:

- `get_user_config_dir()` — `$GROVE_CONFIG_HOME` or `~/.config/grove`
- `get_user_config_path()` — `<dir>/config.toml`
- `get_project_config_path(repo_root)` — `<root>/.config/grove.toml`
- `get_legacy_config_path(repo_root)` — `<root>/.grove.toml`
- `get_explicit_grove_config_path()` — `$GROVE_CONFIG_PATH`
- `iter_grove_config_paths(repo_root) -> tuple[Path, ...]` — ordered merge paths
- `load_toml_file(path) -> dict` — read TOML to raw dict
- `merge_dicts(base, override) -> dict` — deep recursive merge (explicit `false`/empty must override)
- `dump_toml(data) -> str` — stdlib-only TOML serializer

### 1.2 Extend `config.py` with new dataclasses

Add to existing `config.py`:

- `LLMProviderEntry(provider: str, model: str)`
- `LLMConfig(providers: list[LLMProviderEntry])`
- `CommitGenerationConfig(command: str | None)`
- `CommitConfig(stage: str, generation: CommitGenerationConfig)`
- `ListConfig(full: bool, branches: bool, remotes: bool, url: bool)`
- `LifecycleMergeConfig(squash, commit, rebase, remove, verify)`
- `CIConfig(platform: str | None)`
- `HooksConfig` — hook type validation and storage

Update `WorktreeConfig` with `backend`, `copy_venv`, `worktree_path`, `llm`.

Update `GroveConfig` to include all new sections.

Add constants: `VALID_BACKENDS`, `VALID_LLM_PROVIDERS`, `VALID_STAGE_VALUES`, `HOOK_TYPES`.

Add `parse_llm_config()` with provider name validation at load time.

Add hook parsing that supports both table and string shorthand.

### 1.3 Update config loading to use raw dict merging

Replace current `load_config()`:
1. `iter_grove_config_paths()` returns ordered paths
2. Load each as raw dict, merge with `merge_dicts()`
3. Parse merged dict into `GroveConfig` via dedicated `_parse_*` functions
4. Use `None` sentinels (not falsy defaults) for merge semantics

Preserve external import interface: `from grove.config import load_config, GroveConfig, ...`

### 1.4 Tests

- `tests/test_user_config.py` — path resolution, env overrides, `dump_toml`, `merge_dicts`, precedence
- Update `tests/test_config.py` — new sections, hook string shorthand, LLM validation, raw dict merging, explicit `false` overriding `true`

### Verify

```bash
pytest tests/test_config.py tests/test_user_config.py -v
pytest -q  # full suite still passes
ruff check src/ tests/
```

### Exit criteria

- Config precedence matrix tests pass.
- Legacy `.grove.toml` loading unchanged for existing sections.
- All existing tests still pass.

### Commit

`integration: config foundation — user config, raw dict merging, new sections`

---

## Phase 2: CLI Split and Backend Infrastructure

### 2.1 Extract `cli_parsers.py`

Move `build_parser()` from `cli.py` to new `cli_parsers.py`. Add parsers for all new subcommands:

- `worktree switch` — all flags from design doc
- `worktree list` — `--format`, `--branches`, `--remotes`, `--full`, `--progressive`
- `worktree init-submodules` — extracted as standalone command
- `worktree step {commit,squash,push,rebase,diff,copy-ignored,prune,for-each,promote,relocate}`
- `worktree hook {show, <type>}`
- `config import-wt`
- `shell init`

All flags per the design doc's "CLI Surface: Flag Parity with wt" section.

### 2.2 Extract `cli_dispatch.py`

New file with `dispatch_command(args, parser) -> int`. Lazy imports throughout.

### 2.3 Slim down `cli.py`

Reduce to ~100 lines: `main()` does alias expansion, `--no-color` handling, calls `build_parser()` and `dispatch_command()`.

### 2.4 Add parser-shape stability test

`tests/test_cli_parser_shape.py` — captures expected CLI surface (commands, subcommands, flags) and fails on unexpected changes.

### 2.5 Create `worktree_backend.py`

Delegation functions:

```python
def maybe_delegate_switch(repo_root, args) -> int | None
def maybe_delegate_list(repo_root, args) -> int | None
def maybe_delegate_remove(repo_root, args) -> int | None
def maybe_delegate_step(repo_root, args) -> int | None
def maybe_delegate_hook(repo_root, args) -> int | None
```

Internal helpers:
- `_resolve_backend(repo_root) -> str` — reads config, checks `shutil.which("wt")`
- `_delegation_env(repo_root)` — context manager: synthesize config, set `WORKTRUNK_CONFIG_PATH`, yield env, cleanup
- `_synthesize_wt_config(raw) -> dict` — maps all grove config sections to wt equivalents
- `_append_flag(cmd, flag, value)` — conditional flag builder

Documentation-only `BackendContract(Protocol)` at the top.

### 2.6 Create `worktree_common.py`

Shared helpers:
- `resolve_default_branch(repo_root, rows=None)`
- `resolve_target_branch(repo_root, explicit_target)`
- `normalize_remainder_args(extra_args)`
- `emit_switch_target(args, target_path)`

### 2.7 Fix `remove_worktree` safety bug

In `worktree.py`, add dirty submodule check before the `shutil.rmtree` fallback:

```python
if not force and _has_dirty_children(worktree_path):
    print(Colors.red("Worktree has uncommitted changes in submodules."))
    print("Use --force to remove anyway.")
    return 1
```

### 2.8 Add `init_submodules_command` standalone entry point

Extract `_init_submodules` + `_checkout_submodule_branches` as a CLI-callable function in `worktree.py`.

### 2.9 Update worktree.py dispatch

Update `run(args)` to route new worktree subcommands (`switch`, `list`, `step`, `hook`, `init-submodules`) to their handler modules.

### 2.10 Tests

- `tests/test_worktree_backend.py` — `_resolve_backend`, delegation functions, config synthesis
- `tests/test_cli_parser_shape.py` — parser stability
- Update `tests/test_cli.py` — three-file structure, dispatch routing
- Update `tests/test_worktree.py` — dirty submodule check on remove (regression test)

### Verify

```bash
pytest tests/test_cli.py tests/test_cli_parser_shape.py tests/test_worktree_backend.py tests/test_worktree.py -v
pytest -q
ruff check src/ tests/
```

### Exit criteria

- CLI split complete, parser-shape test passes.
- Backend resolution and delegation functions exist with tests.
- Remove safety bug fixed with regression test.
- All existing tests still pass.

### Commit

`integration: CLI split, backend delegation, safety fixes`

---

## Phase 3: Lifecycle Commands

Build one command at a time: switch, list, hooks.

### 3.1 Create `worktree_switch.py`

Module entry: `run(args)` calls `maybe_delegate_switch()`, then `switch_native()`.

Native implementation:
- Parse worktrees via `git worktree list --porcelain`
- Resolve shortcuts: `^` (default branch), `-` (previous), `@` (current)
- Detect and fail-fast on `pr:N`/`mr:N` in native mode
- Branch matches existing worktree → emit switch target
- `--create` or branch not found → create worktree, init submodules, run `post-create` hooks
- No args → numbered interactive list (include `--branches`/`--remotes` rows if flagged)
- `--clobber`: remove stale path before creation
- `--no-verify`: skip hooks
- `--no-cd`: suppress directory change
- State tracking: `_save_previous_worktree()` / `_get_previous_worktree()`
- Shell integration: `generate_shell_wrapper(shell)` for bash/zsh/fish
- Pre-switch/post-switch hooks: warn and skip in native mode
- Path computation via `worktree-path` template with `{{ branch | sanitize }}`

All call sites use `run_git(repo_root, ...)` — never `cwd=`.

### 3.2 Create `worktree_list.py`

Module entry: `run(args)` calls `maybe_delegate_list()`, then native list.

- `WorktreeInfo` dataclass: path, branch, commit, dirty, ahead/behind, age, subject, is_main, is_current, kind
- `discover_worktrees(repo_root)` — parse porcelain, enrich with metadata
- `collect_worktree_rows(repo_root, *, include_branches, include_remotes)` — adds branch/remote rows
- Table output: branch, commit, status, ahead/behind, age, subject
- JSON output: all fields (schema matches wt's for native-populated fields)
- `--full`: show what native can compute; warn that CI/LLM summaries require wt
- `--progressive`: accept silently natively, pass to wt when delegating

### 3.3 Create `hooks.py`

- `run_configured_hooks(repo_root, hook_type, *, name, variables, yes)` — run all/named hooks
- `_render_template(command, variables)` — `{{ var | filter }}` with `sanitize` filter
- `_iter_hook_commands(repo_root, hook_type)` — yields `(name, command)` from config
- `_confirm_hook_execution(hook_id, command)` — interactive TTY approval
- `run(args)` — CLI entry for `grove worktree hook show|<type>`
- `_show_hooks(repo_root, hook_type, *, expanded)` — display with optional expansion
- Delegation via `maybe_delegate_hook()` at top of `run()`
- Constants: `_FAIL_FAST_HOOKS`, `_SHELL_ONLY_HOOKS`, `_BACKGROUND_HOOKS`
- Shell-only hooks (`pre-switch`, `post-switch`): warn and skip in native mode
- Background hooks (`post-start`, `post-remove`): run in foreground with warning in native mode

### 3.4 Tests

- `tests/test_worktree_switch.py` — shortcuts, create-on-demand, state persistence, shell wrappers, `pr:N` fail-fast, `--clobber`, `--no-verify`
- `tests/test_worktree_list.py` — table/JSON output, metadata enrichment, branch/remote rows
- `tests/test_hooks.py` — template expansion, hook execution, show/expanded, string shorthand, approval flow, shell-only warnings

### Verify

```bash
pytest tests/test_worktree_switch.py tests/test_worktree_list.py tests/test_hooks.py -v
pytest -q
ruff check src/ tests/
```

### Exit criteria

- `switch`, `list`, `hook` commands work natively.
- Delegation to wt tested (with wt mocked).
- Shell wrapper generation tested for bash/zsh/fish.

### Commit

`integration: switch, list, hooks — core lifecycle commands`

---

## Phase 4: Step Commands

### 4.1 Create `worktree_step.py`

Module entry: `run(args)` calls `maybe_delegate_step()`, then dispatches to native handler.

For wt-only commands (`for-each`, `promote`, `relocate`): delegation check comes first. Only if native backend, raise `UnsupportedWithoutWt` error.

Step handlers (all `_run_*(repo_root, args) -> int`):

- `_run_commit` — stage changes, run `pre-commit` hook, generate message via LLM or `$EDITOR`, commit
- `_run_squash` — soft reset to merge-base, restage, generate squash message
- `_run_push` — `git push . HEAD:<target>`
- `_run_rebase` — `git rebase <target>`
- `_run_diff` — `git diff <merge-base>...HEAD` with extra args
- `_run_copy_ignored` — copy gitignored files between worktrees; `.worktreeinclude` filtering when present
- `_run_prune` — find merged secondary worktrees; `--min-age` duration parsing; `--dry-run`; `--yes`

Helpers:
- `_build_commit_prompt(repo_root)` — structured prompt with diffstat/diff/recent commits
- `_build_squash_prompt(repo_root, target, base)` — commits since base + diffstat
- `_stage_changes(repo_root, stage)` — `git add -A` / `-u` / skip
- `_confirm_message(message, template)` — Y/n/edit prompt
- `_open_editor(initial_text)` — `$VISUAL`/`$EDITOR` tempfile flow
- `_parse_duration(duration)` — parses `1h`, `2d`, `1w`

### 4.2 Tests

- `tests/test_worktree_step.py` — all step handlers, `$EDITOR` confirm flow, `--yes` bypass, duration parsing, wt-only error with delegation check first, `.worktreeinclude` filtering

### Verify

```bash
pytest tests/test_worktree_step.py -v
pytest -q
ruff check src/ tests/
```

### Exit criteria

- All native step commands work.
- wt-only commands error clearly in native mode.
- Delegation ordering verified: delegate before rejecting.

### Commit

`integration: step commands — commit, squash, push, rebase, diff, copy-ignored, prune`

---

## Phase 5: LLM Integration

### 5.1 Create `llm.py`

Dual-path implementation:

```python
def generate_message(repo_root, prompt, llm_config) -> str | None:
    """Try generation command first, then Strands providers. Returns None if all fail."""
```

Shell command path: runs `[commit.generation].command` with prompt on stdin, reads stdout.

Strands path: lazy import, provider registry, `_build_model()`, try each provider in order.

Constants: `COMMIT_PROMPT`, `SQUASH_PROMPT` templates (verbatim from design doc).

Helpers: `build_commit_prompt(repo_root)`, `build_squash_prompt(repo_root, base, target)`, `_truncate(text, max_chars)`.

Error type: `LLMUnavailableError` for missing strands install.

### 5.2 Wire LLM into step commit/squash

Update `_run_commit` and `_run_squash` in `worktree_step.py` to call `generate_message()`, falling back to `$EDITOR` when it returns `None`.

### 5.3 Update `pyproject.toml`

```toml
[project.optional-dependencies]
llm = ["strands-agents[ollama]", "strands-agents-tools", "claude-agent-sdk"]
```

### 5.4 Tests

- `tests/test_llm.py` — shell command execution, Strands provider fallback (mocked), prompt building, truncation, no-config fallback, `LLMUnavailableError` when strands absent

### Verify

```bash
pytest tests/test_llm.py tests/test_worktree_step.py -v
pytest -q
ruff check src/ tests/
```

### Exit criteria

- Each fallback level works independently.
- Strands lazy import doesn't break when not installed.
- Shell command path works.
- Deterministic tests for fallback ordering.

### Commit

`integration: LLM with dual-path generation (shell command + Strands providers)`

---

## Phase 6: Config Import and Remaining Features

### 6.1 Create `config_import.py`

`grove config import-wt` command:
- `run(args)` — imports WorkTrunk user/project config into Grove canonical paths
- `_translate_wt_to_grove(raw)` — schema mapping
- `_import_one(source, target, *, dry_run, force)` — load, merge or replace, write

### 6.2 Shell init

`grove shell init {bash,zsh,fish}` command:
- Reuses `generate_shell_wrapper(shell)` from `worktree_switch.py`
- Outputs the shell wrapper function to stdout for `eval` usage

### 6.3 Update `completion.py`

Add completions for new subcommands: `switch`, `list`, `init-submodules`, `step *`, `hook *`, `config import-wt`, `shell init`.

### 6.4 Tests

- `tests/test_config_import.py` — translation, merge, dry-run
- Update `tests/test_completion.py` — new subcommands present

### Verify

```bash
pytest tests/test_config_import.py tests/test_completion.py -v
pytest -q
ruff check src/ tests/
```

### Exit criteria

- Config import works for wt user and project configs.
- Shell wrappers generate correctly for all three shells.
- Tab completion covers new subcommands.

### Commit

`integration: config import, shell init, completion updates`

---

## Phase 7: Test Consolidation and Polish

### 7.1 Coverage sweep

For each module, verify:
- Happy path
- Error paths and edge cases
- Delegation behavior (wt available vs not)
- Flag interaction (e.g., `--force` + `--no-delete-branch`)

Port additional test scenarios from the spike branches where they cover gaps.

### 7.2 End-to-end CLI tests

Targeted tests exercising the full path from CLI args through dispatch to module execution:
- `grove worktree switch -c <branch>` — creates worktree, inits submodules
- `grove worktree list --format json` — produces valid JSON
- `grove worktree step diff` — shows merge-base diff
- `grove worktree hook show` — lists configured hooks
- `grove worktree remove <branch>` — removes with hooks

### 7.3 Documentation

- Update `README.md` with new commands, config, wt integration
- Update `grove-add.md` skill for `--exclude-sync-group`
- New skills: `grove-switch.md`, `grove-list.md`, `grove-step.md`
- Update `CLAUDE.md` with new project structure and commands

### 7.4 Final verification

```bash
pytest -q                           # all tests pass
ruff check src/ tests/              # no lint issues
ruff format --check src/ tests/     # format clean
grove --help                        # CLI works
grove worktree --help               # new subcommands visible
```

### Exit criteria

- Full test suite green.
- No lint issues.
- Documentation matches implemented behavior.
- All known bugs from both spikes covered by regression tests.

### Commit

`integration: test consolidation, docs, and final verification`

---

## Validation Gates (every phase)

These must pass before proceeding to the next phase:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
pytest -q                      # or phase-targeted subset + full run before commit
```

After Phase 2, additionally:
- `tests/test_cli_parser_shape.py` — CLI surface matches expected shape

---

## Commit Strategy

Small, reviewable commits per phase:

| # | Message | Scope |
|---|---------|-------|
| 0 | `integration: baseline verification and plan` | Plan committed, baseline verified |
| 1 | `integration: config foundation — user config, raw dict merging, new sections` | Config dataclasses, user config, raw dict merging |
| 2 | `integration: CLI split, backend delegation, safety fixes` | CLI three-file split, delegation functions, worktree dispatch, remove fix |
| 3 | `integration: switch, list, hooks — core lifecycle commands` | Three command modules with tests |
| 4 | `integration: step commands` | All step handlers |
| 5 | `integration: LLM with dual-path generation` | Shell command + Strands provider chain |
| 6 | `integration: config import, shell init, completion updates` | Extras |
| 7 | `integration: test consolidation, docs, and final verification` | Polish |

Each commit must pass validation gates.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Raw dict merging changes break existing config loading | Medium | High | Existing config tests catch regressions; add explicit `false` override tests |
| CLI parser extraction breaks tab completions | Low | Medium | Run completion tests immediately after split |
| Switch shell integration is complex | Medium | Medium | Test each shell (bash/zsh/fish) wrapper independently |
| Delegation ordering regression for wt-only commands | Medium | High | Dedicated regression test: delegate before rejecting |
| Remove safety regression | High | High | Regression test: dirty submodule check before `shutil.rmtree` |
| LLM dependency fragility | Low | Low | Strands is lazy-imported; tests mock it; shell command fallback exists |
| Config import schema drift from wt | Low | Medium | Test against real wt config samples |
| Coverage gaps not caught until late | Medium | Medium | Run targeted test suite per phase, not just at end |

---

## Acceptance Criteria

1. **One coherent implementation path** — no duplicate competing flows, no dead code from either spike branch.
2. **Full design doc coverage** — every command, flag, and config section in `worktrunk-integration-design.md` is implemented or explicitly documented as wt-only with clear error message.
3. **No safety regressions** — remove fallback checks dirty state, step dispatch delegates before rejecting, `run_git` called correctly, config merge uses proper sentinels.
4. **Config compatibility** — user/project/legacy/explicit-override precedence works; `grove config import-wt` available for migration.
5. **LLM fallback chain functional** — shell command, Strands providers, `$EDITOR`, and error path all work independently.
6. **CI green** — `pytest` + `ruff` pass in CI across Python 3.11/3.12/3.13.
