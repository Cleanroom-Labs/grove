# Design: Worktrunk Integration for Grove (Codex Revised)

## Status
- Target branch: `grove.merge`
- Purpose: final integration design to merge `grove.codex` + `grove.claude`
- Supersedes: prior `worktrunk-integration-design.md` for implementation guidance

## Context
Grove owns submodule-graph workflows (`check`, `sync`, `push`, `cascade`, `worktree merge`).
Worktrunk (`wt`) excels at lifecycle UX (`switch`, `list`, `remove`, `step`, hooks, richer status).

The integrated design must:
1. Work without `wt` (native baseline).
2. Delegate to `wt` when available/configured.
3. Preserve safety and correctness in destructive/graph-sensitive flows.
4. Keep CLI/config interfaces stable and migration-friendly.

## Design Goals
1. **Correctness first**: no known unsafe fallback paths; no delegation ordering bugs.
2. **Low coupling architecture**: command modules own native behavior and delegation decisions.
3. **Config coherence**: one canonical Grove config model with compatibility readers.
4. **Clear capability boundaries**: unsupported native features fail fast with explicit guidance.
5. **Strict regression control**: parser-shape stability + lint + full tests + complexity gate.

## Non-Goals
1. Re-implement all wt UX in native mode (CI integrations, advanced interactive flows).
2. Introduce unrelated features beyond integration scope.
3. Remove legacy config support immediately.

## Architecture

## CLI layering
- `src/grove/cli.py`: thin orchestration only.
- `src/grove/cli_parsers.py`: command/flag tree.
- `src/grove/cli_dispatch.py`: routing to command modules.

This split is required to avoid monolithic dispatch logic and reduce drift risk.

## Command ownership
Each worktree module owns its flow:
- `worktree.py` (`add`, `remove`, `init-submodules`, shared worktree ops)
- `worktree_switch.py`
- `worktree_list.py`
- `worktree_step.py`
- `hooks.py`

Each module executes:
1. resolve repo/context,
2. attempt delegation,
3. run native implementation if not delegated.

## Delegation model (command-local)
Use `maybe_delegate_*() -> int | None` functions:
- `maybe_delegate_switch`
- `maybe_delegate_list`
- `maybe_delegate_remove`
- `maybe_delegate_step`
- `maybe_delegate_hook`

Semantics:
- `None`: native path should continue.
- `int`: delegated execution finished; return that code.

No class-hierarchy runtime dispatch is required.
An optional typed contract (`Protocol` or lightweight ABC) may be added for documentation only.

## Backend Resolution

## Config
```toml
[worktree]
backend = "auto"  # auto | native | wt
```

## Resolution rules
1. `native` -> never delegate.
2. `wt` -> delegate; error if `wt` missing.
3. `auto` -> delegate only when `wt` exists on PATH; else native.

## Delegation environment
When delegating:
1. load merged Grove config (user + project + legacy fallback + explicit override),
2. synthesize a temporary wt-compatible TOML (only supported mapped fields),
3. set `WORKTRUNK_CONFIG_PATH` for delegated call,
4. run `wt ...`,
5. clean up temp file.

## Capability Contract

## Native-required
- `worktree add`
- `worktree init-submodules`
- `worktree merge`

## Native + wt-delegable
- `worktree switch`
- `worktree list`
- `worktree remove`
- `worktree step` core (`push`, `rebase`, `diff`, `commit`, `squash`, `copy-ignored`, `prune`)
- `worktree hook` operations

## wt-only (native unsupported)
- `worktree step for-each`
- `worktree step promote`
- `worktree step relocate`

Unsupported native response:
`Error: grove worktree step <cmd> requires the worktrunk backend (wt).`

## CLI Surface (Target)

## Worktree switch
- Positional: `[BRANCH]` (supports `^`, `-`, `@`, and pass-through for wt-only patterns like `pr:N`, `mr:N`)
- Flags: `--branches`, `--remotes`, `-c/--create`, `-b/--base`, `-x/--execute`, `-y/--yes`, `--clobber`, `--no-cd`, `--no-verify`

## Worktree list
- Flags: `--format {table,json}`, `--branches`, `--remotes`, `--full`, `--progressive`
- Native mode may warn and degrade for wt-only enriched fields.

## Worktree remove
- Positional: `[TARGETS]...` (branch/path, default current when applicable)
- Flags: `-f/--force`, `-D/--force-delete`, `--no-delete-branch`, `--foreground`, `--no-verify`, `-y/--yes`

## Worktree step
- `commit`: `--stage`, `--no-verify`, `--show-prompt`, `-y/--yes`
- `squash`: `[TARGET]`, `--stage`, `--no-verify`, `--show-prompt`, `-y/--yes`
- `push`: `[TARGET]`
- `rebase`: `[TARGET]`
- `diff`: `[TARGET] [EXTRA_ARGS]...`
- `copy-ignored`: `--from`, `--to`, `--dry-run`, `--force`
- `prune`: `--dry-run`, `--foreground`, `--min-age`, `-y/--yes`
- wt-only: `for-each`, `promote`, `relocate`

## Worktree hook
- `hook show [HOOK_TYPE] [--expanded]`
- `hook <type> [NAME] [-y|--yes] [--var KEY=VALUE ...]`

