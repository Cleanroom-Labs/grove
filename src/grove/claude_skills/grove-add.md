---
name: grove-add
description: Create a feature branch worktree with submodule initialization
---

# grove-add: Worktree Branch Setup

Create a new worktree for a feature branch with full submodule initialization.

`$ARGUMENTS` should contain `<branch-name> <path>`. If only a branch name is provided, default the path to `../<branch-name>-wt`. If no arguments are provided, ask the user for the branch name.

Example usage: `/grove-add my-feature ../my-feature-wt`

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

### Step 2.5: Determine worktree flags

Check `.grove.toml` for a `[worktree]` section:
- If `copy-venv = true` is configured, the CLI applies `--copy-venv` automatically. No extra flag needed in the command below.
- If no `[worktree]` section exists, check the project for Python indicators:
  - Directories: `.venv/`, `venv/`, `.direnv/python-*`
  - Files: `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile`
  - If indicators are found, add `--copy-venv` to the command in Step 3.
  - Suggest adding `[worktree]` with `copy-venv = true` to `.grove.toml` for future runs.

### Step 3: Create the worktree

Run the appropriate command (add `--copy-venv` if determined in Step 2.5):

- **New branch:** `grove worktree add <branch-name> <path>`
- **Existing branch:** `grove worktree add --checkout <branch-name> <path>`

This creates the worktree and recursively initializes all submodules using the main worktree as a reference. By default, submodule remotes point to the main worktree so pushes stay on-machine until you merge back and push from the main worktree.

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

## Customization

Project-level defaults can be set in `.grove.toml` so they apply automatically without editing this skill:

```toml
[worktree]
copy-venv = true    # Auto-copy Python venv on worktree creation
```

Other flags can be adjusted in the commands above after installing with `grove claude install`. For example:

- Add `--no-local-remotes` if you want worktree submodules to push directly to upstream remotes.
