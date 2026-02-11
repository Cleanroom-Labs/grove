# Cascade User Guide

## Quick Start

1. Configure test commands in `.grove.toml`:

```toml
[cascade]
local-tests = "pytest tests/unit -x"
```

2. Make changes in a leaf submodule, commit them there.

3. Cascade the change upward:

```bash
grove cascade libs/common
```

4. Push when satisfied:

```bash
grove push
```

## Common Workflows

### Basic: Change a leaf, cascade upward

```bash
# 1. Make your change in the leaf
cd libs/common
# ... edit files, run local tests ...
git add -A && git commit -m "feat: add new helper"

# 2. Return to root and cascade
cd ../..
grove cascade libs/common
```

Cascade walks up the tree: `libs/common → services/api → . (root)`, running tests and committing pointer updates at each level.

### Cascading a sync-group submodule

When a submodule belongs to a sync group (e.g., `libs/common` shared by multiple parent repos), cascade automatically detects this and builds a DAG covering all instances.

```bash
# 1. Ensure all instances are in sync
grove sync common

# 2. Make your change in one instance, commit it
cd frontend/libs/common
# ... edit, test, commit ...
cd ../../..

# 3. Sync the change to all instances
grove sync common

# 4. Cascade from all instances upward (DAG mode)
grove cascade frontend/libs/common

# 5. Push just the affected repos
grove push --cascade frontend/libs/common
```

In DAG mode, cascade processes all instances as leaves, all their parents as intermediates, and the root last — deduplicating shared ancestors. You can point to any instance; grove discovers the others automatically.

If instances are out of sync, cascade fails with a suggestion to run `grove sync` first. Use `--force` to bypass this check during prototyping.

### Handling test failures

When a test fails, cascade pauses and shows what went wrong:

```
  ✗ integration-tests FAILED (2.3s)

  Auto-diagnosis:
    Running local-tests of libs/common...
    ✓ libs/common — local-tests passed (problem is likely at the interface)

  Paused. Fix the issue, then run: grove cascade --continue
```

Fix the issue in the indicated repo, then resume:

```bash
grove cascade --continue
```

### Rolling back

If you want to undo all cascade commits:

```bash
grove cascade --abort
```

This restores each repo to its pre-cascade commit.

### Previewing the cascade

Use `--dry-run` to see what would happen without making changes:

```bash
grove cascade libs/common --dry-run
```

## Configuration Examples

### Small project (2-3 repos)

Just `local-tests` is enough. No other tiers needed.

```toml
[cascade]
local-tests = "pytest -x"
```

### Medium project

Add `integration-tests` for interface verification at intermediate and root levels.

```toml
[cascade]
local-tests = "pytest tests/unit -x"
integration-tests = "pytest tests/integration"
```

### Large project

All four tiers for maximum confidence.

```toml
[cascade]
local-tests = "pytest tests/unit -x"
contract-tests = "pytest tests/contracts -x"
integration-tests = "pytest tests/integration"
system-tests = "pytest tests/system"
```

### Mixed-language grove

Use per-repo overrides when repos use different test runners.

```toml
[cascade]
local-tests = "pytest tests/unit -x"

[cascade.overrides."services/api"]
local-tests = "npm test -- --unit"
contract-tests = "npm test -- --contracts"
integration-tests = "npm test -- --integration"

[cascade.overrides."."]
local-tests = "make test-unit"
system-tests = "make test-e2e"
```

### Existing worktree-merge users

If you already have `[worktree-merge].test-command`, cascade inherits it as `local-tests` automatically — no additional config needed.

```toml
[worktree-merge]
test-command = "pytest -x"
# cascade.local-tests will use "pytest -x" as fallback
```

## When to Use Each Flag

| Flag | Use case |
|------|----------|
| *(default)* | Everyday cascading. Balanced speed and confidence. |
| `--quick` | Rapid iteration during development. Only runs local + contract tests. |
| `--system` | Before releases or after major changes. Runs system-tests at every level. |
| `--no-system` | When experimental changes in sibling repos would break system tests. |
| `--force` | Skip sync-group consistency check. Use when prototyping with out-of-sync instances. |
| `--dry-run` | Preview cascade chain and test plan without executing anything. |

## Test Design Tips

### local-tests

Design so that if they pass, remaining failures are most likely at inter-project interfaces. Mock all dependencies. Focus on internal correctness.

### contract-tests

Test your *expectations* about dependency APIs — arguments, return types, error handling. Mock the other side. If these fail, your code is calling a dependency incorrectly.

### integration-tests

Use real direct dependencies, mock transitive ones. This isolates failures to the *direct* interface. If integration-tests fail but contract-tests pass, there's an incompatibility at the boundary.

### system-tests

No mocking. Full end-to-end verification. These are inherently slower and more sensitive to changes elsewhere. Reserve for the root level by default; use `--system` when you need full confidence at every level.

### Practical guidance

Tier boundaries are practical, not rigid. The goal is a test suite that supports the cascade workflow — don't let categorization overhead slow you down. Three similar lines of code in two tiers is better than a premature test abstraction.
