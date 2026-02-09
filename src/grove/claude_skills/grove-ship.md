---
name: grove-ship
description: Health check all submodules then push changes bottom-up
---

# grove-ship: Health Check + Push

Run a health check on all submodules, report any issues, and push committed changes if everything is clean.

## Workflow

### Step 1: Run verbose health check

Run `grove check -v` and capture the output. Report the results to the user.

- Exit code 0 means all checks passed.
- Exit code 1 means issues were found (detached HEAD, sync groups out of sync, etc.).

### Step 2: Evaluate health check results

If `grove check` reports issues:
- Summarize each issue clearly (which submodules are detached, which sync groups are out of sync).
- For **detached HEAD** issues: suggest `cd <submodule> && git checkout <branch>`.
- For **sync group out of sync** issues: suggest running `/grove-sync` to fix before shipping.
- **Stop here.** Do not proceed to push. Tell the user to fix issues first, then re-run `/grove-ship`.

If all checks passed, proceed to Step 3.

### Step 3: Dry-run push preview

Run `grove push --dry-run` to preview what would be pushed.

- If nothing to push, report that to the user and stop.
- Otherwise, show the user what repositories will be pushed and how many commits each has ahead.

### Step 4: Execute push

Run `grove push` to push all committed changes bottom-up through the submodule hierarchy.

- If the push succeeds (exit code 0), report success.
- If the push fails (exit code 1), report the failure and suggest:
  - Check remote connectivity: `git remote -v`
  - Check authentication: `ssh -T git@github.com`
  - Try `grove push --force` only for recovery scenarios.

### Step 5: Post-push verification

Run `grove check` one final time to confirm everything is clean after pushing.

Report the final status to the user.
