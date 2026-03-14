# CLAUDE.md

## Quick Reference

**Install:** `pip install -e ".[dev]"`
**Install (LLM):** `pip install -e ".[llm]"`
**Install (Docs):** `pip install -r docs/requirements.txt`
**Test:** `pytest`
**Build Docs:** `make -C docs html`
**Run:** `grove --help`

## Project Structure

- `src/grove/` — Package source (src layout)
- `src/grove/cli.py` — CLI entry point (`grove.cli:main`)
- `src/grove/claude_skills/` — Claude Code skill markdown files (package data)
- `src/grove/worktree_switch.py` / `worktree_list.py` / `worktree_step.py` — lifecycle command modules
- `src/grove/worktree_backend.py` — optional delegation to `wt`
- `src/grove/config_import.py` — `config import-wt` migration path
- `src/grove/llm.py` — commit/squash prompt + message generation fallback chain
- `tests/` — Test suite (pytest)
- `docs/` — Sphinx documentation site
- `docs/source/` — Documentation source (markdown via myst-parser)
- `docs/common/` — Shared Sphinx theme submodule (cleanroom-website-common)

## Key Concepts

- Zero external dependencies — stdlib only, requires Python 3.11+
- Operates on any git repo with submodules via `.grove.toml` config
- CLI entry point: `grove = "grove.cli:main"` (console_scripts)
- Skills are bundled as package data and installed via `grove claude install`
- Worktree backend defaults to `auto` (`wt` delegation when available, native fallback otherwise)

## Workflow Principles

- **Main checkout is the merge hub** — not a development environment. All development happens in worktrees.
- **Merges and pushes run from main checkout**, not from the worktree.
- **Detached HEAD is expected** for sync-group submodule members. Only non-sync-group submodules must be on named branches.
- **Grove auto-detects repo root** via `git rev-parse --show-toplevel` — commands work from any subdirectory.

## Configuration (.grove.toml)

The root `.grove.toml` defines:
- `[sync-groups.<name>]` — Submodules that must stay synchronized across locations
- `[worktree-merge]` — Test commands run during `grove worktree merge`
- `[worktree]` — Defaults for `grove worktree add` (e.g., `copy-venv`)
- `[worktree].backend` — `auto|native|wt` lifecycle backend mode
- `[commit.generation]` / `[worktree.llm]` — commit/squash message generation configuration
- `[aliases]` — Command shortcuts

## Typical Workflow

```
grove worktree add <path> <branch>   # Create isolated worktree
# ... develop in worktree ...
grove worktree merge <branch>        # Merge back (run from main checkout)
grove ship                           # Health check + push (from main checkout)
```

## Skills

| Skill | Purpose |
|-------|---------|
| `/grove` | General assistant — decision tree, command reference, worktree lifecycle (switch/list/step) |
| `/grove-add` | Create feature worktree with submodule initialization |
| `/grove-merge` | Bottom-up merge of feature branches with conflict handling |
| `/grove-checkout` | Checkout a ref on a submodule with recursive submodule update |
| `/grove-cascade` | Propagate submodule changes upward through dependency tree |
| `/grove-sync` | Synchronize sync-group instances with dry-run preview |
| `/grove-push` | Push with path, sync-group, or cascade filtering |
| `/grove-ship` | Health check then push (Claude skill workflow, not a CLI command) |

## Common Issues

**Detached HEAD:** Sync-group submodules are expected to be detached. Fix non-sync-group submodules with `grove worktree checkout-branches`.

**Lifecycle shortcuts:** Prefer `grove worktree switch`, `grove worktree list --format json`, and `grove worktree step` over manual git plumbing for routine worktree operations.

**Submodule pointer conflicts:** Submodule entries (mode 160000) conflict differently than regular files. Use the `git update-index --cacheinfo` commands grove prints — not `git checkout --ours/--theirs`.

**Directory context:** All grove commands auto-find repo root. For raw git commands, verify you're in the right directory with `pwd` and `git rev-parse --show-toplevel`.

## Testing

```bash
pytest              # Full suite
pytest -v           # Verbose
pytest -k <pattern> # Filter by name
```
