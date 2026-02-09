# grove

Git submodule management tools for nested repositories.

## Overview

`grove` provides a unified CLI for managing complex git submodule hierarchies. The package includes subcommands for verifying, synchronizing, pushing, visualizing, and managing worktrees of nested submodules.

All subcommands can be run from any subdirectory within the repository. Configuration (`.grove.toml`) is optional — commands gracefully handle repos without it.

## Installation

Install in development mode:

```bash
pip install -e .
```

For development with testing dependencies:

```bash
pip install -e ".[dev]"
```

**Requirements:** Python 3.11+

## Usage

### `grove init`

Generate a template `.grove.toml` configuration file with all available options documented as comments.

```bash
# Generate in current directory
grove init

# Generate at a specific path
grove init ../other-repo

# Overwrite an existing .grove.toml
grove init --force
```

**Exit codes:**
- `0` — Template written successfully
- `1` — File already exists (use `--force`) or invalid path

### `grove check`

Verify that all submodules are on branches (not detached HEAD) and that all sync-group submodules are at the same commit. When no sync groups are configured (no `.grove.toml`), the sync-group check is skipped with a warning.

```bash
# Basic check
grove check

# Verbose output with commit SHAs and remotes
grove check -v
grove check --verbose
```

**Exit codes:**
- `0` — All checks passed
- `1` — One or more issues found

### `grove push`

Push committed changes through nested submodules in bottom-up order using topological sort. This ensures child submodules are pushed before their parents.

```bash
# Push all submodules
grove push

# Dry run: preview what would be pushed
grove push --dry-run

# Force push (skip validation, for recovery scenarios)
grove push --force
```

**Exit codes:**
- `0` — Push successful
- `1` — Push failed or validation error

### `grove sync`

Synchronize submodule sync groups (defined in `.grove.toml`) across all locations in the repository tree. By default, syncs to the latest commit on `main` from the standalone repo or remote URL.

```bash
# Sync all groups to latest
grove sync

# Sync just the "common" group
grove sync common

# Sync "common" to a specific commit
grove sync common abc1234

# Preview changes without making them
grove sync --dry-run

# Commit changes but skip pushing
grove sync --no-push

# Skip remote sync validation
grove sync --force
```

**Flags:**
- `group` (positional) — Sync group name (syncs all groups if omitted)
- `commit` (positional) — Target commit SHA (optional)
- `--dry-run` — Preview changes without making them
- `--no-push` — Commit only, skip pushing to remotes
- `--force` — Skip remote sync validation

**Exit codes:**
- `0` — Sync successful (or no sync groups configured)
- `1` — Sync failed or validation error

### `grove visualize`

Open an interactive tkinter GUI showing the git repository hierarchy and submodule relationships. Can be run from any subdirectory within a repository. Nodes are color-coded by status:

- **Green** — Clean working directory
- **Yellow** — Uncommitted changes
- **Red** — Error or detached HEAD

Sync-group submodules are outlined with a distinct border color per group.

```bash
# Visualize current repository (auto-detects repo root)
grove visualize

# Visualize specific repository
grove visualize /path/to/repo
```

The GUI supports:
- Zooming (mouse wheel)
- Panning (click and drag)
- Node selection (click node for details)
- Interactive submodule operations

### `grove worktree`

Create and remove git worktrees with automatic recursive submodule initialization. Local git config (e.g. `user.name`, `user.email`, signing settings) is copied from the main worktree and its submodules to the new worktree by default. Structural keys (`core.*`, `remote.*`, `submodule.*`, `extensions.*`, `gc.*`) are excluded.

```bash
# Create a new worktree with a new branch
grove worktree add feature-x ../feature-x-wt

# Create a worktree using an existing branch
grove worktree add --checkout existing-branch ../wt-path

# Skip copying local git config
grove worktree add --no-copy-config feature-x ../feature-x-wt

# Remove a worktree
grove worktree remove ../feature-x-wt

# Force-remove a worktree with uncommitted changes
grove worktree remove --force ../feature-x-wt
```

### `grove worktree merge`

Merge a feature branch into the current branch across all repos in the submodule tree, processing leaves first (topological order). Supports pause/resume on conflicts or test failures, full abort/rollback, and conflict prediction.

```bash
# Start a merge
grove worktree merge my-feature

# Dry run: show what would happen (includes conflict prediction)
grove worktree merge my-feature --dry-run

# Merge only the root repo (skip submodules)
grove worktree merge my-feature --no-recurse

# Always create merge commits (no fast-forward)
grove worktree merge my-feature --no-ff

# Skip test commands
grove worktree merge my-feature --no-test

# Resume after resolving a conflict or fixing a test
grove worktree merge --continue

# Abort and restore all repos to pre-merge state
grove worktree merge --abort

# Show current merge progress
grove worktree merge --status
```

**Test commands** can be configured in `.grove.toml`:

```toml
[worktree-merge]
test-command = "pytest"                    # default for all repos

[worktree-merge.test-overrides]
"." = "npm test"                           # override for root repo
"technical-docs" = "make html"             # override for a submodule
"technical-docs/whisper" = ""              # empty string = skip tests
```

Test command resolution order (highest priority first):
1. Root's `test-overrides[repo's rel_path]`
2. Repo's own `.grove.toml` `test-command`
3. Root's `test-command`
4. No test command — skip testing

**Merge journal:** Actions are logged to `.git/grove/merge-journal-YYYY-MM.log` with monthly rotation. Useful for post-mortems. The journal is stored under `--git-common-dir`, so it is shared across all worktrees.

**Topology cache:** Submodule tree structure is cached at `.git/grove/topology.json`, enabling quick detection of structural divergence between branches. Also shared across worktrees via `--git-common-dir`.

**Merge state:** In-progress merge state is stored at `.git/grove/merge-state.json` (per-worktree via `--absolute-git-dir`), so each worktree can have its own independent merge in progress.

**Exit codes:**
- `0` — Merge complete (or dry run)
- `1` — Paused (conflict or test failure), or error
- `2` — Usage error

## Development

### Running Tests

```bash
pytest
```

### Project Structure

```
grove/
├── pyproject.toml              # Package configuration
├── README.md                   # This file
├── docs/
│   └── submodule-workflow.md   # Detailed workflow documentation
├── src/
│   └── grove/
│       ├── __init__.py
│       ├── cli.py              # Main CLI entry point
│       ├── config.py           # .grove.toml loader
│       ├── repo_utils.py       # Shared git utilities
│       ├── check.py            # check subcommand
│       ├── push.py             # push subcommand
│       ├── sync.py             # sync subcommand
│       ├── topology.py         # Topology caching for submodule structure
│       ├── worktree.py         # worktree add/remove subcommand
│       ├── worktree_merge.py   # worktree merge subcommand
│       └── visualizer/         # visualize subcommand
│           ├── __init__.py
│           ├── __main__.py
│           ├── actions.py
│           ├── app.py
│           ├── graph_canvas.py
│           ├── layout.py
│           └── repo_node.py
└── tests/                      # Test suite
```

## Documentation

For detailed workflow guidance, see:
- [docs/submodule-workflow.md](docs/submodule-workflow.md) — Common workflows and troubleshooting

## License

MIT License. See [LICENSE](LICENSE) for details.
