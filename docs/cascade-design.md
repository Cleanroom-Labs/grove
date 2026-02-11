# Cascade Design

`grove cascade` propagates a change from a leaf submodule upward through the dependency tree to the root, running tests at each level and committing submodule pointer updates.

## Motivation

Existing grove commands serve different axes:

- **sync** — horizontal consistency (all instances of a sync-group submodule at the same commit)
- **push** — distribute already-committed changes bottom-up
- **merge** — merge a feature branch across the submodule tree

None of them answer: "I changed a leaf submodule — now integrate it upward through the tree with testing at each level." Cascade fills this vertical-integration gap.

## Four-Tier Test Model

Four test tiers form a progressive confidence ladder:

| Tier | What it tests | Mocking strategy |
|------|--------------|-----------------|
| `local-tests` | Project-internal correctness | All dependencies mocked |
| `contract-tests` | Interface boundaries | Other side mocked |
| `integration-tests` | Direct interface works when connected | Transitive dependencies mocked |
| `system-tests` | Full end-to-end chain | No mocking |

Each tier is optional. When a tier is not configured, it is skipped during cascade execution.

### Fallback

`local-tests` falls back to `[worktree-merge].test-command` when not configured explicitly. This enables easy adoption — projects already using `worktree merge` with a test command get cascade testing for free.

## Role-Based Execution

Each repo in the cascade chain has a role based on its position:

| | Leaf | Intermediate | Root |
|---|---|---|---|
| `local-tests` | Run | Run | Run |
| `contract-tests` | Run | Run | Run |
| `integration-tests` | **Skip** | Run | Run |
| `system-tests` | **Skip** | **Skip** | **Run** |

**Rationale:**
- **Leaf skips integration-tests**: the leaf's dependencies haven't changed.
- **Intermediates skip system-tests**: full end-to-end at every level would be slow and might break due to experimental changes in sibling dependencies.
- **Root runs system-tests**: final gate before the cascade is complete.

### Override Flags

- `--system` — run system-tests at every level (thorough mode)
- `--no-system` — skip system-tests even at root (fast mode)
- `--quick` — run only local-tests + contract-tests everywhere (fastest)

## Auto-Diagnosis

When tests fail, cascade provides diagnostic information to help locate the failure source.

### `local-tests` / `contract-tests` failure

No auto-diagnosis. The problem is internal to the project or in its interface expectations.

### `integration-tests` failure

**Single-phase diagnosis:** Run `local-tests` of the changed child submodule.

- Submodule's local-tests **fail** → problem is *inside* the dependency
- Submodule's local-tests **pass** → problem is at the *interface* between parent and child

### `system-tests` failure

**Two-phase diagnosis:**

1. **Phase 1:** Run `local-tests` of the changed child submodule. If it fails, the culprit is found — skip phase 2.
2. **Phase 2:** Run `integration-tests` of the changed child submodule.
   - Fails → problem is in a transitive dependency or deeper interface
   - Passes → problem is at the root's own interface or a non-obvious interaction

## State Machine

Cascade uses a state file for pause/resume/abort, following the same pattern as `worktree merge`.

### States per repo

```
pending → local-passed → contract-passed → integration-passed → system-passed → committed
                                     ↘ paused (on any failure)
```

### State file

Location: `.git/grove/cascade-state.json` (inside the git directory, not the working tree).

Contents:
- `submodule_path` — the original cascade target
- `started_at` — ISO 8601 timestamp
- `system_mode` — `"default"` | `"all"` | `"none"`
- `quick` — boolean
- `repos[]` — per-repo state including role, status, pre_cascade_head, failed_tier, diagnosis, child_rel_paths
- `sync_group_name` — (optional) name of the sync group if DAG mode
- `is_dag` — (optional) boolean, true for DAG cascades
- `intermediate_sync_groups` — (optional) list of sync-group names discovered at intermediate levels
- `deferred_sync_groups` — (optional) list of sync-group names with unresolved divergence
- `merge_conflict_peer` — (optional) rel_path of peer with active merge conflict
- `merge_conflict_primary` — (optional) rel_path of the primary that committed before conflict

### Operations

| Command | Effect |
|---------|--------|
| `grove cascade <path>` | Create state, execute chain |
| `grove cascade --sync-group NAME` | Create state from sync-group name, execute DAG |
| `grove cascade --continue` | Load state, resume from paused repo |
| `grove cascade --abort` | Load state, `git reset --hard` each committed repo, delete state |
| `grove cascade --status` | Load and display state |

## Sync-Group Awareness

### Consistency Check

Before building the cascade chain, grove checks if the target submodule belongs to a sync group. If it does, all instances of that sync group must be at the same commit.

