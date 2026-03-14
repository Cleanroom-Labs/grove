# WorkTrunk Integration

[WorkTrunk](https://worktrunk.dev/) (`wt`) is a standalone worktree lifecycle tool that provides enhanced features like PR/MR shortcuts, richer metadata, and advanced commit message generation. Grove provides a complete native worktree lifecycle but can optionally delegate lifecycle commands to WorkTrunk when it's installed.

## Backend Modes

Configure backend mode in Grove config:

```toml
[worktree]
backend = "auto"   # auto | native | wt
```

Mode behavior:

- `native`: always run Grove-native lifecycle implementations.
- `wt`: always delegate lifecycle commands to `wt` (errors if `wt` missing).
- `auto`: delegate when `wt` is present on PATH; otherwise fallback to native.

## Delegated Command Surface

Lifecycle commands that may delegate:

- `grove worktree switch`
- `grove worktree list`
- `grove worktree remove`
- `grove worktree step ...`
- `grove worktree hook ...`

For WorkTrunk-only operations (for example `grove worktree step for-each`), delegation is checked first when backend is `auto`/`wt`.

## Config Synthesis

When Grove delegates to `wt`, it synthesizes a temporary WorkTrunk config file from Grove config and sets `WORKTRUNK_CONFIG_PATH` for that delegated invocation.

Mapped areas include:

- `worktree-path`
- `[list]`, `[commit]`, `[merge]`, `[ci]`
- hook sections (`[pre-remove]`, etc.)

This keeps one canonical Grove config file rather than split configuration ownership.

## Migration

Import existing WorkTrunk config into Grove:

```bash
grove config import-wt
grove config import-wt --user
grove config import-wt --project
grove config import-wt --dry-run
grove config import-wt --force
```

Conflict behavior:

- Without `--force`, conflicting fields are reported and import exits non-zero.
- With `--force`, the target Grove config file is replaced by imported content.

## LLM Fallback Chain

For `worktree step commit/squash`, Grove uses:

1. Delegated `wt` behavior (when backend delegates)
2. `[commit.generation].command`
3. `[worktree.llm].providers` (optional `grove[llm]`)
4. `$EDITOR`
