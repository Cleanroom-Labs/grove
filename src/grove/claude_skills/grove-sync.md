---
name: grove-sync
description: Sync submodule sync groups with dry-run preview and verification
---

# grove-sync: Submodule Sync

Synchronize submodule sync groups with a preview, confirmation, and verification cycle.

`$ARGUMENTS` may contain `[group] [commit]`. Both are optional.

Example usages:
- `/grove-sync` -- sync all groups to latest
- `/grove-sync common` -- sync just the "common" group
- `/grove-sync common abc1234` -- sync "common" to a specific commit

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
- Number of submodules to update
- Number of commits to make
- Number of repos to push
- Mention `--no-push` as an option if they want to commit locally only

### Step 3: Execute sync

Run `grove sync $ARGUMENTS` (or with `--no-push` if requested).

Monitor output for successful updates, push failures, or errors.

### Step 4: Verify

Run `grove check -v` to verify all sync groups are consistent.

Report final status. If issues remain, explain and suggest remediation.

## Error Handling

- **Unknown sync group**: list available groups from `.grove.toml`
- **Validation failures**: suggest `git pull` or `grove sync --force`
- **Push failures**: suggest `grove push` as follow-up
- **Network errors**: suggest providing a specific commit SHA
