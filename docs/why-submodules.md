# Why Submodules?

Git submodules have a reputation for being difficult. This document argues that for certain classes of projects, submodule-based decomposition is not just tolerable but actively superior to the alternatives — and that the tooling gap is the real problem, not the model itself.

## The Case for Modular Architecture

Software grows. A project that starts as a single repository eventually accumulates components with different ownership, different release cadences, and different testing requirements. At some point, keeping everything in one repo creates friction: changes to a shared library trigger CI for unrelated services, one team's broken test blocks another team's deploy, and the repository becomes too large to reason about.

Decomposition is the natural response. The question is how.

Three models dominate:

1. **Monorepo.** Everything in one repository. Tight coupling. All teams see all changes. Coordination is implicit (everyone is on the same commit). Works well with heavy tooling investment (Bazel, Nx, Turborepo), but couples release cadences and makes independent ownership difficult.

2. **Package registries.** Each component is independently versioned and published to a registry (PyPI, npm, Maven). Consumers declare version ranges. A resolver picks compatible versions at install time. Loose coupling. Independent ownership. But the indirection introduces its own problems.

3. **Submodules.** Each component is its own git repository. Parent repos pin exact commits. The dependency tree is explicit in the git history. No resolver. No registry. No version ranges.

Each model has trade-offs. Submodules trade convenience for precision.

## Why Submodules Over Package Registries

Package registries solve real problems. But they introduce a layer of indirection that creates its own:

**You depend on artifacts, not source.** When you consume a package from a registry, you get a built artifact. The source that produced it lives somewhere else, at some commit you may or may not be able to find. Debugging a dependency issue means cloning the dependency repo, checking out the right tag, and hoping the build environment matches.

With submodules, you depend on source directly. The exact code you run is right there in your tree. You can read it, search it, debug it, and modify it in place.

**Cross-cutting changes are a multi-release dance.** If you need to change a library's API and update all consumers simultaneously, registries require: change the library, publish a new version, update each consumer's dependency declaration, publish each consumer. If any step fails, you're in a partially-migrated state.

With submodules, a cross-cutting change is a coordinated commit chain. Update the library, update each parent's pointer, test at each level, push. It's not trivial, but it's atomic — you can see the entire change in one diff.

**Version resolution is implicit and fragile.** The resolver picks versions based on declared ranges. Two developers running `pip install` on the same day can get different transitive dependency versions. The resolved set depends on what's currently published, not what was tested.

With submodules, there is no resolution step. Every dependency is pinned to an exact commit. What you test is what you ship.

**You can develop against the actual code.** Need to prototype a change to a dependency? With registries, you either publish a pre-release version, use a local development install hack, or vendor the dependency temporarily. With submodules, the dependency code is already in your tree — edit it, test the integration, commit when it works.

## Tree-Structured Dependencies

A common objection to submodules: "But the same dependency appears multiple times in my tree. Isn't that wasteful?"

Consider a project where `shared-theme` is used by three documentation repos, each of which is a submodule of the aggregator:

```
root/
├── docs-aggregator/
│   ├── project-alpha-docs/
│   │   └── shared-theme/
│   ├── project-beta-docs/
│   │   └── shared-theme/
│   └── shared-theme/
└── shared-theme/
```

The theme appears four times. This is intentional.

**Each parent pins its own version.** If `project-alpha-docs` needs theme v2.3 while `project-beta-docs` is still on v2.1, that's fine. Each submodule pointer is independent. There's no global resolution that forces everyone onto the same version simultaneously.

**The dependency graph is explicit.** Run `git submodule status --recursive` and you see exactly which commit of which dependency every repo is using. There's no hidden transitive resolution. No "works on my machine because the resolver picked a different version."

**Standalone builds work.** Because each project embeds its dependencies, any project can build in isolation. You don't need the full tree to work on a single component. This matters for CI, for onboarding, and for independent team autonomy.

**Duplication is a feature, not a bug.** The disk cost of checking out the same repo multiple times is trivial. The value of having each parent independently control its dependency version is enormous.

## The Dependency Hell Problem

In languages like Python, only one version of a package can exist at runtime. If your application depends on `library-a` and `library-b`, and both depend on `utils`, Python's resolver must find a single version of `utils` that satisfies both. If `library-a` requires `utils>=2.0,<3.0` and `library-b` requires `utils>=1.0,<2.0`, you're stuck.

