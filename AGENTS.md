# AGENTS.md

## Overview

`grove` is a Python 3.11+ CLI for managing nested git submodules. The package is stdlib-only on the Python side — keep new runtime dependencies out unless there is a strong reason.

## Setup and Verification

```bash
pip install -e ".[dev]"          # Install for development
pip install -e ".[llm]"          # Optional: LLM-backed commit/squash generation
pip install -r docs/requirements.txt  # Optional: Sphinx documentation
pytest                           # Run the full test suite
ruff check src tests             # Lint
ruff format --check src tests    # Format check
make -C docs html                # Build docs (requires docs dependencies)
grove --help                     # Verify CLI works
```

## Project Structure

- `src/grove/cli.py` — CLI entry point (`grove.cli:main`)
- `src/grove/cli_parsers.py` — argparse parser definitions for all commands
- `src/grove/cli_dispatch.py` — command dispatch routing
- `src/grove/repo_utils.py` — shared git/repository helpers
- `src/grove/config.py` — `.grove.toml` / `.config/grove.toml` loader
- `src/grove/worktree.py` — worktree add/remove and branch checkout
- `src/grove/worktree_merge.py` — bottom-up merge with persisted state
- `src/grove/worktree_switch.py` — worktree switching lifecycle
- `src/grove/worktree_list.py` — worktree inventory and branch metadata
- `src/grove/worktree_step.py` — iterative step commands (commit/squash/push/rebase/diff/prune)
- `src/grove/worktree_backend.py` — optional delegation to WorkTrunk (`wt`)
- `src/grove/sync.py` — sync-group discovery, target resolution, propagation
- `src/grove/cascade.py` — cascade workflow for propagating changes upward with tiered tests
- `src/grove/check.py` — submodule health checks
- `src/grove/push.py` — bottom-up push with path/sync-group/cascade filtering
- `src/grove/hooks.py` — lifecycle hook execution
- `src/grove/llm.py` — commit/squash message generation fallback chain
- `src/grove/visualizer/` — browser-based visualizer (server.py + web/ assets)
- `src/grove/claude_skills/` — packaged skill markdown files (installed via `grove claude install`)
- `tests/` — pytest test suite
- `docs/source/` — Sphinx documentation source (markdown via myst-parser)
- `docs/common/` — shared Sphinx theme submodule (cleanroom-website-common)

## Workflow Invariants

- **Main checkout is the merge hub** — not a development environment. All development happens in worktrees.
- **Merges and pushes run from main checkout**, not from feature worktrees.
- **Sync-group submodules are expected to be detached.** Only non-sync-group submodules should be on named branches.
- **`worktree add` syntax is `grove worktree add <path> <branch>`** — path first, branch second.
- **Grove auto-detects repo root** via `git rev-parse --show-toplevel` — commands work from any subdirectory.
- For repo-wide commands, prefer running from the top-level repository root. Running from inside `docs/common/` or another submodule scopes git discovery to that submodule.

## Configuration

Config precedence (lowest → highest):
1. User config: `~/.config/grove/config.toml`
2. Project config: `.config/grove.toml`
3. Legacy fallback: `.grove.toml` (deprecated)
4. Explicit override: `$GROVE_CONFIG_PATH`

Key sections:
- `[sync-groups.<name>]` — submodules that must stay synchronized
- `[worktree-merge]` — test commands for merge workflow
- `[worktree]` — defaults for `grove worktree add`
- `[cascade]` — four test tiers (local/contract/integration/system)
- `[aliases]` — command shortcuts

## CLI Commands

| Command | Purpose |
|---------|---------|
| `grove init` | Generate template `.config/grove.toml` |
| `grove check` | Verify submodule health and sync-group consistency |
| `grove push` | Push bottom-up through submodules |
| `grove sync` | Synchronize sync-group instances |
| `grove checkout` | Checkout a ref with recursive submodule update |
| `grove cascade` | Propagate changes upward with tiered testing |
| `grove worktree add` | Create feature worktree with submodule init |
| `grove worktree remove` | Remove a worktree |
| `grove worktree merge` | Merge feature branch bottom-up |
| `grove worktree switch` | Switch/create worktrees |
| `grove worktree list` | Worktree inventory (table or JSON) |
| `grove worktree step` | Iterative steps (diff/commit/squash/push/rebase/prune) |
| `grove worktree checkout-branches` | Fix detached HEAD on non-sync-group submodules |
| `grove visualize` | Interactive submodule visualizer |
| `grove completion install` | Shell tab completion setup |
| `grove config import-wt` | Migrate WorkTrunk config |
| `grove claude install` | Install Claude Code skills |

## Coding Guidance

- Match the existing style: straightforward stdlib code, small helpers, minimal abstraction.
- Keep command behavior explicit and CLI-facing output readable.
- Add or update pytest coverage with code changes, especially for git edge cases and failure paths.
- When touching parser behavior in `cli_parsers.py`, check completion behavior in `completion.py` and parser tests in `tests/test_cli.py` and `tests/test_completion.py`.
- When changing repo discovery, sync, push, or merge logic, review parent/child relationships and topological ordering carefully — many commands depend on `RepoInfo.parent`.

## Common Issues

- **Detached HEAD**: sync-group submodules are expected to be detached. Fix non-sync-group submodules with `grove worktree checkout-branches`.
- **Submodule pointer conflicts**: mode 160000 entries conflict differently than regular files. Use `git update-index --cacheinfo` commands grove prints — not `git checkout --ours/--theirs`.
- **Directory context**: all grove commands auto-find repo root. For raw git commands, verify directory with `pwd` and `git rev-parse --show-toplevel`.

## High-Risk Areas

- `worktree remove` and merge/sync resume paths can affect user work; preserve safety checks.
- Repo-root detection is sensitive because this repo contains a submodule (`docs/common/`).
- Sync-group behavior depends on nested `.gitmodules` traversal and correct propagation of parent repo commits.
- The visualizer ships static web assets as package data; preserve packaging entries in `pyproject.toml`.

## Before Finishing

- Run targeted tests for changed modules at minimum.
- Prefer the full `pytest` suite before closing out meaningful changes.
- Call out any behavior/docs mismatches you notice, especially around command syntax or working-directory assumptions.