- **All consistent**: cascade proceeds in DAG mode (all instances)
- **Inconsistent**: cascade fails, suggesting `grove sync <group>` first
- **Inconsistent + `--force`**: cascade proceeds with a warning

### DAG Cascading

When the leaf is a sync-group submodule, cascade builds a DAG instead of a linear chain:

1. Discover all instances of the sync group
2. Build individual cascade chains from each instance to root
3. Merge chains into a deduplicated set keyed by repo path
4. Compute parent-relative `child_rel_paths` for each entry
5. Sort by depth ascending (leaves first, root last)
6. Assign roles: instances = leaf, root = root, others = intermediate

**Example:** If `libs/common` exists in `frontend/`, `backend/`, and `shared/`:

```
Execution order (depth ascending):
  depth 0: frontend/libs/common (leaf), backend/libs/common (leaf), shared/libs/common (leaf)
  depth 1: frontend (intermediate), backend (intermediate), shared (intermediate)
  depth 2: . (root)
```

Each intermediate stages the correct parent-relative child path (e.g., `frontend` stages `libs/common`, root stages `frontend`, `backend`, `shared`).

### Intermediate Sync-Group Expansion

Sync-group detection happens not just at the leaf level, but at **all** levels during plan building. If an intermediate repo belongs to a sync group, the cascade plan is expanded to include peer instances and their parent chains.

**Algorithm (fixed-point iteration):**
1. For each non-leaf entry in the plan, check if it belongs to a sync group
2. If yes, discover all instances → add peers as "sync targets"
3. Build cascade chains from each peer's parent upward to root
4. Repeat until no new repos are added (handles nested sync groups)

**Primary/sync-target pattern:**
- One instance per sync group is the "primary" (commits normally via test+stage+commit)
- Other instances are "sync targets" (skipped during processing, synced to primary's SHA after it commits)
- Within the same depth, primaries are sorted before sync targets

**Linear-to-DAG promotion:** A cascade that starts as linear (non-sync-group leaf) becomes a DAG if any intermediate is a sync-group member.

### Intermediate Divergence Resolution

When intermediate sync-group instances have diverged commits (neither is ancestor of the other):

**Phase 1 — Pre-cascade auto-resolve:** After building the plan, check each intermediate sync group for divergence. Attempt auto-merge for all groups. Groups that merge cleanly have all instances synced to the merged commit. Groups with conflicts are flagged as "deferred."

**Phase 2 — Dynamic resolution:** When the cascade commits a primary whose peers belong to a deferred group, the merge is attempted with the primary's new commit (which includes the child pointer update) against each peer's current commit. If the merge has a conflict, cascade pauses with instructions to resolve.

## Algorithm

### Linear Chain (non-sync-group)

1. **Discover cascade chain:** From the given submodule path, walk up `Path.parents` to find all ancestor repos. Result: `[leaf, parent1, ..., root]`.

2. **Process each repo bottom-up:**
   a. Record `pre_cascade_head` (for rollback).
   b. Stage child submodule pointer (if not leaf).
   c. Run applicable test tiers based on role and flags.
   d. On failure: run auto-diagnosis if applicable, save state, exit with code 1.
   e. On all tiers passing: commit pointer update, advance to next repo.

3. **On completion:** Delete state file, print summary.

### DAG (sync-group)

1. **Build unified plan:** Discover all sync-group instances, build per-instance chains, merge and deduplicate, sort by depth.

2. **Process each repo in depth order (ascending):**
   a. Record `pre_cascade_head`.
   b. Stage all `child_rel_paths` (may be multiple for repos with several sync-group children).
   c. Run applicable test tiers.
   d. On failure: save state with `is_dag=True` and `sync_group_name`, exit.
   e. On success: commit, advance.

3. **On completion:** Delete state file, print summary.

## Configuration

New `[cascade]` section in `.grove.toml`:

```toml
[cascade]
local-tests = "pytest tests/unit -x"
contract-tests = "pytest tests/contracts -x"
integration-tests = "pytest tests/integration"
system-tests = "pytest tests/system"

# Per-repo overrides
[cascade.overrides."services/api"]
local-tests = "npm test -- --unit"
integration-tests = "npm test -- --integration"

[cascade.overrides."."]
local-tests = "make test-unit"
system-tests = "make test-e2e"
```

Override resolution: per-repo override → default tier command → None (skip).

## Reuse from Existing Code

| Component | Source |
|-----------|--------|
| State persistence | `worktree_merge.py` MergeState pattern |
| Repo discovery | `repo_utils.discover_repos_from_gitmodules()` |
| Git operations | `repo_utils.RepoInfo.git()`, `run_git()` |
| File locking | `filelock.atomic_write_json()`, `locked_open()` |
| Journal logging | Same monthly-rotated log pattern as merge |