Semantic versioning is supposed to prevent this. If everyone follows the rules — patch releases don't break anything, minor releases are backward-compatible, major releases signal breaking changes — then version ranges work. In practice:

- Maintainers accidentally introduce breaking changes in minor releases.
- Upstream projects deprecate behavior that downstream projects depend on.
- Transitive dependencies update independently, creating incompatible combinations that no one tested together.
- The resolver picks the "newest compatible" version, which may be a version published after your tests last passed.

The result is dependency hell: your project worked yesterday, nothing in *your* code changed, but a transitive dependency updated and now the resolver produces a broken combination.

**Submodules eliminate this entire class of problem.** You don't declare version ranges. You pin exact commits. The version of `utils` that `library-a` uses is the version you tested against. It doesn't change unless you explicitly update the submodule pointer and re-test.

## Sync Groups: Version Uniformity Across the Tree

When a dependency appears in multiple places in your submodule tree, you often want all instances at the same commit — not because the tree structure requires it, but because your runtime does. If your application loads `shared-theme` from four different locations, they'd better all be the same version.

Grove's sync groups enforce this by construction:

```toml
# .grove.toml
[sync-groups.theme]
url-match = "shared-theme"
standalone-repo = "/path/to/shared-theme"
```

With this configuration:

- `grove sync theme` discovers every instance of `shared-theme` in the tree and updates them all to the same commit.
- `grove check` verifies that all instances are at the same commit and warns if they've drifted.
- `grove cascade` detects sync-group submodules and builds a DAG covering all instances and their parent chains, ensuring the entire tree is tested after a change.

This is version uniformity by construction, not by convention. You don't need to remember to update every instance. You don't need to hope that CI catches a missed one. The tooling enforces it.

## Mitigating Dependency Hell

Submodules shift dependency management from **runtime resolution** to **build-time pinning**:

- **You choose when to update.** A new version of a dependency doesn't automatically flow into your project. You pull the update, test it, and commit the new pointer when you're satisfied.

- **You test against what you ship.** There's no gap between "the version I tested" and "the version the resolver picked in production." The commit SHA in your submodule pointer is the exact code that will run.

- **You can cascade changes incrementally.** When updating a shared dependency, `grove cascade` walks the tree bottom-up, running tests at each level. If the update breaks an intermediate consumer, you find out immediately — not after publishing three packages and waiting for CI.

- **You can roll back atomically.** If an update causes problems, `git revert` the parent's submodule pointer. The entire tree returns to the known-good state.

- **Cross-ecosystem changes are possible.** If your tree spans multiple languages (a Python backend, a JavaScript frontend, a shared protocol buffer definition), submodules let you coordinate changes across ecosystem boundaries. Package registries can't do this — PyPI and npm don't know about each other.

The trade-off is real: submodules require more explicit management than `pip install --upgrade`. But the explicitness is the point. In a world where implicit resolution produces unpredictable results, explicit pinning with automated propagation (`grove sync`, `grove cascade`, `grove push`) is a reliable alternative.

## When Submodules Are the Right Choice

Submodules aren't for every project. They're best suited when:

- **You need exact reproducibility.** Regulatory environments, safety-critical systems, or any context where "works on my machine" is unacceptable.
- **You're building a multi-component system.** Multiple services, libraries, or applications that need to evolve together but be independently developable.
- **You want to avoid registry indirection.** Especially for internal libraries that don't need to be published.
- **You're working across language ecosystems.** A single submodule tree can coordinate Python, JavaScript, Go, and documentation repos.
- **You have shared dependencies that need version uniformity.** Sync groups solve this directly.

They're less suited when:

- **You're consuming many third-party open-source packages.** These are better managed by package registries. Submodules are for code you own or co-develop.
- **Your dependency graph is wide and shallow.** Submodules shine with deep, narrow trees. A project with 200 leaf dependencies is better served by a lockfile.
- **You need zero tooling overhead.** Vanilla git submodule commands are clumsy. Grove mitigates this, but there's still a learning curve.

For practical advice on configuring git to reduce submodule friction — and where grove's tooling picks up where git configs leave off — see [Taming Git Submodules](taming-submodules.md).
