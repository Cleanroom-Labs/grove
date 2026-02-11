# Nested Submodules and Git Worktrees: A Developer's Guide

This project uses a three-level nested submodule architecture to manage documentation across multiple independent projects. This post explains why, what that means in practice, and how git worktrees make the workflow survivable.

## The Architecture

The full structure looks like this:

```
my-project/
├── docs-aggregator/
│   ├── project-alpha-docs/
│   │   └── source/shared-theme/
│   ├── project-beta-docs/
│   │   └── source/shared-theme/
│   ├── project-gamma-docs/
│   │   └── source/shared-theme/
│   └── source/shared-theme/
└── shared-theme/
```

Three levels of nesting, each serving a distinct purpose:

1. **Level 1: Root → Aggregator.** The root repo pins a single submodule (`docs-aggregator`) that aggregates all project documentation into one build.
2. **Level 2: Aggregator → Project Docs.** The aggregator contains submodules for each project's documentation (`project-alpha-docs`, `project-beta-docs`, `project-gamma-docs`), plus a copy of the shared theme.
3. **Level 3: Project Docs → Theme.** Each project doc repo embeds the shared theme (`shared-theme`) so it can build independently without the aggregator.

The theme submodule appears at every level. This is intentional—it allows each project to build its own documentation in isolation while maintaining consistent styling.

## Core Workflow Principle

The main checkout of the root repository is the central merge point — not a development environment. All development happens in worktrees created with `grove worktree add`. When work is complete, changes are merged back into main with `grove worktree merge`, and only then pushed upstream with `grove push`. The main checkout stays clean; it exists solely to integrate parallel work.

The rest of this guide explains the mechanics behind this workflow.

## Complexities of Deeply Nested Submodules

Working with this setup day-to-day surfaces a few recurring realities.

### Detached HEAD is the Default State

Every submodule checks out a specific commit, not a branch. Running `git status` inside any submodule shows `HEAD detached at <sha>`. This is correct behavior, not an error. To make changes, you `git checkout main` first, do your work, commit, then update the parent repo to point to the new commit.

### Three-Commit Propagation

A change to project documentation requires three separate commits to reach the website:

1. Commit in the project docs repo (e.g., `project-beta-docs`)
2. Commit in `docs-aggregator` to update the submodule pointer
3. Commit in `my-project` to update its submodule pointer

Each commit records a new SHA, and the parent must be updated to reference it. There's no shortcut. The `grove push` command automates this, but the fundamental mechanics remain.

### Theme Duplication

The `shared-theme` submodule is referenced six times across the tree—once at the root, once inside the aggregator, and once inside each of the three project doc repos. They all point to the same source repo, but each is an independent checkout. Updating the theme means updating it in every location, which is handled by `grove sync`.

### Submodule Drift

If you work in a project docs repo directly (outside the website tree), its commits advance independently. The parent repos still point to the old SHAs until you explicitly update them. `git status` in the parent will show `modified: <submodule> (new commits)`, which is easy to miss. The `grove check` command helps catch this.

## Advantages

Despite the complexity, this architecture provides real benefits.

**Explicit version coupling.** Every level records exact SHAs. You always know which version of every project's docs a given website release includes. There's no ambiguity about what was deployed.

**Independent ownership.** Each project team owns their documentation repo. They can commit, review, and tag releases without coordinating with other teams or the website maintainer.

**Standalone builds.** Because each project embeds the theme, any project can build its own documentation in isolation. You don't need the full website tree to work on a single project's docs.

**Dual-homing.** Code repositories can include their documentation as a submodule. The same docs repo that lives inside the aggregator can also live inside the code repo, so documentation stays close to the code it describes.

## Disadvantages

**Steep learning curve.** Git submodules are already unfamiliar territory for many developers. Three levels of nesting compounds this. Detached HEADs, recursive updates, and multi-commit propagation are confusing until you've internalized the model.

**Multi-step propagation.** Every change requires touching multiple repos. Even with automation scripts, this is slower than editing files in a monorepo.