## Configuration Model

## Canonical locations and precedence
Lowest -> highest precedence:
1. `~/.config/grove/config.toml`
2. `.config/grove.toml`
3. `.grove.toml` **only if** `.config/grove.toml` absent (legacy fallback)
4. `GROVE_CONFIG_PATH` if set

Additional env:
- `GROVE_CONFIG_HOME` overrides user config directory root.

## Merge semantics
- Use raw-dict deep merge before typed parsing.
- Explicit false/empty values must override lower-precedence defaults.
- Avoid truthy checks for precedence decisions.

## Required sections
- Existing:
  - `[sync-groups]`
  - `[worktree-merge]`
  - `[cascade]`
  - `[aliases]`
  - `[commit]`
  - hook sections
- Integration sections:
  - `[worktree]` (`backend`, `copy-venv`, `worktree-path`)
  - `[list]` defaults
  - `[commit.generation]` command
  - optional provider-based LLM config (`[worktree.llm]`)
  - lifecycle merge controls (`[merge]`)
  - optional ci metadata (`[ci]`)

## Hooks config compatibility
Support both:
1. shorthand: `post-create = "cmd"`
2. table:
```toml
[hooks.post-create]
deps = "npm ci"
```

## Migration/Import
Provide `grove config import` workflow to:
1. import wt config into Grove format,
2. migrate legacy patterns to canonical `.config/grove.toml`,
3. preserve user intent with explicit conflict reporting.

## Native Safety and Correctness Contracts

## Remove contract
If `git worktree remove` fails due to submodules:
1. detect dirty child repos,
2. refuse deletion without `--force`,
3. print actionable error and affected paths,
4. only then allow manual fallback removal.

Manual deletion must never silently bypass force semantics.

## Delegation contract
For wt-only commands:
1. attempt delegation first when backend resolves to wt,
2. fail as unsupported only in native path.

No premature command rejection before backend resolution.

## Sync contract
Remote URL resolution for sync groups must search nested `.gitmodules` consistently with sync-group discovery behavior.

## Repo-root contract
Command behavior from nested directories must respect documented repository/root discovery expectations (including submodule boundary cases where applicable).

## Hooks Behavior

## Hook types
`post-create`, `post-start`, `pre-merge`, `post-merge`, `pre-remove`, `post-remove`, `pre-switch`, `post-switch`, `pre-commit`.

## Behavior rules
1. `--no-verify` disables relevant hooks.
2. Fail-fast for pre-* hooks that gate destructive or mutating actions.
3. Post hooks may continue after failures with warning unless explicitly gating.
4. Native limitations (for switch background semantics, etc.) must be surfaced as clear warnings.

## Template model
Support:
- variables: `branch`, `worktree_path`, `worktree_name`, `repo`, `repo_path`, `primary_worktree_path`, `default_branch`, `commit`, `short_commit`, `target`, `base`
- filters: `sanitize`

## LLM Strategy (Hybrid)
For commit/squash message generation:
1. If delegated to wt, wt handles LLM flow.
2. If `[commit.generation].command` configured: run command with prompt on stdin.
3. Else if Strands providers configured/available: try providers in configured order.
4. Else fallback to `$EDITOR`.

Requirements:
1. graceful failure at each layer,
2. no hard dependency when LLM extras absent,
3. deterministic tests for fallback order.

## Quality Gates and Verification

## Required checks
1. `ruff check src/ tests/`
2. `pytest -q`
3. complexity gate:
   - `python scripts/quality/check_function_size.py --max-lines 180`
4. parser-shape parity tests for CLI stability.

## Targeted suites
- `tests/test_cli*.py`
- `tests/test_config*.py`
- `tests/test_worktree*.py`
- `tests/test_hooks.py`
- `tests/test_llm.py`
- `tests/test_sync.py`

## End-to-end scenarios
1. native mode: full lifecycle smoke tests.
2. wt mode: delegation behavior and passthrough flags.
3. config migration/import scenarios.
4. destructive-path safety regressions.

## Rollout Plan (Design-Coupled)
1. Phase A: correctness/safety fixes.
2. Phase B: delegation convergence.
3. Phase C: CLI split and parity stabilization.
4. Phase D: config convergence + migration tooling.
5. Phase E: LLM hybrid and hooks normalization.
6. Phase F: CI/test consolidation and docs.

Each phase must end green on required gates before proceeding.

## Risks and Mitigations
1. Structural churn regressions:
   - mitigate with small commits, parser-shape tests, strict phase gates.
2. Config behavior drift:
   - mitigate with precedence matrix tests and compatibility fixtures.
3. Native/wt parity gaps:
   - mitigate with command-level native + delegated tests.
4. Safety regressions in remove/sync:
   - mitigate with dedicated regression tests and invariants.
5. Optional dependency fragility:
   - mitigate with hybrid LLM fallback and explicit warnings.

## Acceptance Criteria
1. One coherent architecture active (no conflicting runtime patterns).
2. No known unsafe deletion or delegation-order bugs.
3. CLI surface stable and test-guarded.
4. Config compatibility and migration path available.
5. Full CI green with lint, full tests, and complexity gate.
