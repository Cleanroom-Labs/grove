# Validation Design

Grove commands perform "flight prechecks" before mutating repository state. The philosophy: check early, fail clearly, and provide `--skip-checks` (or `--force` where appropriate) for external/remote checks but never for local safety checks.

## Validation Matrix

| Check | check | push | sync | wt add | wt remove | wt merge | init |
|-------|:-----:|:----:|:----:|:------:|:---------:|:--------:|:----:|
| Repo root discovery | x | x | x | x | x | x | - |
| Uncommitted changes | - | x | x | - | - | x | - |
| Detached HEAD | report | allow | allow | - | - | skip | - |
| Sync group consistency | report | x | - | - | - | - | - |
| Remote divergence | - | - | x | - | - | - | - |
| Target exists/valid | - | - | x | x | - | x | x |
| Conflict prediction | - | - | - | - | - | warn | - |
| Topology divergence | - | - | - | - | - | warn | - |
| Config exists | - | - | x | - | - | - | x |

**Legend:** **x** = blocks (unless `--skip-checks` / `--force`), **report** = diagnostic output only, **warn** = non-blocking warning, **skip** = silently skips affected repo, **allow** = permits the condition, **-** = not checked.

## The `--skip-checks` / `--force` Contract

`--skip-checks` (on push, sync, cascade) and `--force` (on init, worktree remove) bypass remote and external checks. They never bypass local safety checks that could cause data loss.

| Command | Flag | Bypasses |
|---------|------|----------|
| `push --skip-checks` | `--skip-checks` | uncommitted changes, sync group consistency |
| `sync --skip-checks` | `--skip-checks` | parent repo divergence, uncommitted changes |
| `cascade --skip-checks` | `--skip-checks` | sync-group consistency check |
| `init --force` | `--force` | existing config file |
| `worktree remove --force` | `--force` | uncommitted changes in worktree |
| `worktree merge` | *(none)* | **no skip flag** -- must fix issues directly |

`worktree merge` intentionally omits a skip flag because it mutates multiple repos atomically. Partial state from a forced merge would be harder to recover from than fixing the precheck issue.

## Shared Infrastructure

These functions implement the validation checks. Reuse them when adding new commands.

- **`RepoInfo.validate()`** (`src/grove/repo_utils.py:242`) -- checks uncommitted changes, detached HEAD, remote divergence. Used by `push` and `sync`.
- **`RepoInfo.has_uncommitted_changes()`** (`src/grove/repo_utils.py:192`) -- standalone dirty-tree check. Used directly by `worktree merge` (which doesn't need the full `validate()` pipeline).
- **`check_sync_groups()`** (`src/grove/check.py`) -- verifies all instances of a sync-group submodule are at the same commit. Used by `check` and `push`.
- **`_predict_conflicts()`** (`src/grove/worktree_merge.py:144`) -- simulates `git merge --no-commit --no-ff`, then aborts, to predict which files will conflict.
- **`_check_structural_consistency()`** (`src/grove/worktree_merge.py:253`) -- compares submodule topology between HEAD and the target branch using the topology cache.

## Design Principles

1. **Check uncommitted changes before mutating working trees.** This is the most common source of lost work. Every command that modifies files should check this.

2. **`--skip-checks` / `--force` bypasses remote/external checks, never local safety.** Remote divergence, sync consistency, and "file already exists" are external conditions that an informed user can override. Uncommitted changes in `push` are overridable because push itself doesn't modify the working tree.

3. **Dry-run should show exactly what the real run would do.** Same discovery, same validation, same output -- just skip the final mutation step.

4. **Non-blocking warnings inform but don't prevent.** Conflict prediction and topology divergence are advisory. They help the user prepare but shouldn't block legitimate merges.

5. **Detached HEAD handling varies by intent.** `check` reports it as a problem. `worktree merge` skips the repo (can't merge into a detached HEAD). `push` and `sync` allow it (submodules are often on detached HEADs by design).

## Gaps to Consider

- `worktree add` does not check for uncommitted changes or detached HEAD before creating the worktree. This is intentional -- it creates a *new* worktree rather than modifying the current one, so the current tree's state is irrelevant.
- `worktree merge` does not check sync group consistency. A merge may intentionally bring groups out of sync (e.g., merging a branch that updates one instance but not another). The user runs `grove check` afterward to verify.
