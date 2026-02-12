# Best Practices

This document covers operational guidance for teams building multi-component systems with submodules and Grove. The tools handle the mechanics; these practices help you get the most out of them.

## Testing Is the Foundation

The cascade workflow — propagating a change from a leaf submodule upward through the tree, committing pointer updates at each level — only works if you have tests. Without tests, cascade is just auto-committing and hoping. With tests, cascade is a confidence ladder: each level proves that the change works in an increasingly realistic context.

Grove's cascade uses four test tiers:

| Tier | What it tests | What it mocks |
|------|--------------|---------------|
| **local-tests** | Internal correctness of a single project | Everything external |
| **contract-tests** | Interface expectations (arguments, return types, error handling) | The other side of each interface |
| **integration-tests** | Direct connections between a project and its immediate dependencies | Transitive dependencies |
| **system-tests** | Full end-to-end behavior across the entire tree | Nothing |

Each tier is optional. You don't need all four to get value from cascade. A project with just `local-tests` configured still benefits from automated bottom-up propagation with basic correctness checks at each level.

But the more tiers you cover, the more cascade can tell you about a failure. When `integration-tests` fail, cascade runs auto-diagnosis to determine whether the problem is inside the dependency (its local-tests fail) or at the interface (its local-tests pass but the integration doesn't work). This distinction saves significant debugging time.

## Design Tests for Cascade

Each test tier should be:

- **Fast.** Cascade runs tests at every level of the tree. If your local-tests take 10 minutes, a three-level cascade takes 30 minutes just for that tier. Aim for seconds, not minutes.
- **Deterministic.** Flaky tests that pass sometimes and fail sometimes will pause cascade unnecessarily. Fix flaky tests before relying on cascade.
- **Focused.** Each tier should test what its level of mocking implies. Local-tests shouldn't reach out to external services. Integration-tests shouldn't depend on the state of unrelated components.

Practical guidance for each tier:

**local-tests.** This is your standard unit test suite. Mock all dependencies. If the project has a test command already configured in `[worktree-merge].test-command`, cascade inherits it automatically — no additional configuration needed.

**contract-tests.** These test your *expectations* about dependency APIs. If you call `library.parse(data)` and expect a dict back, write a contract test that asserts this. Mock the library's internals, but test the contract. When contract-tests fail, your code is calling a dependency incorrectly.

**integration-tests.** Use real direct dependencies, mock transitive ones. This isolates failures to the direct interface. If integration-tests fail but contract-tests pass, there's an incompatibility at the boundary that your contract assumptions didn't cover.

**system-tests.** No mocking. Full end-to-end. These are inherently slower and more sensitive to changes elsewhere in the tree. By default, cascade only runs them at the root level. Use `--system` when you need full confidence at every level (before a release). Use `--no-system` when experimental changes in sibling repos would break system tests but you want to cascade anyway.

### Tier Boundaries Are Practical, Not Rigid

Don't let categorization overhead slow you down. If you're not sure whether a test belongs in `local-tests` or `contract-tests`, put it wherever makes it run. Three similar assertions in two tiers is better than a premature test abstraction. The goal is a test suite that supports the cascade workflow — move tests between tiers as your understanding of the failure modes evolves.

## Cascade Early, Cascade Often

Small, frequent cascades are easier to debug than large, infrequent ones.

When you change a leaf submodule, cascade immediately. Don't accumulate changes and cascade once at the end of the day. Each cascade is a checkpoint — it proves that the change integrates cleanly at every level. If it doesn't, you're debugging one change, not ten.

During active development, use `--quick` to run only local-tests and contract-tests. This is fast enough to cascade on every commit:

```bash
grove cascade libs/common --quick
```

Before pushing or merging, run a full cascade:

```bash
grove cascade libs/common
```

Before a release, run with `--system` to get full end-to-end testing at every level:

```bash
grove cascade libs/common --system
```

If a cascade fails, fix the issue and `grove cascade --continue`. If you need to abandon the cascade entirely, `grove cascade --abort` restores every repo to its pre-cascade state.

## Sync Groups as a Discipline

If a dependency appears in multiple places in your tree, define a sync group for it. Don't rely on manual bookkeeping to keep instances in sync.

```toml
[sync-groups.common]
url-match = "shared-library"
standalone-repo = "/path/to/shared-library"
```

Run `grove sync` after every change to a shared component. This catches drift immediately. If instances have diverged (different developers committed to different instances), sync detects it and either auto-merges or pauses for conflict resolution.

Run `grove check` regularly (or in CI). It verifies that all sync-group instances are at the same commit and warns about drift before it causes integration problems.

When cascading a sync-group submodule, use the group name directly:

```bash
grove cascade --sync-group common
```

This builds a DAG covering all instances and their parent chains, ensuring the entire tree is tested cohesively.

## Building Composable Ecosystems

Submodules, sync groups, and cascade together enable a development model where components are:

- **Independently developable.** Each component is its own repo with its own tests, its own CI, and its own release cadence. A developer can clone just one component and work on it in isolation.

- **Collectively testable.** When components are assembled into a larger system via submodules, the full integration is testable at every level. Cascade walks the tree bottom-up, verifying that changes propagate correctly.

- **Composable.** The same component can participate in multiple systems. A shared library can be a submodule of both a web application and a CLI tool. Sync groups ensure version consistency where needed, while independent pinning allows divergence where appropriate.

This is the modular architecture promise delivered through tooling rather than convention. You don't need organizational discipline to keep versions in sync — `grove sync` enforces it. You don't need manual integration testing — `grove cascade` automates it. You don't need to coordinate release timing — each component advances at its own pace, and the integration points are explicit in the submodule pointers.

### The Ecosystem Growth Pattern

A common evolution:

1. **Start with a monolith.** One repo, one test suite, one deploy.
2. **Extract shared libraries.** When code is reused across projects, extract it into its own repo and add it as a submodule.
3. **Add sync groups.** When a shared library appears in multiple places, define a sync group to keep instances in sync.
4. **Add cascade tests.** When integration failures start costing time, configure test tiers to catch them earlier.
5. **Add worktrees.** When parallel development becomes a bottleneck, use worktrees to work on multiple features simultaneously.

You don't need to adopt everything at once. Each capability layer adds value independently. A project with just `grove push` and `grove check` is already better off than one managing submodules manually. Add sync groups, cascade, and worktrees as the need arises.

## Author Identity Across Submodules

Git submodules are independent repositories. Each one resolves its user identity by walking its own config chain: submodule local config → global `~/.gitconfig` → system config. A parent repo's local `user.name` and `user.email` settings do not propagate into its submodules.

This means if your global `~/.gitconfig` has a personal identity and you set a project-specific identity with `git config --local` in the parent repo, commits made inside submodules will still use the global (personal) identity — not the one you configured in the parent.

Grove's `worktree add` handles this for worktrees by copying local git config from the main worktree's submodules into the new worktree's submodules. But for the main checkout itself, you need a different solution.

### Git Conditional Includes

Git's `includeIf` directive lets you apply config based on the repository's location on disk. Set it in your global `~/.gitconfig`:

```gitconfig
# ~/.gitconfig

[user]
    name = Andrew Franklin
    email = andfranklin3@gmail.com

[includeIf "gitdir:~/Projects/cleanroom-labs/"]
    path = ~/.gitconfig-cleanroom
```

Then create the included file with the project identity:

```gitconfig
# ~/.gitconfig-cleanroom

[user]
    name = Lead Dev
    email = lead@cleanroomlabs.dev
```

Every repository under `~/Projects/cleanroom-labs/` — including submodules at any nesting depth — will use the Cleanroom Labs identity. The `gitdir:` condition matches based on the resolved `.git` directory, so it works for submodules (which have their own `.git` directories or gitdir references) and worktrees alike.

This is the cleanest solution for projects with submodules: one config change, applied automatically, no per-repo setup needed.

## Recommended Workflow

Pulling it all together, the recommended development cycle:

1. **Create a worktree** for the task:
   ```bash
   grove worktree add --local-remotes my-feature ../my-project-my-feature
   ```

2. **Develop** in the worktree. Make changes, write tests, iterate.

3. **Cascade** from the changed leaf to verify integration:
   ```bash
   grove cascade libs/common --quick    # during development
   grove cascade libs/common            # before merging
   ```

4. **Sync** if you changed a shared dependency:
   ```bash
   grove sync common
   ```

5. **Merge** back to main from the main checkout:
   ```bash
   cd ~/Projects/my-project
   grove worktree merge my-feature
   ```

6. **Push** upstream:
   ```bash
   grove push
   ```

7. **Clean up** the worktree:
   ```bash
   grove worktree remove ../my-project-my-feature
   ```

Each step has a clear purpose, a clear tool, and a clear recovery path if something goes wrong. The workflow scales from a single developer working on one feature to a team of developers and AI agents working on dozens of features in parallel.
