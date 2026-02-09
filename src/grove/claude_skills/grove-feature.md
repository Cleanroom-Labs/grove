---
name: grove-feature
description: Create a feature branch worktree with submodule initialization
---

# grove-feature: Feature Branch Setup

Create a new worktree for a feature branch with full submodule initialization.

`$ARGUMENTS` should contain `<branch-name> <path>`. If only a branch name is provided, default the path to `../<branch-name>-wt`. If no arguments are provided, ask the user for the branch name.

Example usage: `/grove-feature my-feature ../my-feature-wt`

## Workflow

### Step 1: Parse arguments

Extract `<branch-name>` and `<path>` from `$ARGUMENTS`.

- Two arguments: use as branch and path.
- One argument: use as branch, default path to `../<branch-name>-wt`.
- No arguments: ask the user for the branch name.

### Step 2: Pre-flight checks

1. Run `git branch --list <branch-name>` to check if the branch already exists locally.
   - If it exists, inform the user and ask whether to use `--checkout` (check out the existing branch) instead of creating a new one.
2. Check that the target path does not already exist.
   - If it exists, tell the user and stop.

### Step 3: Create the worktree

Run the appropriate command:

- **New branch:** `grove worktree add <branch-name> <path>`
- **Existing branch:** `grove worktree add --checkout <branch-name> <path>`

This creates the worktree and recursively initializes all submodules using the main worktree as a reference. It also copies local git config (user.name, user.email, signing settings).

If the command fails:
- Report the error output.
- If submodule initialization failed, suggest `git submodule update --init --recursive` inside the worktree.
- If worktree creation failed, check for branch name conflicts or invalid paths.

### Step 4: Verify the setup

1. `git -C <path> branch --show-current` -- confirm correct branch.
2. `git -C <path> submodule status --recursive` -- confirm all submodules initialized (no `-` prefixes).

### Step 5: Report status

Summarize:
- Worktree path (absolute)
- Branch name
- Number of submodules initialized
- Remind: remove later with `grove worktree remove <path>`
- Remind: merge back with `grove worktree merge <branch-name>` from the main worktree when done
