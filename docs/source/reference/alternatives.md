# Alternatives and Rationale

Grove exists because managing deeply nested git submodule hierarchies (3+ levels) requires orchestrated, tree-aware operations that neither native git nor existing third-party tools provide.

This document surveys what's available and where grove fills gaps.

## Native Git Features

### `git push --recurse-submodules=on-demand`

Pushes submodules before the parent repo. Works for single-level nesting, but doesn't perform a full topological sort across a deeply nested tree. It also doesn't validate that each repo is on a branch with no uncommitted changes before pushing.

Grove's `grove push` discovers the full submodule hierarchy, sorts it topologically (leaves first), validates branch state at every level, and pushes in the correct order.

### `git worktree add`

Creates a linked worktree but does **not** initialize submodules. The official git documentation states under BUGS: *"Multiple checkout in general is still experimental, and the support for submodules is incomplete."* The workaround — running `git submodule update --init --recursive` afterward — fetches from the network instead of reusing the existing local checkout.

Grove's `grove worktree add` initializes submodules recursively using the main worktree as a local reference (no network fetches), copies local git config, optionally copies the Python venv, and can keep submodule remotes pointing locally.

### `git submodule update` / `git submodule sync`

Operates on the submodules of a single repository. There is no built-in concept of "update every instance of submodule X across the entire tree to the same commit." Each level of nesting must be handled separately.

Grove's `grove sync` resolves a target commit, discovers all instances of a sync-group submodule across the full tree, updates them all, commits bottom-up, and pushes.

## Third-Party Tools

### git-wtsm

[github.com/shibuido/git-wtsm](https://github.com/shibuido/git-wtsm)

A wrapper for safe worktree operations in repositories with submodules. Provides `add`, `status`, and `remove` commands with submodule-aware safety checks. This is the closest equivalent to `grove worktree add/remove`.

**Differences:** git-wtsm does not copy local git config, copy Python venvs, set up local remotes, run direnv, or use the main worktree as a local reference for submodule cloning.

### git-worktree-runner

[github.com/coderabbitai/git-worktree-runner](https://github.com/coderabbitai/git-worktree-runner)

A general-purpose worktree manager with editor and AI tool integration. Handles file copying, hooks, and shell completions. Does not handle submodules.

### sync_submodules

[github.com/shibuido/sync_submodules](https://github.com/shibuido/sync_submodules)

A bash script for team synchronization using a superrepo pattern. Pushes submodule changes and updates superrepo references with conflict detection.

**Differences:** Designed for flat (single-level) team sync workflows. Does not support sync-group semantics across deeply nested trees or topological commit ordering.

### Google Repo

[source.android.com/docs/setup/develop/repo](https://source.android.com/docs/setup/develop/repo)

A manifest-driven multi-repo management tool built for Android's 1000+ repository ecosystem. Uses an XML manifest to define repository locations and revisions.

**Differences:** Repo replaces submodules entirely with its own model. There is no parent-child version pinning — each repository is independently checked out per the manifest. This is a fundamentally different architecture suited for very large, flat multi-repo projects.

### Git Subtree / Git Subrepo

Subtree merges external repositories into subdirectories of a single repo. Subrepo provides a simpler interface for the same concept.

**Differences:** Both eliminate submodules by inlining external code into the parent repo. This removes multi-commit propagation but also removes independent ownership — external repos can't be worked on in isolation with their own branches and tags.

### Git X-Modules

[gitmodules.com](https://gitmodules.com/)

A server-side SaaS that syncs repository subdirectories across repos. Works transparently on the server without client-side tooling.

**Differences:** Requires server infrastructure. Not a local CLI tool.

## What's Unique to Grove

These features have no equivalent in native git or existing third-party tools:

- **`grove worktree merge`** — Merges a feature branch across the entire submodule tree in topological order, with pause/resume on conflicts, test command execution, and full abort/rollback.
- **`grove sync` with sync groups** — Discovers all instances of a shared submodule across an arbitrarily nested tree and updates them to the same commit in a single operation.
- **`grove push` with topological validation** — Validates branch state and pushes through 3+ levels of nesting in the correct order.
- **`grove check` with sync-group verification** — Confirms that all instances of sync-group submodules are at the same commit.
- **`grove worktree add` convenience bundle** — Submodule init from local reference + config copy + venv copy + local remotes + direnv allow, all in one command.
