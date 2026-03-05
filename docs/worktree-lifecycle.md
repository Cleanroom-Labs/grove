# Worktree Lifecycle

This document covers Grove's lifecycle commands for day-to-day worktree development.

## Core Commands

### `grove worktree switch`

Switch to an existing worktree or create one on demand:

```bash
grove worktree switch my-branch
grove worktree switch -c my-branch
grove worktree switch ^
grove worktree switch -
```

Key notes:

- `-c/--create` creates a missing worktree at the configured path template.
- `--no-cd` prints target path without attempting shell directory integration.
- `pr:N` / `mr:N` shortcuts are WorkTrunk-only when running native.

### `grove worktree list`

List known worktrees with optional inventory expansion:

```bash
grove worktree list
grove worktree list --format json
grove worktree list --branches --remotes
grove worktree list --full
```

### `grove worktree remove`

Remove worktrees by branch (or compatibility path), with safety checks:

```bash
grove worktree remove my-branch
grove worktree remove --force my-branch
grove worktree remove --no-delete-branch my-branch
```

Native behavior refuses deleting dirty submodule worktrees unless `--force` is set.

### `grove worktree step`

Incremental lifecycle helpers:

```bash
grove worktree step commit
grove worktree step squash
grove worktree step push
grove worktree step rebase
grove worktree step diff
grove worktree step copy-ignored
grove worktree step prune --dry-run
```

WorkTrunk-only step subcommands in native mode:

- `for-each`
- `promote`
- `relocate`

### `grove worktree hook`

Inspect or run lifecycle hooks:

```bash
grove worktree hook show
grove worktree hook show --expanded
grove worktree hook pre-remove
grove worktree hook pre-remove --var branch=my-branch
```

## Shell Integration

`grove shell init` prints wrapper functions for directory-switch directives:

```bash
eval "$(grove shell init zsh)"
```

Supported shells:

- `bash`
- `zsh`
- `fish`

## Hook Types

Supported hook types:

- `post-create`
- `post-start`
- `pre-switch` / `post-switch`
- `pre-commit`
- `pre-merge` / `post-merge`
- `pre-remove` / `post-remove`

Notes:

- Native mode warns and skips shell-only switch hooks (`pre-switch`, `post-switch`).
- Native mode runs background-style hooks (`post-start`, `post-remove`) in the foreground with warning.
- `--no-verify` disables applicable pre/post hooks for supported commands.
