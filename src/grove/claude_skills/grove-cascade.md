---
name: grove-cascade
description: Propagate a submodule change upward through the dependency tree with tiered testing
---

# grove-cascade: Bottom-Up Cascade Integration

Propagate a change from a leaf submodule upward through intermediate parents to the root, running tests at each level and committing submodule pointer updates.

`$ARGUMENTS` should contain one of:
- `<path>` -- start a new cascade from a leaf submodule
- `--sync-group <name>` -- cascade all instances of a sync group by name
- `--continue` -- resume after fixing a test failure
- `--abort` -- rollback all cascade commits
- `--status` -- show current cascade progress

Optional flags: `--quick`, `--system`, `--no-system`, `--force`, `--push`, `--dry-run`

When the leaf is a **sync-group submodule** (multiple instances sharing the same URL), cascade automatically builds a DAG covering all instances and their parent chains, deduplicating shared ancestors.

## Starting a New Cascade

### Step 1: Check for in-progress cascade

Run `grove cascade --status`.

- If a cascade is already in progress, inform the user and suggest `--continue`, `--abort`, or `--status`.
- If no cascade in progress, proceed.

### Step 2: Sync-group consistency check

If the leaf belongs to a sync group, grove verifies all instances are at the same commit before proceeding.

- **All in sync**: cascade proceeds normally (DAG mode with all instances)
- **Out of sync**: cascade fails with a suggestion to run `grove sync <group>` first
- **Out of sync + `--force`**: cascade proceeds with a warning (useful during prototyping)

If the leaf is NOT in a sync group, this check is skipped and cascade runs as a linear chain.

### Step 3: Dry-run preview

Run `grove cascade <path> --dry-run`.

Report:
- The cascade chain (leaf → intermediates → root)
- Which test tiers will run at each level
- Which test commands are configured

### Step 4: Confirm and execute

Show the preview and ask user to confirm. Mention:
- Number of repos in the chain (or DAG)
- Whether DAG mode is active (sync-group submodule with multiple instances)
- Test tiers per role (leaf: local+contract, intermediate: +integration, root: +system)
- Suggest `--quick` for fast iteration or `--system` for thorough testing

Run `grove cascade <path>`.

### Step 5: Handle cascade pause

If the cascade pauses (exit code 1):

**local-tests or contract-tests failure:**
- Show which repo and tier failed.
- Tell the user to fix the issue in the specified repo.
- After fixing: `/grove-cascade --continue`

**integration-tests failure (with auto-diagnosis):**
- Show which repo failed and the diagnosis results.
- If the submodule's local-tests passed: problem is at the interface between parent and child.
- If the submodule's local-tests failed: problem is inside the dependency.
- After fixing: `/grove-cascade --continue`

**system-tests failure (with two-phase diagnosis):**
- Show phase 1 (local-tests of direct submodules) and phase 2 (integration-tests) results.
- Guide the user to the likely source of the failure.
- After fixing: `/grove-cascade --continue`

### Step 6: Report completion

When cascade finishes (exit code 0):
- Summary of repos and tiers tested
- Suggest `grove push --cascade <path>` to push exactly the repos that were cascaded
- Suggest `grove check` to verify grove health

## Continuing a Cascade (`--continue`)

1. Run `grove cascade --continue`.
2. If tests fail again, report the failure with diagnosis.
3. If it succeeds, report completion as above.

## Aborting a Cascade (`--abort`)

1. Run `grove cascade --abort`.
2. This restores all repositories to their pre-cascade state (git reset --hard).
3. Report which repos were restored and confirm the abort.

## Checking Status (`--status`)

1. Run `grove cascade --status`.
2. Report: cascade target, per-repo status with progress indicators.
3. For paused repos, show which tier failed and any diagnosis results.
4. Suggest next actions.

## Flag Guide

- **Default** (`grove cascade <path>`): everyday cascading, balanced speed and confidence
- **`--quick`**: rapid iteration during development, only local + contract tests
- **`--system`**: before releases or after major changes, system-tests at every level
- **`--no-system`**: when experimental sibling changes would break system tests
- **`--sync-group NAME`**: cascade all instances of a sync group by name (alternative to specifying a path)
- **`--force`**: skip sync-group consistency check (for prototyping when instances are out of sync)
- **`--push`**: push all cascade repos after successful completion (persisted through pause/resume)
- **`--dry-run`**: preview cascade chain and test plan without executing

## Sync-Group Cascade Workflow

When a submodule belongs to a sync group (e.g., `libs/common` shared by `frontend`, `backend`, `shared`):

1. `grove sync common` — ensure all instances are at the same commit
2. Make your change in one instance
3. `grove sync common` — propagate the change to all instances
4. `grove cascade libs/common --push` — cascade from ALL instances and push on success
   - Alternative: `grove cascade --sync-group common --push` — same result, by group name
   - Or omit `--push` and run `grove push --cascade libs/common` separately

In DAG mode, execution order is by depth (leaf-first, root-last):
- **Leaves** (all sync-group instances): run leaf test tiers
- **Intermediates** (parent repos): stage updated submodule pointers, run intermediate test tiers
- **Root**: stage all updated parents, run root test tiers

## Intermediate Sync-Group Handling

Cascade detects sync groups at **all levels**, not just the leaf. If an intermediate repo in the cascade chain belongs to a sync group, cascade automatically:

1. **Discovers peer instances** of that intermediate sync group
2. **Expands the plan** to include peers and their parent chains (may promote linear → DAG)
3. **Designates one instance as primary** (commits normally), others as sync targets
4. **Syncs peers** to the primary's SHA after each primary commits

If intermediate sync-group instances have **diverged**:
- **Pre-cascade**: grove auto-merges cleanly resolvable divergences before starting the cascade
- **Deferred**: groups with merge conflicts are resolved dynamically during cascade execution
- **Merge conflict**: cascade pauses with instructions; resolve conflicts, then `grove cascade --continue`

## Error Handling

- **"A cascade is already in progress"**: direct to `--continue`, `--abort`, or `--status`
- **"not a recognized repository"**: verify the path points to a submodule in the grove
- **"at least a leaf and one parent"**: cascade needs a submodule, not the root itself
- **"instances are not in sync"**: run `grove sync <group>` first, or use `--force` to bypass
- **"Merge conflict syncing <peer>"**: resolve conflicts in the specified path, then `--continue`
- **"Divergence could not be auto-resolved"**: group will be resolved dynamically during cascade
- **No test tiers configured**: cascade will commit without testing (with warning)
