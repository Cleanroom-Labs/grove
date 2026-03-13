---
name: grove-list
description: List worktrees and branch inventory for lifecycle planning
---

# grove-list: Inspect Worktree Inventory

Use this skill when the user needs to see active worktrees and branch coverage.

## Quick Commands

```bash
grove worktree list
grove worktree list --format json
grove worktree list --branches --remotes
grove worktree list --full
```

## Workflow

1. Start with `grove worktree list --format json` for machine-readable output.
2. If branch inventory is needed, include `--branches` and optionally `--remotes`.
3. Summarize current, main, and stale/merged candidates for follow-up actions.
4. Recommend next actions (`switch`, `step prune --dry-run`, `remove`).

## Notes

- Native mode returns core metadata (branch/path/status/ahead-behind/age).
- `--full` may include additional fields when available; WorkTrunk backend can enrich further.
