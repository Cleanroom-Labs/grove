# Guides Overview

These guides cover grove's practical workflows. Pick the one that matches what you're trying to do.

| Scenario | Guide |
|----------|-------|
| **Starting a new feature** — create an isolated worktree, develop, and manage branches | [Worktree Lifecycle](worktree-lifecycle.md) |
| **Propagating a submodule change** — test and push a leaf change upward through the tree | [Cascade Guide](cascade-guide.md) |
| **Syncing shared dependencies** — keep sync-group instances consistent, then cascade | [Sync-Group Cascade Workflow](sync-group-cascade-workflow.md) |
| **Full development workflow** — architecture, worktrees, merging, and parallel development | [Submodule Workflow](submodule-workflow.md) |
| **Team patterns and testing** — test tiers, sync discipline, and building composable systems | [Best Practices](best-practices.md) |

## Typical Flow

Most development follows this pattern:

1. **Create a worktree** — `grove worktree add ../my-feature feature-branch`
2. **Develop** — commit, test, iterate in the worktree
3. **Switch back to main** — `grove worktree switch main`
4. **Merge** — `grove worktree merge feature-branch`
5. **Push** — `grove push` (or `grove check -v && grove push` for safety)

For sync-group changes, insert a **sync** step (`grove sync <group>`) and a **cascade** step (`grove cascade <path>`) between developing and pushing.
