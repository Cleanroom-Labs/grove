---
name: grove
description: General-purpose grove assistant for git submodule management
---

# grove: Submodule Management Assistant

General-purpose assistant for grove, a git submodule management toolkit. Use this for any grove-related task that doesn't match a specific workflow skill, or when unsure which workflow to use.

For well-defined workflows, delegate to the specific skill listed below rather than re-implementing the steps here.

`$ARGUMENTS` may contain a question, a description of what the user wants to do, or a specific grove command to help with.

## Command Reference

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `grove init` | Generate template `.grove.toml` | `--force` |
| `grove check` | Verify submodule health and sync-group consistency | `-v` for details |
| `grove push` | Push committed changes bottom-up through submodules | `--dry-run`, `--sync-group`, `--cascade`, paths |
| `grove sync` | Synchronize sync-group instances to same commit | `--remote`, `--dry-run`, `--no-push`, `--continue/--abort` |
| `grove cascade` | Propagate leaf change upward with tiered testing | `--quick`, `--system`, `--push`, `--sync-group`, `--dry-run` |
| `grove worktree add` | Create feature worktree with submodule init | `--no-local-remotes`, `--copy-venv`, `--checkout` |
| `grove worktree remove` | Remove a worktree and prune stale entries | `--force` |
| `grove worktree merge` | Merge feature branch bottom-up across submodules | `--dry-run`, `--no-test`, `--continue/--abort` |
| `grove claude install` | Install/update Claude Code skills | `--user`, `--check` |
| `grove visualize` | Open interactive submodule visualizer GUI | |
| `grove completion install` | Install shell tab completion | `--shell`, `--check` |

## Workflow Skills

For these common workflows, delegate to the dedicated skill instead of handling manually:

| Skill | When to use |
|-------|-------------|
| `/grove-add` | Creating a new feature branch worktree |
| `/grove-merge` | Merging a completed feature branch back to main |
| `/grove-cascade` | Propagating a submodule change upward with testing |
| `/grove-sync` | Syncing shared dependencies across all locations |
| `/grove-push` | Pushing with path, sync-group, or cascade filters |
| `/grove-ship` | Health check followed by push (safest push path) |

## Decision Tree

### "I want to..."

- **Start a new feature** → `/grove-add`
- **Merge completed work** → `/grove-merge`
- **Push my changes upstream** → `/grove-ship` (safe, includes health check) or `/grove-push` (filtered)
- **Propagate a submodule change upward** → `/grove-cascade`
- **Sync a shared dependency** → `/grove-sync`
- **Check if something is wrong** → `grove check -v`
- **Set up grove for the first time** → See "First-Time Setup" below
- **Configure cascade testing** → See "Configuration" below
- **Clean up old worktrees** → See "Cleanup & Maintenance" below
- **Understand how grove works** → See "Conceptual Model" below

### Common Multi-Step Scenarios

**Changed a shared dependency (sync group member):**
1. Commit the change in the submodule
2. `grove sync <group>` — propagate to all instances
3. `grove cascade <path> --push` — test and push upward

**Finished a feature in a worktree:**
1. `/grove-merge` — merge back to main (from main worktree)
2. `/grove-ship` — health check and push
3. `grove worktree remove <path>` — clean up

**Quick iteration during development:**
1. Commit in the leaf submodule
2. `grove cascade <path> --quick` — fast cascade (local + contract tests only)
3. Before merging: `grove cascade <path>` — full cascade

**Release preparation:**
1. `grove check -v` — verify all submodules are healthy
2. `grove sync` — ensure all sync groups are consistent
3. `grove cascade <path> --system` — full testing at every level
4. `grove push` — ship it

## Configuration (.grove.toml)

Generated with `grove init`. Four sections:

### [sync-groups.\<name\>]

Define groups of submodules that should be at the same commit. Required when the same dependency appears in multiple places.

```toml
[sync-groups.common]
url-match = "shared-library"           # Substring match on submodule URLs
standalone-repo = "~/path/to/clone"    # Optional: local repo for resolving commits
commit-message = "chore: sync {group} submodule to {sha}"  # Optional
allow-drift = ["path/to/exception"]    # Optional: instances allowed to diverge
```

### [worktree-merge]

Default test command for `grove worktree merge`. Also inherited by `cascade.local-tests` as fallback.

