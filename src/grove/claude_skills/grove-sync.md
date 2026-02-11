---
name: grove-sync
description: Sync submodule sync groups with dry-run preview and verification
---

# grove-sync: Submodule Sync

Synchronize submodule sync groups with a preview, confirmation, and verification cycle.

`$ARGUMENTS` may contain `[group] [commit]` and/or flags. Both positional args are optional.

By default, sync resolves the target from the most advanced **local** submodule instance (local-first). Use `--remote` to resolve from the remote instead.

When instances have **diverged** (no linear ordering), sync automatically attempts a merge. If conflicts arise, the merge pauses for manual resolution.

Example usages:
- `/grove-sync` -- sync all groups to most advanced local instance
- `/grove-sync common` -- sync just the "common" group
- `/grove-sync common abc1234` -- sync "common" to a specific commit
- `/grove-sync --remote` -- sync all groups to remote HEAD
- `/grove-sync --continue` -- resume after resolving merge conflicts
- `/grove-sync --abort` -- cancel an in-progress merge
- `/grove-sync --status` -- show current merge progress

## Workflow

### Step 1: Dry-run preview

Run `grove sync $ARGUMENTS --dry-run` to preview what would happen.

Report to the user:
- Target commit SHA and source
- Which submodule locations will be updated (current -> target SHA)
- Which locations are already at the target
- How many parent repos will need commits and pushes

If no sync groups are configured, tell the user `.grove.toml` needs sync group configuration and stop.

If validation failures occur (parent repos out of sync with remotes), warn and suggest:
- Pull latest in affected repos first
- Use `--force` only for recovery scenarios

If all submodules are already at target, report "Nothing to sync" and stop.

### Step 2: Confirm with user

Show key facts and ask to confirm:
- Target commit source (local tip vs remote vs explicit SHA)
- Number of submodules to update
- Number of commits to make
- Number of repos to push
- Mention `--no-push` as an option if they want to commit locally only
- Mention `--remote` if they want to sync from the remote instead of the local tip

### Step 3: Execute sync

Run `grove sync $ARGUMENTS` (or with `--no-push` if requested).

Monitor output for successful updates, push failures, or errors.

### Step 4: Verify

Run `grove check -v` to verify all sync groups are consistent.

Report final status. If issues remain, explain and suggest remediation.

## Divergence Merge

When sync detects diverged instances (different commits with no linear ordering), it automatically attempts to merge them:

1. Selects a workspace (standalone repo if configured, otherwise first instance)
2. Fetches all diverged commits into the workspace
3. Attempts a merge (two-way for 2 commits, octopus for 3+)

**Clean merge**: sync continues normally using the merged commit as target.

**Conflict**: sync pauses with instructions. The user must:
1. Resolve conflicts in the workspace directory shown
2. Stage resolved files (`git add`)
3. Run `grove sync --continue` to commit and resume

**Abort**: run `grove sync --abort` to cancel the merge and restore the workspace.

**Status**: run `grove sync --status` to see merge progress and which commits are involved.

## Error Handling

- **Unknown sync group**: list available groups from `.grove.toml`
- **Diverged local instances**: sync now auto-merges. If merge conflicts, guide user through `--continue`/`--abort`
- **"A sync merge is already in progress"**: direct to `--continue`, `--abort`, or `--status`
- **Validation failures**: suggest `git pull` or `grove sync --force`
- **Push failures**: suggest `grove push --sync-group <name>` as follow-up
- **Network errors** (with `--remote`): suggest dropping `--remote` to use local-first, or providing a specific commit SHA
