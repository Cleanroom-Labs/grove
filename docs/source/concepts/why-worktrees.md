# Why Worktrees?

Git worktrees let you check out multiple branches of the same repository simultaneously, each in its own directory. They share the same `.git` object store, so creating one is nearly instant — no cloning, no duplicating history. This document explains why worktrees are the natural development primitive for submodule-heavy projects, why they're painful without tooling, and how Grove makes them practical.

## Why You Want Worktrees

### Parallel Development

The traditional git workflow is serial: you're on a branch, you finish or stash, you switch to another branch, you work, you switch back. For a single-repo project this is manageable. For a project with nested submodules, branch-switching is expensive — `git checkout` in the parent doesn't update submodules, and `git submodule update --recursive` can take minutes and clobber local state.

Worktrees eliminate this entirely. Each worktree is a separate directory with its own working tree and index. You can have `feature-a` in one directory and `feature-b` in another, both fully functional, both with all submodules initialized. Switch between them with `cd`.

```
~/Projects/
├── my-project/              main (merge hub)
├── my-project-feature-a/    feature-a branch
├── my-project-feature-b/    feature-b branch
└── my-project-hotfix/       hotfix branch
```

There's no stashing, no half-finished commits, no "I need to switch back to main but I'm in the middle of something." Each piece of work has its own directory.

### Agentic Development

AI coding agents need isolated environments. If you're running multiple agents — one adding a feature, another fixing a bug, a third refactoring a module — they can't share a working directory. They'd overwrite each other's changes, trigger each other's file watchers, and produce merge conflicts in real time.

Worktrees give each agent a complete, independent checkout. Three agents, three worktrees, three terminals, zero interference:

```bash
grove worktree add --local-remotes ../my-project-feature-a  feature-a
grove worktree add --local-remotes ../my-project-refactor-b refactor-b
grove worktree add --local-remotes ../my-project-fix-theme  fix-theme
```

Each agent operates in its own directory with its own branch, its own submodule state, and its own index. When they finish, you review each worktree's changes and merge them sequentially from the main checkout. The main checkout is the integration point — it's where parallel work becomes serial, deliberate, and tested.

This pattern scales to as many agents as you have tasks. The limiting factor is review bandwidth, not tooling.

### Hotfixes

Your team is mid-feature when a production bug surfaces. Without worktrees, you'd stash your in-progress work (hoping the stash applies cleanly later), switch to the release branch, fix the bug, push, then switch back and pop the stash. With nested submodules, each of those switches involves recursive submodule updates.

With worktrees:

```bash
grove worktree add ../my-project-hotfix release-1.2
cd ../my-project-hotfix
# fix the bug, test, commit
cd ../my-project
grove worktree merge release-1.2
grove push
grove worktree remove ../my-project-hotfix
```

Your feature work is untouched. The hotfix happens in a separate directory. When it's done, you merge it into main and clean up.

### Review and Comparison

Sometimes you need to see two versions of the codebase side by side — comparing the output of a feature branch against main, checking whether a refactor changed behavior, or reviewing a colleague's branch while keeping your own work open.

Worktrees let you have both checked out simultaneously. No screenshots, no `git stash && git checkout && look && git checkout - && git stash pop`. Just two directories.

## The Submodule Problem

Vanilla `git worktree add` creates a new worktree with the parent repository checked out. It does not initialize submodules. For a project with nested submodules, this means:

1. **You run `git submodule update --init --recursive` in the new worktree.** This hits the network for every submodule, re-downloading history that already exists on disk in the main worktree. For a project with 20 submodules across three levels of nesting, this can take minutes.

2. **Submodule URLs might not be accessible.** If you're working offline, behind a VPN, or the upstream remote has moved, the init fails. The submodule data is right there on your local disk — but git doesn't know to look there.

3. **Submodule configuration isn't copied.** Local git config (user name, email, signing settings) in the main worktree's submodules doesn't carry over. You have to reconfigure each submodule manually.

4. **Python virtual environments don't transfer.** If you have a `.venv` in your main worktree, the new worktree doesn't have one. You either rebuild it (slow) or try to symlink it (fragile, path-dependent).

