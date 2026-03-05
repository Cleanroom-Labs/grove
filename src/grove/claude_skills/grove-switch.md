---
name: grove-switch
description: Switch to an existing worktree or create one on demand
---

# grove-switch: Switch or Create Worktrees

Use this skill when the user wants to move to another worktree quickly.

## Quick Commands

```bash
grove worktree switch <branch>
grove worktree switch -c <branch>
grove worktree switch ^
grove worktree switch -
```

## Workflow

1. Resolve target branch from arguments.
2. If the target worktree exists, run `grove worktree switch <branch>`.
3. If missing and creation is desired, run `grove worktree switch -c <branch>`.
4. If shell integration is enabled, rely on directive output for directory change.
5. Report final target path and next lifecycle step (`step diff`, `step commit`, etc.).

## Notes

- `pr:N` / `mr:N` shortcuts require the WorkTrunk backend.
- Use `--no-cd` for script contexts where path printing is preferred over shell integration.
