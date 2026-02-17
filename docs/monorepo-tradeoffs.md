# Monorepo vs. Polyrepo: A Design Decision, Not a Best Practice

Choosing between a monorepo and a polyrepo is an architectural decision with real tradeoffs. There is no universally correct answer. The right choice depends on the size of your team, the maturity of your codebase, and how tightly coupled your modules are.

This document examines the tradeoffs honestly, drawing on practical experience and the excellent analysis in [Philomatics' video on monorepo dependency management](https://www.youtube.com/watch?v=_uDvhXK4T3M), which we recommend watching. It also explains where grove fits in the spectrum.

## The Monorepo Advantage: Atomic Cross-Module Changes

The strongest argument for a monorepo is that changes spanning multiple modules can land as a single atomic commit (or pull request).

Consider a shared logging library used by both a frontend and a backend. In a monorepo, updating the library and both consumers is one commit. The entire change is visible in one diff, reviewable in one PR, and revertable in one operation.

In a polyrepo, the same change requires: clone the library repo, branch, commit, PR, wait for review, merge, release a new version. Then find every consumer repo and repeat the process for each one, updating the dependency version along the way. The feature is now scattered across multiple commits in multiple repos, and reverting it means tracking down and reverting each one separately.

This is a genuine, significant advantage — especially during active development when APIs are still evolving.

### Other Monorepo Benefits

- **Discoverability.** Full-text search and IDE refactoring tools work across all modules out of the box. In organizations with hundreds of repos, simply finding the code you need can be a challenge.
- **Consistent CI and tooling.** Linters, formatters, build rules, and CI pipelines can be enforced uniformly across the entire codebase from a single configuration.
- **Simpler onboarding.** New developers clone one repo and have everything. No hunting for which repos to clone or how they connect.

## The Hidden Cost: Forced Consumer Upgrades

The advantage of atomic cross-module changes has a flip side that only reveals itself as the codebase and team grow. As Philomatics puts it: whenever you make a change to an internal library, you are also forced to upgrade all its consumers immediately.

In a typical monorepo, internal dependencies are managed through workspace features (npm workspaces, Cargo workspaces, etc.) where consumers always use the current version of the library — whatever is on disk right now. There are no version numbers for internal dependencies.

This works beautifully when the change is small or when all consumers can be updated in the same sitting. But consider this scenario: you need to make a breaking change to the logging library to add a feature the frontend urgently needs. The backend also depends on this library, and updating it would be a significant refactor. In a polyrepo, you could leave the backend untouched on the old version. In a monorepo, the backend's build breaks until someone updates it.

As the number of consumers grows, this forced-upgrade pressure becomes a real burden. Urgent work in one part of the codebase can be blocked by the effort required to update unrelated consumers elsewhere.

## The Workaround Trap

There are several ways to work around forced upgrades in a monorepo, but each comes with its own costs.

### Backwards Compatibility Shims

Instead of changing the existing API, keep the old version around and add a new method alongside it. Add deprecation warnings to the old one. This preserves compatibility but introduces technical debt that needs to be cleaned up later — and is easy to forget about.

### Feature Flags

Gate new behavior behind runtime flags. The frontend opts into the new code path while the backend keeps using the old one. This avoids breaking changes but adds conditional complexity to the library and makes it harder to reason about behavior.

### Internal Package Registries

Publish internal libraries to a private registry (GitLab Package Registry, JFrog Artifactory, etc.) with fixed version numbers. Consumers pin to specific versions instead of using workspace-relative paths.

This technically works, but as Philomatics observes, it undermines the core monorepo advantage. The latest commit in the repo contains version 2.0 of the logging library, but the backend is consuming version 1.x from a previous commit. The code on disk no longer matches the code being consumed. Full-text search becomes unreliable — you might search for a function that was removed in 2.0 but is still used by the backend through the registry. Refactoring tools can't help you because they only see the current state of the files.

At this point, you've added the complexity of versioned dependency management on top of a monorepo, getting the downsides of both approaches.

## Other Monorepo Disadvantages

### Tooling Investment

Good tooling is not optional for monorepos at scale. You need a build tool like Nx, Turborepo, or Bazel that can determine which modules are affected by a change and rebuild only those, with smart caching. Without this, every push rebuilds the entire repo — which quickly becomes untenable.

Don't underestimate the investment required. Companies like Google have spent millions on monorepo tooling. Just because Google-scale tools like Bazel are freely available doesn't mean they scale down well to smaller teams. The configuration and maintenance overhead can exceed the benefits.

### Access Control

You cannot give fine-grained read access within a single repo. If your frontend team should only see frontend code, or you want to open-source certain modules, or you need to share code with external partners — a monorepo makes all of this difficult or impossible.

### Repository Scale

At very large scales (hundreds of thousands of files and commits), git itself starts struggling. Cloning becomes slow, branch switching takes minutes, and common operations that developers do dozens of times a day acquire noticeable latency. This is a real problem, but only at truly massive scale — most teams will never hit it.

## A Practical Decision Framework

Based on the analysis in the Philomatics video, here is a practical approach:

1. **Start with a monorepo.** When you have a handful of applications and libraries, it's the simpler approach. The advantages are real and the disadvantages haven't materialized yet.

2. **Split opportunistically when specific problems emerge.** When a library becomes mature, has many consumers, and the forced-upgrade pressure is causing real friction — split it out into its own repo with versioned releases. This is a conscious decision to defer system integration to a later time. Don't split preemptively; split because a specific problem demands it.

3. **Don't confuse team boundaries with repo boundaries.** Team splitting is sometimes cited as a reason for polyrepos, but this is using a technical solution to solve a management problem. Separate teams can work effectively in the same repo with proper ownership conventions and CI configuration.

4. **At large scale, consider returning to a monorepo.** Organizations with hundreds of engineers and thousands of repos often find the polyrepo sprawl overwhelming. At that scale, you're investing in internal tooling regardless — and if you're making that investment, you might as well get the monorepo advantages in discoverability, refactoring, and CI consistency.

The key insight is that this is a spectrum, not a binary choice. And the right position on that spectrum changes as your organization evolves.

## Where Grove Fits

Grove occupies the space between pure monorepo and pure polyrepo by using **git submodules with monorepo-like tooling**.

Each module is its own git repository with its own branches, tags, and release cadence. This gives you polyrepo-style independence: consumers pin to specific commits, and you can defer upgrades when needed. There is no forced-upgrade pressure.

But grove layers monorepo-style workflows on top:

- **`grove worktree merge`** merges a feature branch across the entire submodule tree atomically, in topological order — giving you the atomic cross-module changes that are the monorepo's core advantage.
- **`grove sync`** keeps shared dependencies at the same commit across all locations — opt-in consistency without forced upgrades.
- **`grove cascade`** propagates a leaf change upward through the tree with progressive testing at each level — catching integration issues early without requiring all consumers to update simultaneously.
- **`grove push`** validates and pushes through the entire submodule hierarchy in the correct order.

The tradeoff is explicit: you manage submodule pointers instead of workspace-relative paths, and you need grove's tooling to make the workflow practical. But you get independent versioning per module, fine-grained access control, and the ability to defer consumer upgrades — while still having the option of atomic cross-module changes when you want them.

For a deeper discussion of why submodules over package registries, see [Why Submodules?](why-submodules.md). For a survey of alternative tools, see [Alternatives and Rationale](alternatives.md).

## Further Reading

- [Monorepos and the Fallacy of Scale](https://www.youtube.com/watch?v=_uDvhXK4T3M) — Philomatics video covering the dependency management tradeoffs discussed in this document
- [Why Submodules?](why-submodules.md) — The case for submodule-based decomposition
- [Alternatives and Rationale](alternatives.md) — Survey of existing tools and where grove fills gaps
- [Submodule Workflow](submodule-workflow.md) — How grove's worktree-based development model works in practice
