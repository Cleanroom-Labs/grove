---
name: grove-cascade
description: Propagate a submodule change upward through the dependency tree with tiered testing
---

# grove-cascade: Bottom-Up Cascade Integration

Propagate a change from a leaf submodule upward through intermediate parents to the root, running tests at each level and committing submodule pointer updates.

`$ARGUMENTS` should contain one of:
- `<path>` -- start a new cascade from a leaf submodule
- `--continue` -- resume after fixing a test failure
- `--abort` -- rollback all cascade commits
- `--status` -- show current cascade progress

## Starting a New Cascade

### Step 1: Check for in-progress cascade

Run `grove cascade --status`.

- If a cascade is already in progress, inform the user and suggest `--continue`, `--abort`, or `--status`.
- If no cascade in progress, proceed.

### Step 2: Dry-run preview

Run `grove cascade <path> --dry-run`.

Report:
- The cascade chain (leaf → intermediates → root)
- Which test tiers will run at each level
- Which test commands are configured

### Step 3: Confirm and execute

Show the preview and ask user to confirm. Mention:
- Number of repos in the chain
- Test tiers per role (leaf: local+contract, intermediate: +integration, root: +system)
- Suggest `--quick` for fast iteration or `--system` for thorough testing

Run `grove cascade <path>`.

### Step 4: Handle cascade pause

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

### Step 5: Report completion

When cascade finishes (exit code 0):
- Summary of repos and tiers tested
- Suggest `grove push` to distribute the committed changes
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
- **`--dry-run`**: preview cascade chain and test plan without executing

## Error Handling

- **"A cascade is already in progress"**: direct to `--continue`, `--abort`, or `--status`
- **"not a recognized repository"**: verify the path points to a submodule in the grove
- **"at least a leaf and one parent"**: cascade needs a submodule, not the root itself
- **No test tiers configured**: cascade will commit without testing (with warning)
