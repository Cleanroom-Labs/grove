---
name: grove-merge
description: Merge a feature branch bottom-up across all submodules with conflict handling
---

# grove-merge: Worktree Merge

Merge a feature branch into the current branch across all repositories in the submodule tree, processing leaves first (topological order). Supports starting, continuing, aborting, and checking status.

`$ARGUMENTS` should contain one of:
- `<branch>` -- start a new merge
- `--continue` -- resume after resolving conflicts or fixing tests
- `--abort` -- abort and restore all repos to pre-merge state
- `--status` -- show current merge progress

## Starting a New Merge

### Step 1: Check for in-progress merge

Run `grove worktree merge --status`.

- If a merge is already in progress, inform the user and suggest `--continue`, `--abort`, or `--status`.
- If no merge in progress, proceed.

### Step 2: Dry-run prediction

Run `grove worktree merge <branch> --dry-run`.

Report:
- How many repositories discovered
- Which repos need merging (and commit count)
- Which repos will be skipped (already merged, branch not found, etc.)
- Conflict predictions
- Structural consistency warnings

If no repos need merging, report "Nothing to merge" and stop.

### Step 3: Confirm and execute

Show the prediction and ask user to confirm. Mention:
- Total repos to merge
- Expected conflicts (if any)
- Test commands that will run (from `.grove.toml`)

Run `grove worktree merge <branch>`.

### Step 4: Handle merge pause

If the merge pauses (exit code 1):

**Conflict:**
- Show which files have conflicts and in which repo.
- If any conflicting files are submodule pointers, grove prints `git update-index --cacheinfo` commands for both sides (ours/theirs). Relay these to the user — standard `git checkout --ours/--theirs` does **not** work for submodule pointers.
- Tell the user to resolve conflicts in the specified repo path.
- After resolving: `/grove-merge --continue`

**Test failure:**
- Show which test command failed and in which repo.
- Tell the user to fix the issue.
- After fixing: `/grove-merge --continue`

### Step 5: Report completion

When merge finishes (exit code 0):
- Number of repos merged and skipped
- Suggest `grove check` to verify health
- Suggest `/grove-ship` to push the merged changes

## Continuing a Merge (`--continue`)

1. Run `grove worktree merge --continue`.
2. If unresolved conflicts remain, report which files still need resolving.
3. If tests fail again, report the failure.
4. If it succeeds, report completion as above.

## Aborting a Merge (`--abort`)

1. Run `grove worktree merge --abort`.
2. This restores all repositories to their pre-merge state.
3. Report which repos were restored and confirm the abort.

## Checking Status (`--status`)

1. Run `grove worktree merge --status`.
2. Report: branch being merged, per-repo status (merged/skipped/paused/pending).
3. Suggest next actions based on state.

## Error Handling

- **"A merge is already in progress"**: direct to `--continue`, `--abort`, or `--status`
- **"has uncommitted changes"**: commit or stash changes first
- **"detached HEAD (not on a branch)"**: a submodule is not on a named branch. Run `grove worktree checkout-branches` in the worktree to fix, then retry the merge.
- **State corruption**: suggest `--abort` to clean up and start fresh
- **Structural divergence warnings**: non-blocking but flag for manual intervention

## Submodule Pointer Conflicts

Submodule entries are stored as gitlinks (mode `160000`) — just SHA pointers, not files. When these conflict:

- `git checkout --ours/--theirs <submodule>` does **not** work (it tries to write file content, but there is none)
- Grove prints the exact `git update-index --cacheinfo 160000,<sha>,<path>` commands for both sides

**Resolution workflow:**
1. Pick which submodule commit to keep (ours = current HEAD, theirs = branch being merged)
2. Run the `git update-index --cacheinfo` command grove printed
3. Run `/grove-merge --continue`

**Common scenario:** The feature branch only exists in the root repo, not in submodules. During merge, submodule pointers conflict because main and the branch updated them independently. Choose whichever pointer is newer, or check with `git -C <submodule> log --oneline <sha1>..<sha2>` to compare.
