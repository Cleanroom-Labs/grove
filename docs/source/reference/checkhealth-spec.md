# `grove check` Health Check — Spec

This document defines the intended complete scope of `grove check` as a
single entry point for grove repo health verification. It covers both checks
already implemented and checks planned for future implementation.

## Philosophy

`grove check` is the single health-check command. It answers the question:
*"Is this repo in a state where grove commands will behave as expected?"*

Checks belong in `grove check` if they are:
- **Non-destructive** — they only read state, never modify it
- **Discrete** — they have a clear pass/fail result
- **Actionable** — a failing check comes with a remediation path

`grove check` is not a linter and not a CI gate by default, but it should be
*usable* as one (`exit 0` = healthy, `exit 1` = issues found).

## Output Contract

Output is divided into labeled sections. Each section contains one or more
check lines with a status symbol:

```
Section name
  ✓ check name or description
  ✗ check name or description
    Explanation of the failure.
    Fix: ...
  ⚠ advisory (non-blocking)
```

Color coding:
- `✓` green — passed
- `✗` red — failed (contributes to `exit 1`)
- `⚠` yellow — advisory (does not affect exit code)

Exit codes: `0` (all checks passed), `1` (one or more failures).

`--verbose` (`-v`) shows additional detail (commit SHAs, config file origins,
remote URLs) without changing the pass/fail result.

## Checks

### Currently Implemented

**Branch state** — for each non-sync-group submodule, verifies it is on a
named branch (not detached HEAD). Sync-group submodules are excluded because
detached HEAD is expected for them.

**Sync-group consistency** — for each sync group defined in `.grove.toml`,
verifies all instances are at the same commit. Highlights divergent instances
and the majority commit. Skipped with a warning if no `.grove.toml` is found.

### Planned: Git Config Validation

**`submodule.recurse` check** — verifies that `submodule.recurse` does not
resolve to `true` in the effective git configuration.

Implementation notes:
- Run `git config --get submodule.recurse`; non-zero exit means unset (safe)
- Run `git config --show-origin --get submodule.recurse` to obtain the origin
  file path for display; parse the first tab-delimited field
- Add a `check_git_config()` function in `check.py` following the pattern of
  `check_sync_groups()`
- Add a row to the validation matrix in `docs/validation-design.md`

Failure output:
```
Git config
  ✗ submodule.recurse = true  [~/.config/git/config]
    Grove manages submodule state explicitly. This setting causes git to
    silently advance submodule pointers on checkout/pull, conflicting with
    grove's sync and merge commands.
    Fix: remove [submodule] recurse = true from the config file above, or:
      git config --global submodule.recurse false
    See: grove check -v
```

Pass output:
```
Git config
  ✓ submodule.recurse is not set (default: false)
```

**Secondary: non-fatal warnings in mutating commands**

In `sync.py`, `worktree_merge.py`, and `cascade.py`, emit a yellow `⚠`
warning at the start of `run()` if `submodule.recurse = true` is detected:

```
⚠ submodule.recurse = true detected. This may interfere with this operation.
  Run `grove check` for details and remediation steps.
```

This is non-blocking. The user may be mid-operation or unable to change
global config immediately. The warning ensures visibility without requiring
a `--skip-checks` flag.

### Deferred: Worktree Hygiene

Check for stale worktree entries that `git worktree prune` would remove. Stale
entries don't cause errors but can produce confusing output in `git worktree list`.

Implementation sketch: run `git worktree list --porcelain`, identify entries
where the worktree path no longer exists on disk, report as advisory (`⚠`).

### Deferred: Config Validity

Verify that `.grove.toml` parses without error and that referenced sync-group
URL patterns are non-empty. Useful as an early-failure check before running
sync or cascade.

Implementation sketch: call `load_config()` and catch `ValueError`; check
that each `SyncGroup.url_match` is a non-empty string.

## Integration with CI

`grove check` is designed to be usable as a pre-merge or post-checkout gate:

```yaml
- run: grove check
```

The exit code contract (`0` = healthy, `1` = issues) makes it safe to use in
`&&` chains or as a CI step that blocks on failure. The `--verbose` flag adds
detail to CI logs without changing the exit code.

## Relationship to Other Commands

`grove check` is diagnostic only — it never modifies state. Pre-flight
validation in individual commands (e.g., `grove push` checking uncommitted
changes) is separate and serves a different purpose: blocking unsafe mutations.
The two layers are complementary. See [Validation Design](validation-design.md) for the full
validation matrix.
