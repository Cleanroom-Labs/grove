# Deferred Ideas

Ideas captured during implementation of `grove worktree merge` that are out of scope for the initial release.

## Rebase support

`grove worktree rebase <branch>` — same workflow as merge but using rebase instead. Would share most of the state management, journal, and topology infrastructure.

## Merge recipe files

Declarative merge ordering with partial commit ranges. A recipe file could specify which repos to merge and in what order, allowing selective merges of specific commit ranges rather than full branch merges.

## Commit-at-a-time merge mode

Instead of merging the entire branch at once, merge one commit at a time across the submodule tree. Useful for debugging when a sequence of changes needs careful review.

## Hierarchical bisection

Bisect across nested repos: when a bug is introduced somewhere in the tree, bisect the root repo first to find the breaking commit, then drill into the specific submodule that changed and bisect within it.
