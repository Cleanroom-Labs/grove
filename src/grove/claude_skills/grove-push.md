---
name: grove-push
description: Push changes with optional filtering by path, sync group, or cascade chain
---

# grove-push: Filtered Push

Push committed changes through nested submodules bottom-up, with optional filtering to target specific repos.

`$ARGUMENTS` may contain positional paths and/or flags:
- `/grove-push` -- push all repos (same as `/grove-ship` but without the health check)
- `/grove-push frontend backend` -- push specific repos by relative path
- `/grove-push --sync-group common` -- push parent repos of a sync group
- `/grove-push --cascade libs/common` -- push repos in the cascade chain
- Filters compose with union semantics (matching ANY filter is included)

## Workflow

### Step 1: Dry-run preview

Run `grove push $ARGUMENTS -n` to preview what would be pushed.

Report to the user:
- How many repos discovered, how many targeted by filters
- Which repos have pending changes
- Which filter mode is active (path, sync-group, cascade, or all)

If nothing to push, report that and stop.

### Step 2: Confirm with user

Show key facts:
- Number of repos to push and their names
- Filter mode and scope
- Mention that sync-group consistency checks are skipped when filters are active

### Step 3: Execute push

Run `grove push $ARGUMENTS`.

If validation fails (uncommitted changes, etc.):
- Report the specific repos and issues
- Suggest `-f`/`--skip-checks` only for recovery scenarios

### Step 4: Post-push verification

Run `grove check` to verify grove health after pushing.

## Choosing the Right Filter

Guide the user based on their workflow:

- **Just finished a cascade?** Use `--cascade <path>` to push exactly the repos that were cascaded.
- **Synced a sync group?** Use `--sync-group <name>` to push the parent repos that got new submodule pointers.
- **Working on specific repos?** List them positionally: `grove push frontend backend`.
- **Want everything?** Use `/grove-ship` instead (includes health check).

## Error Handling

- **Unknown path**: list available repos from discovery
- **Unknown sync group**: list available groups from `.grove.toml`
- **Invalid cascade path**: verify the path points to a recognized repo
- **Validation failures**: suggest fixing issues or `-f`/`--skip-checks` for recovery