5. **Merging across worktrees is manual and error-prone.** When you want to merge a feature branch back into main, you need to merge at every level of the submodule tree. Miss a level and the parent's submodule pointer is stale. Merge in the wrong order and you get cascading conflicts.

For a simple project with one level of submodules, these are annoyances. For a deeply nested project — three or more levels, with shared dependencies appearing at multiple points in the tree — they're workflow-breaking.

## How Grove Solves It

### Creating Worktrees: `grove worktree add`

Grove creates the worktree and recursively initializes all submodules using the main worktree's existing checkout as a reference. No network access required. Submodule URLs are temporarily rewritten to point to the main worktree's local clones, then restored to their original values after initialization.

```bash
grove worktree add ../my-project-feature-x feature-x
```

What this does:

- Creates the worktree with `git worktree add`
- Recursively initializes submodules at every nesting level
- Uses local references (no network round-trips)
- Keeps submodule remotes pointing to the main worktree's local copies (local remotes)
- Optionally copies the Python venv (`--copy-venv`) with path fixups
- Runs `direnv allow` if an `.envrc` is present

Local remotes are the key default: submodule remotes in the new worktree point to the main worktree's local copies. This means `git push` inside a worktree submodule stays on-machine. Nothing reaches the upstream remote until you explicitly push from the main checkout. This enforces the merge-hub pattern by construction. Pass `--no-local-remotes` if you need worktree submodules to push directly to upstream.

### Merging: `grove worktree merge`

When a feature is complete, merging it back into main requires merging at every level of the submodule tree. Grove handles this automatically:

```bash
cd ~/Projects/my-project     # main checkout
grove worktree merge feature-x
```

This processes the entire submodule tree in topological order (leaves first, root last). At each repo:

1. Check if the feature branch exists (skip if not)
2. Merge the branch
3. Run configured test commands
4. If a conflict or test failure occurs, pause with instructions

The pause/resume model means you don't have to get everything right in one pass. Fix a conflict, run `grove worktree merge --continue`, and it picks up where it left off. If things go sideways, `grove worktree merge --abort` restores every repo to its pre-merge state.

### Cleaning Up: `grove worktree remove`

When you're done with a worktree:

```bash
grove worktree remove ../my-project-feature-x
```

This removes the worktree directory and prunes stale worktree entries. Clean and simple.

## The Merge-Hub Pattern

All of the above supports a specific development model: **the main checkout is the merge hub, not a development environment.**

The main checkout serves three purposes:

1. **Integration point.** Feature branches are merged here, one at a time, in a controlled sequence.
2. **Push origin.** Upstream pushes happen from here, after integration is complete and tested.
3. **Worktree source.** New worktrees are created from here, inheriting the latest integrated state.

It does not serve as a development environment. You don't edit files in the main checkout. You don't commit feature work there. You don't run experiments there. It stays clean, on the main branch, always ready to accept the next merge.

This matters because:

- **Merge conflicts are localized.** Each merge is feature-branch-into-main. If two features conflict, you resolve it during the second merge, with the first feature already cleanly integrated.
- **The main checkout is always deployable.** After each merge + push cycle, main represents a tested, integrated state.
- **Worktrees inherit a stable base.** When you create a new worktree, it branches from the latest integrated state, not from a half-merged in-progress state.

Local remotes (the default) reinforce this: pushes from worktree submodules go to the main worktree's local clones, not to the upstream remote. The only path to upstream is through the main checkout. This prevents accidental pushes from feature worktrees and ensures that all upstream changes go through the integration point.

## When Worktrees Shine

Worktrees are most valuable when:

- **You regularly work on multiple tasks.** Even two concurrent tasks benefit from the isolation.
- **Your project has submodules.** The deeper the nesting, the more pain worktrees eliminate.
- **You use AI agents.** Worktrees are the natural isolation mechanism for parallel agents.
- **You need fast context switching.** `cd` is faster than `git stash && git checkout && git submodule update --recursive`.
- **You need reproducibility.** Each worktree is a complete, independent snapshot. No shared mutable state.
