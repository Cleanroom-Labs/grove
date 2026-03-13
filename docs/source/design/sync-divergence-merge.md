# Sync Divergence Merge Design

When `grove sync` detects that sync-group instances have diverged (different commits with no linear ordering), it automatically attempts to merge them instead of failing.

## Problem Statement

Diverged instances occur when different developers commit to different instances of the same sync-group submodule. Since there's no single "most advanced" instance (no linear ordering), `resolve_local_tip()` returns None.

Previously, this was a hard failure requiring manual intervention. Now, grove merges the diverged commits automatically, pausing for manual resolution only when there are conflicts.

## Merge Algorithm

1. **Collect diverged commits**: gather unique SHAs and their source paths from all instances.

2. **Select workspace**: use the standalone repo if configured (preferred — avoids modifying an instance), otherwise use the first instance.

3. **Fetch commits**: fetch all diverged commits into the workspace so they're available locally.

4. **Find merge-base**: compute the merge-base of the diverged commits.

5. **Merge**:
   - 2 commits: standard two-way merge
   - 3+ commits: octopus merge

6. **Handle result**:
   - **Clean merge**: return `(merged_sha, workspace, description)` — sync continues using the merged commit as the target.
   - **Conflict**: save state, print resolution instructions, return None — sync pauses.

## Pause/Resume State Machine

Follows the same pattern as `cascade.py` and `worktree_merge.py`.

### State file

Location: `.git/grove/sync-merge-state.json`

Contents:
- `group_name` — the sync group being merged
- `started_at` — ISO 8601 timestamp
- `workspace_path` — where the merge is happening
- `base_commit` — merge-base of all diverged commits
- `diverged_commits[]` — list of `{sha, source_path, status}` entries
- `merged_sha` — set when merge completes
- `conflict_sha` — the conflicting commit (for two-way merges)

### Operations

| Command | Effect |
|---------|--------|
| `grove sync` | Detects divergence, attempts merge |
| `grove sync --continue` | Check conflicts resolved, commit merge, output merged SHA |
| `grove sync --abort` | `git merge --abort`, restore workspace, delete state |
| `grove sync --status` | Display merge state and instructions |

## Workspace Selection

The standalone repo (configured in `.grove.toml` as `standalone-repo`) is preferred as the merge workspace because:

- It avoids modifying any sync-group instance during the merge
- The standalone repo is a full clone specifically for operations like this
- If no standalone repo exists, the first discovered instance is used

## Interaction with Cascade

After a successful sync merge, the user can cascade the merged commit through all parent chains:

```bash
grove sync common              # merges diverged instances, syncs to merged commit
grove cascade libs/common      # cascade from all instances (DAG mode)
grove push --cascade libs/common  # push affected repos
```

## Dry Run

When `grove sync --dry-run` encounters diverged instances, it reports what would happen without actually merging:

```
(dry-run) Would attempt merge in workspace.
```