**Tooling assumptions.** Most git GUIs, IDE integrations, and CI systems assume a single-repo workflow. Nested submodules expose edge cases and gaps in tooling. Some operations that should be simple (like "show me what changed") require running commands at multiple levels of the tree.

**Slow clone and init.** `git clone --recursive` must descend through every level and initialize every submodule. The time scales with the number of nesting levels and total submodule count.

## Git Worktrees to the Rescue

A common scenario: you're working on a documentation update and need to check something on a different branch—maybe to compare output or cherry-pick a fix. With submodules, switching branches is expensive. `git checkout other-branch` in the parent doesn't automatically update submodules, and `git submodule update --recursive` can take a while and clobber local changes.

Git worktrees solve this by letting you check out multiple branches simultaneously in separate directories. Each worktree shares the same `.git` object store, so it's lightweight. But there's a catch: `git worktree add` doesn't initialize submodules. You'd need to manually run `git submodule update --init --recursive` in the new worktree, which can fail or require unnecessary network round-trips.

The `grove worktree add` command handles this. It creates the worktree, then recursively initializes submodules using the main worktree's existing checkout as a reference, avoiding redundant fetches. Original submodule URLs are restored afterward.

Creating a new worktree with a new branch:

```bash
grove worktree add --local-remotes my-feature ../my-project-my-feature
```

Creating a worktree on an existing branch (without `-b`):

```bash
grove worktree add --local-remotes --checkout existing-branch ../my-project-wt2
```

### Why `--local-remotes` Is the Default for Feature Worktrees

The `--local-remotes` flag keeps submodule remotes in the new worktree pointing to the main worktree's local copies rather than the upstream remote (e.g., GitHub). This means any `git push` inside a worktree submodule stays on-machine — changes propagate between worktrees through the shared local filesystem, not through the network.

This matches the intended workflow: develop in a feature worktree, merge back into main locally with `grove worktree merge`, and only then push everything upstream with `grove push` from the main worktree. Without `--local-remotes`, a `git push` inside a worktree submodule would go directly to GitHub, bypassing the merge-then-push workflow.

If you need worktree submodules to push directly to upstream (e.g., for CI integration from a worktree), omit the flag.

Removing a worktree (also runs `git worktree prune`):

```bash
grove worktree remove ../my-project-my-feature
```

Force-removing a worktree with uncommitted changes:

```bash
grove worktree remove --force ../my-project-my-feature
```

## Propagating Changes Through the Repository

Once you've committed a change inside a nested submodule, it needs to bubble up through every parent—each level requires a separate commit and push, and the order matters. The `grove push` command automates this. It discovers every repo in the hierarchy, performs a topological sort (children before parents), validates that each repo is on a branch with no uncommitted changes, and pushes them in the correct order.

```bash
# Preview what would be pushed
grove push --dry-run

# Push all repos with unpushed commits
grove push

# Skip validation for recovery scenarios
grove push --force
```

The topological ordering matters because pushing a parent before its children would create a state where the parent references commits that don't exist on the remote yet.

## Syncing the Theme

When the shared theme changes—a new color, an updated layout, a bug fix—it needs to be updated in every location it appears. The `grove sync` command handles this end-to-end:

1. Resolves the target commit (defaults to the latest on `main` in the standalone theme repo, or accepts a specific SHA)
2. Discovers all theme submodule locations by parsing `.gitmodules` at every level
3. Validates that parent repos are in sync with their remotes
4. Updates each theme submodule to the target commit
5. Commits the changes bottom-up through the hierarchy
6. Pushes everything (unless `--no-push` is specified)

```bash
# Sync all theme instances to latest, commit, and push
grove sync

# Sync to a specific commit
grove sync abc1234

# Preview without making changes
grove sync --dry-run

# Commit but don't push
grove sync --no-push

# Also check for stale generated files (e.g., icons) after syncing
grove sync --verify
grove sync --rebuild  # auto-regenerate stale files
```

## Merging Worktree Changes Back

After finishing work in a worktree, you need to integrate the changes into your main branch. The workflow is straightforward:

