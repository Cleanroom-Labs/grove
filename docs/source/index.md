# Grove

Git submodule management tools for nested repositories.

[![Tests](https://github.com/Cleanroom-Labs/grove/actions/workflows/ci.yml/badge.svg)](https://github.com/Cleanroom-Labs/grove/actions/workflows/ci.yml)
[![Security](https://github.com/Cleanroom-Labs/grove/actions/workflows/security.yml/badge.svg)](https://github.com/Cleanroom-Labs/grove/actions/workflows/security.yml)
[![Docs](https://github.com/Cleanroom-Labs/grove/actions/workflows/docs.yml/badge.svg)](https://github.com/Cleanroom-Labs/grove/actions/workflows/docs.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/Cleanroom-Labs/grove/blob/main/LICENSE)

---

## About

`grove` provides a unified CLI for managing complex git submodule hierarchies. The package includes subcommands for verifying, synchronizing, pushing, visualizing, and managing worktrees for projects with nested submodules.

All subcommands can be run from any subdirectory within the repository. Configuration (`.grove.toml`) is optional — commands gracefully handle repos without it.

## Prerequisites

**Python 3.11+** is required.

### Git Config

`submodule.recurse` must not be set to `true`. Grove manages submodule state
explicitly through its own commands; when this setting is active, git silently
advances submodule pointers on `checkout` and `pull`, conflicting with grove's
sync and merge operations.

Either leave the option unset or explicitly set it to `false`:

```bash
git config --get submodule.recurse   # should print nothing or "false"

# If you use submodule.recurse globally, disable it per-project:
git config --local submodule.recurse false
```

## Installation

Install in development mode:

```bash
pip install -e .
```

For development with testing dependencies:

```bash
pip install -e ".[dev]"
```

For optional LLM-backed commit/squash generation:

```bash
pip install -e ".[llm]"
```

## Usage

### `grove init`

Generate a template `.grove.toml` configuration file with all available options documented as comments.

```bash
# Generate in current directory
grove init

# Generate at a specific path
grove init ../other-repo

# Overwrite an existing .grove.toml
grove init --force
```

### `grove check`

Verify that all submodules are on branches (not detached HEAD) and that all sync-group submodules are at the same commit.

```bash
grove check
grove check -v    # verbose with commit SHAs
```

### `grove push`

Push committed changes through nested submodules in bottom-up order using topological sort.

```bash
grove push
grove push --dry-run
grove push --force
```

### `grove sync`

Synchronize submodule sync groups (defined in `.grove.toml`) across all locations in the repository tree.

```bash
grove sync                    # sync all groups to latest
grove sync common             # sync just "common" group
grove sync common abc1234     # sync to specific commit
grove sync --dry-run          # preview changes
grove sync --no-push          # commit only, skip push
```

### `grove visualize`

Open an interactive tkinter GUI showing the git repository hierarchy and submodule relationships.

```bash
grove visualize
grove visualize /path/to/repo
```

### Example Workflow

Grove is designed around a central principle: **the main checkout is the merge hub, not a development environment.** All development happens in worktrees. The main checkout exists solely to integrate parallel work and push upstream.

```
~/Projects/
├── my-project/              main checkout (merge hub — no development here)
├── my-project-feature-a/    worktree (development happens here)
├── my-project-feature-b/    worktree (development happens here)
└── my-project-fix-theme/    worktree (development happens here)
```

The development cycle:

1. **Create worktrees** for each task from the main checkout:
   ```bash
   grove worktree add --local-remotes feature-a ../my-project-feature-a
   grove worktree add --local-remotes feature-b ../my-project-feature-b
   ```
   Each worktree gets its own fully initialized checkout with all submodules. The `--local-remotes` flag keeps submodule pushes on-machine — nothing leaves your filesystem until you explicitly push from main.

2. **Develop in worktrees.** Commit, test, iterate. Multiple developers or AI agents can work in separate worktrees simultaneously without interference.

3. **Switch back to the main checkout** when work is complete:
   ```bash
   grove worktree switch main
   ```

4. **Merge feature branches** from the main checkout:
   ```bash
   grove worktree merge feature-a
   grove worktree merge feature-b
   ```
   Merges are done sequentially from the main checkout. Each merge processes the entire submodule tree bottom-up, running tests at each level.

5. **Push upstream** from the main checkout:
   ```bash
   grove push
   ```

For the full narrative with examples, see {doc}`guides/submodule-workflow`.

### `grove worktree`

Create and remove git worktrees with automatic recursive submodule initialization. Local git config (e.g. `user.name`, `user.email`, signing settings) is copied from the main worktree to the new worktree by default.

```bash
grove worktree add ../feature-x-wt feature-x
grove worktree add --copy-venv ../feature-x-wt feature-x
grove worktree add --no-local-remotes ../feature-x-wt feature-x
grove worktree remove feature-x
grove worktree remove --force feature-x
```

### Worktree Lifecycle

Grove includes a full worktree lifecycle surface:

- `grove worktree switch` — switch/create worktrees with shell-friendly path directives
- `grove worktree list` — table or JSON inventory with branch metadata
- `grove worktree step` — commit/squash/push/rebase/diff/copy-ignored/prune flows
- `grove worktree hook` — inspect or execute configured lifecycle hooks
- `grove shell init` — wrapper generation for bash/zsh/fish

See {doc}`guides/worktree-lifecycle` for examples and behavior details.

### Configuration

Grove config precedence:

1. User config: `~/.config/grove/config.toml`
2. Project config: `.config/grove.toml`
3. Legacy fallback: `.grove.toml` (deprecated; used only when project config is absent)
4. Explicit override: `$GROVE_CONFIG_PATH`

### Using with WorkTrunk

Set `[worktree].backend` in Grove config:

- `"native"` — always use Grove-native lifecycle implementation
- `"wt"` — always delegate lifecycle commands to `wt`
- `"auto"` (default) — delegate when `wt` is on PATH, otherwise use native

See {doc}`design/worktrunk-integration` for delegation details.

### LLM Integration

`grove worktree step commit` and `grove worktree step squash` support this fallback chain:

1. `wt` delegation (when active)
2. `[commit.generation].command`
3. `[worktree.llm].providers` (optional Strands providers)
4. `$EDITOR`

Install optional dependencies with `pip install -e ".[llm]"`.

### `grove worktree merge`

Merge a feature branch into the current branch across all repos in the submodule tree, processing leaves first (topological order). Supports pause/resume on conflicts or test failures, full abort/rollback, and conflict prediction.

```bash
grove worktree merge my-feature
grove worktree merge my-feature --dry-run
grove worktree merge my-feature --no-ff
grove worktree merge --continue
grove worktree merge --abort
grove worktree merge --status
```

**Test commands** can be configured in `.grove.toml`:

```toml
[worktree-merge]
test-command = "pytest"

[worktree-merge.test-overrides]
"." = "npm test"
"technical-docs" = "make html"
```

### `grove completion`

Tab-completion for grove commands, subcommands, and flags.

```bash
grove completion install          # auto-detect shell, configure completions
grove completion install --check  # check if installed
grove completion bash             # print bash completion script
grove completion zsh              # print zsh completion script
grove completion fish             # print fish completion script
```

## Development

```bash
pytest              # full suite
pytest -v           # verbose
pytest -k <pattern> # filter by name
```

## License

MIT License. See [LICENSE](https://github.com/Cleanroom-Labs/grove/blob/main/LICENSE) for details.

```{toctree}
:maxdepth: 2
:caption: Concepts
:hidden:

concepts/why-submodules
concepts/why-worktrees
concepts/taming-submodules
concepts/monorepo-tradeoffs
```

```{toctree}
:maxdepth: 2
:caption: Guides
:hidden:

guides/submodule-workflow
guides/cascade-guide
guides/sync-group-cascade-workflow
guides/worktree-lifecycle
guides/best-practices
```

```{toctree}
:maxdepth: 2
:caption: Reference
:hidden:

reference/checkhealth-spec
reference/push-filtering
reference/validation-design
reference/alternatives
```

```{toctree}
:maxdepth: 2
:caption: Design
:hidden:

design/cascade-design
design/sync-divergence-merge
design/worktrunk-integration
design/worktrunk-integration-design
design/deferred-ideas
```

```{toctree}
:maxdepth: 2
:caption: Future Work
:hidden:

future/object-store-deduplication
```
