# `grove check` Health Check — Spec

This document defines the scope of `grove check` as a single entry point
for grove repo health verification.

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

**Git config: `submodule.recurse`** — verifies that `submodule.recurse` is not
set to `true`. This setting causes git to silently advance submodule pointers on
`checkout` and `pull`, conflicting with grove's sync and merge operations. Values
of `false` or unset are safe. When the check fails, it shows the config file
origin and suggests `git config --local submodule.recurse false`. Verbose mode
additionally shows how to unset from global config.

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