```toml
[worktree-merge]
test-command = "pytest -x"

[worktree-merge.test-overrides]
"." = "make test"                      # Override for root repo
"services/api" = "npm test"            # Override for a submodule
"skip-this" = ""                       # Empty string = skip tests
```

### [worktree]

Defaults for `grove worktree add`.

```toml
[worktree]
copy-venv = true                       # Auto-copy Python venv (detects .venv/, venv/, .direnv/)
```

### [cascade]

Four test tiers for `grove cascade`. Each is optional.

```toml
[cascade]
local-tests = "pytest tests/unit -x"           # All deps mocked
contract-tests = "pytest tests/contracts -x"    # Other side mocked
integration-tests = "pytest tests/integration"  # Direct deps real
system-tests = "pytest tests/system"            # No mocking

[cascade.overrides."services/api"]
local-tests = "npm test -- --unit"
```

## First-Time Setup

1. **Initialize config**: `grove init` creates a template `.grove.toml`
2. **Edit `.grove.toml`**: Configure sync groups if you have shared submodules, add test commands
3. **Install skills**: `grove claude install` installs workflow skills to `.claude/skills/`
4. **Install completion**: `grove completion install` adds tab completion to your shell
5. **Verify**: `grove check -v` to confirm grove sees your submodule tree

## Troubleshooting

### Detached HEAD in submodules

**Symptom**: `grove check` reports submodules with detached HEAD.
**Cause**: Normal after `git submodule update`. Submodules check out commits, not branches.
**Fix**: `cd <submodule> && git checkout main` (or the appropriate branch). Only needed before making changes in that submodule.

### Sync group out of sync

**Symptom**: `grove check` reports instances at different commits.
**Cause**: One instance was updated but others weren't.
**Fix**: `grove sync <group>`. If instances have diverged, sync auto-merges. If conflicts arise, resolve then `grove sync --continue`.

### Cascade test failure

**Symptom**: `grove cascade` pauses with a test failure.
**Cause**: The change broke something at a higher level.
**Fix**: Read the auto-diagnosis output. Fix the issue in the indicated repo, then `grove cascade --continue`. Or `grove cascade --abort` to roll back.

### Push rejected

**Symptom**: `grove push` fails on one or more repos.
**Fix**: Check remote connectivity (`git remote -v`), authentication (`ssh -T git@github.com`), or if someone pushed to the remote since your last pull. Use `--force` only for recovery.

### Merge conflicts in worktree merge

**Symptom**: `grove worktree merge` pauses with conflicts.
**Fix**: Resolve conflicts in the indicated repo, stage with `git add`, then `grove worktree merge --continue`. Or `--abort` to cancel.

### "A cascade/sync/merge is already in progress"

**Fix**: Use `--status` to see progress, `--continue` to resume, or `--abort` to cancel.

## Conceptual Model

### Submodule hierarchy

Grove operates on a tree of git repos connected by submodules. The root repo contains submodules, which may contain their own submodules (nested). Changes in a leaf must propagate upward — each parent commits an updated submodule pointer.

### Sync groups

When the same dependency appears in multiple places (e.g., a shared theme in every project), grove treats them as a sync group. `grove sync` keeps all instances at the same commit. `grove cascade` detects sync groups and builds a DAG covering all instances.

### Worktrees

All development happens in worktrees (created with `grove worktree add`), not the main checkout. The main checkout is the merge hub — feature branches merge back into it, and `grove push` distributes from there. Local remotes (the default) keep worktree pushes on-machine until you merge back.

### Cascade

Bottom-up integration testing. When you change a leaf submodule, `grove cascade` walks up the tree, running progressively broader tests at each level (local → contract → integration → system). If a test fails, auto-diagnosis helps locate whether the problem is inside the dependency or at the interface.

## Cleanup & Maintenance

### Remove old worktrees

```bash
git worktree list                              # See all worktrees
grove worktree remove <path>                   # Remove one (also prunes)
grove worktree remove --force <path>           # Force-remove with uncommitted changes
```

### Delete merged branches

```bash
git branch -d feature-a feature-b              # Delete merged branches
git branch -D abandoned-branch                 # Force-delete unmerged branch
```

### Verify grove health

```bash
grove check -v                                 # Full health check with details
```

### Update skills after upgrading grove

```bash
grove claude install --check                   # See if skills are outdated
grove claude install                           # Update to latest
```