```bash
# From the main worktree
cd ~/Projects/my-project

# Merge the feature branch
git merge my-feature

# If the feature touched submodule pointers, update them
git submodule update --recursive

# Push everything through the hierarchy
grove push
```

If you have multiple worktrees with changes to merge, do them sequentially. Each merge may update submodule pointers, and you want to resolve any conflicts at each step rather than accumulating them:

```bash
git merge feature-a
git submodule update --recursive
# verify, then continue

git merge feature-b
git submodule update --recursive
# resolve conflicts if feature-b touched the same submodules as feature-a

grove push
```

## Managing Worktrees and Branches

Worktrees accumulate if you don't clean them up. A few commands to keep things tidy:

```bash
# See all worktrees
git worktree list

# Remove a worktree after you're done with it (also prunes stale entries)
grove worktree remove ../my-project-my-feature

# Delete the branch after merging
git branch -d my-feature
```

A naming convention helps keep things organized. Use the project directory name as a base and append the branch name as a suffix:

```
~/Projects/
├── my-project/                  # main worktree (main branch)
├── my-project-wt1/              # worktree 1 (feature branch)
├── my-project-wt2/              # worktree 2 (another feature)
└── my-project-hotfix/           # worktree for a quick fix
```

Sibling directories make it easy to `cd ../<other-worktree>` and keep everything visible in your file manager. The `-wt1`, `-wt2` pattern works well for short-lived worktrees; use descriptive names for longer-lived ones.

When you're done with a batch of work, clean up in one pass:

```bash
git worktree list                       # see what's active
grove worktree remove ../my-project-wt1
grove worktree remove ../my-project-wt2
git branch -d feature-a feature-b      # delete merged branches
```

## Parallel Development with AI Coding Agents

Worktrees unlock a powerful workflow when combined with AI coding agents: true parallel development on a single repository.

The setup is simple. You have three independent tasks—say, updating component A, refactoring module B, and fixing a theme issue. Instead of working through them sequentially, you create a worktree for each:

```bash
cd ~/Projects/my-project
grove worktree add --local-remotes update-deploy-docs   ../my-project-wt1
grove worktree add --local-remotes expand-whisper-docs  ../my-project-wt2
grove worktree add --local-remotes fix-theme-spacing    ../my-project-wt3
```

Each worktree has its own fully initialized checkout with all submodules at every level. Now you launch a coding agent in each one—three separate terminal sessions, three instances of Claude Code, each pointed at a different directory:

```bash
# Terminal 1
cd ../my-project-wt1 && claude

# Terminal 2
cd ../my-project-wt2 && claude

# Terminal 3
cd ../my-project-wt3 && claude
```

The agents work simultaneously without interfering with each other. Each operates in its own worktree with its own branch, its own working directory, and its own submodule state. There are no lock conflicts because git worktrees are designed for concurrent access to the same repository.

When the agents finish, you review each worktree's changes, then merge sequentially from the main worktree:

```bash
cd ~/Projects/my-project

git merge update-deploy-docs
git submodule update --recursive

git merge expand-whisper-docs
git submodule update --recursive

git merge fix-theme-spacing
git submodule update --recursive

# Push everything in one pass
grove push
```

Then clean up:

```bash
grove worktree remove ../my-project-wt1
grove worktree remove ../my-project-wt2
grove worktree remove ../my-project-wt3
git branch -d update-deploy-docs expand-whisper-docs fix-theme-spacing
```

The key insight is that worktrees give each agent a complete, isolated environment while the shared `.git` object store means every branch and commit is immediately visible from the main worktree when it's time to merge. You get the parallelism of multiple clones without the disk cost or the hassle of syncing between them.

## Closing Thoughts

Nested submodules are a power tool. They solve real problems—version coupling, independent ownership, standalone builds—but they demand an understanding of git's object model that goes beyond typical usage. Git worktrees complement submodules well by removing the need to context-switch destructively. Together, they make it practical to maintain a multi-project documentation platform where each piece can evolve independently while the whole remains coherent.
