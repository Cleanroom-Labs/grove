# Sync-Group Cascade Workflow

This document describes the end-to-end workflow for making a change to a sync-group submodule and propagating it through the dependency tree.

## Scenario

You have a grove with a shared library (`libs/common`) used by multiple parent repos (`frontend`, `backend`, `shared`). All instances are managed as a sync group.

```
root/
├── frontend/
│   └── libs/common/   ← sync group instance
├── backend/
│   └── libs/common/   ← sync group instance
├── shared/
│   └── libs/common/   ← sync group instance
└── .grove.toml
```

## Step-by-Step Workflow

### 1. Verify current state

```bash
grove check -v
```

Ensure all sync-group instances are consistent before starting.

### 2. Make your change

Work in any instance — grove will propagate to the others:

```bash
cd frontend/libs/common
# ... edit files, run tests ...
git add -A && git commit -m "feat: add new utility"
cd ../../..
```

### 3. Sync all instances

```bash
grove sync common
```

This detects that `frontend/libs/common` is ahead and updates `backend/libs/common` and `shared/libs/common` to match.

If instances have diverged (different developers committed to different instances), sync automatically attempts a merge. See [Sync Divergence Merge](sync-divergence-merge.md) for details.

### 4. Cascade upward

```bash
grove cascade frontend/libs/common
```

Grove detects that `libs/common` is a sync-group submodule and enters DAG mode:

1. **All instances** (`frontend/libs/common`, `backend/libs/common`, `shared/libs/common`) are processed as leaves — running `local-tests` and `contract-tests`
2. **All parents** (`frontend`, `backend`, `shared`) stage their `libs/common` pointer and run `integration-tests`
3. **Root** (`.`) stages all three parents and runs `system-tests`

If any test fails, cascade pauses with diagnosis. Fix the issue and `grove cascade --continue`.

### 5. Push affected repos

```bash
grove push --cascade frontend/libs/common
```

This pushes exactly the repos that were part of the cascade — no more, no less. You can preview with `--dry-run` first.

### 6. Verify

```bash
grove check -v
```

Confirm everything is healthy.

## Handling Diverged Instances

If different developers have committed to different instances:

```bash
grove sync common
# → Merge conflict detected.
# → Resolve conflicts in: /path/to/workspace
# → Then run: grove sync --continue
```

Resolve the conflicts, then:

```bash
grove sync --continue
# → Merge resolved: abc1234
# → Run: grove sync common abc1234
```

Run the suggested sync command, then cascade as usual.

## Alternative Approaches

### Push everything

If you don't need targeted pushing, use `grove ship` instead:

```bash
grove ship   # health check + push all
```

### Skip sync-group consistency check

During prototyping, you may want to cascade even when instances are out of sync:

```bash
grove cascade libs/common --skip-checks
```

This proceeds with a warning. Use `--skip-checks` only during development — for production workflows, sync first.

### Cascade without sync

If you're only working in one instance and don't need horizontal consistency:

```bash
# Just cascade from one instance (linear chain)
grove cascade frontend/libs/common --skip-checks
```

This cascades only through `frontend → root`, skipping the other instances. Not recommended for final integration, but useful for quick iteration.

## Intermediate Sync Groups

Sync-group detection extends beyond the leaf level. If any **intermediate** repo in a cascade chain belongs to a sync group, cascade automatically discovers peer instances and expands the plan.

### Example

```
root/
├── workspace-a/           ← sync group "services" (instance 1)
│   └── libs/common/       ← cascade leaf
├── workspace-b/           ← sync group "services" (instance 2)
│   └── libs/common/
```

Cascade from `workspace-a/libs/common`:
1. Builds chain: `libs/common → workspace-a → root`
2. Detects `workspace-a` is in sync group "services"
3. Discovers peer `workspace-b`
4. Expands plan: after committing `workspace-a`, syncs `workspace-b` to the same commit, then cascades `workspace-b → root`

The cascade is automatically promoted from a linear chain to a DAG.

### Diverged Intermediates

If intermediate sync-group instances have diverged (different developers committed different things):

- **Clean divergence**: auto-merged before cascade starts
- **Merge conflict**: cascade pauses; resolve, then `grove cascade --continue`
- **Force**: `--skip-checks` skips divergence resolution (for prototyping)
