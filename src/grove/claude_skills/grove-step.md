---
name: grove-step
description: Run iterative worktree step commands (diff/commit/squash/push/rebase/prune)
---

# grove-step: Iterative Lifecycle Steps

Use this skill for incremental development and cleanup flows inside a worktree.

## Quick Commands

```bash
grove worktree step diff [target]
grove worktree step commit
grove worktree step squash [target]
grove worktree step rebase [target]
grove worktree step push [target]
grove worktree step prune --dry-run
```

## Workflow

1. Start with `step diff` to review branch delta from default target.
2. Use `step commit` or `step squash` to produce message-quality commits.
3. Rebase or push as needed.
4. Run prune in dry-run mode before destructive cleanup.

## Notes

- `for-each`, `promote`, and `relocate` require WorkTrunk backend delegation.
- `--no-verify` skips pre-commit hooks and should be limited to recovery scenarios.
- `copy-ignored` honors `.worktreeinclude` when present.
