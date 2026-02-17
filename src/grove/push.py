"""
grove/push.py
Pushes committed changes through nested submodules bottom-up using topological sort.

Supports filtering by specific paths, sync group, or cascade chain.
When no filters are active, sync-group submodules are excluded (managed by
``grove sync``).  When any filter is active, all repos are discovered and
the filter narrows the push set.

Usage (via entry point):
    grove push                              # Push all (default)
    grove push frontend backend             # Push specific repos
    grove push --sync-group common          # Push sync-group parents
    grove push --cascade libs/common        # Push cascade chain
    grove push --dry-run                    # Preview
    grove push --force                      # Skip validation
"""

from __future__ import annotations

from pathlib import Path

from grove.check import check_sync_groups
from grove.config import GroveConfig, get_sync_group_exclude_paths, load_config
from grove.repo_utils import (
    Colors,
    RepoInfo,
    RepoStatus,
    discover_repos_from_gitmodules,
    find_repo_root,
    print_status_table,
    topological_sort_repos,
)


def _compute_push_filter_set(
    paths: list[str],
    sync_group_name: str | None,
    cascade_path: str | None,
    repo_root: Path,
    config: GroveConfig,
    all_repos: list[RepoInfo],
) -> set[Path] | None:
    """Compute the set of repo paths to include when filters are active.

    Returns None if no filters are active (push all).
    Returns a set of resolved Path objects for repos matching any filter.
    Uses union semantics — a repo matching ANY filter is included.
    Returns an empty set and prints an error if a filter target is invalid.
    """
    if not paths and not sync_group_name and not cascade_path:
        return None

    result: set[Path] = set()
    rel_path_to_repo = {r.rel_path: r for r in all_repos}

    # --- Positional paths: exact match on rel_path ---
    for p in paths:
        if p in rel_path_to_repo:
            result.add(rel_path_to_repo[p].path)
        else:
            print(Colors.red(f"Error: '{p}' is not a recognized repository."))
            available = sorted(r.rel_path for r in all_repos if r.rel_path != "(root)")
            print(f"Available repos: {', '.join(available)}")
            return set()  # empty set signals error

    # --- --sync-group: parent repos of sync-group submodules ---
    if sync_group_name:
        if sync_group_name not in config.sync_groups:
            print(Colors.red(f"Error: Unknown sync group '{sync_group_name}'."))
            print(f"Available groups: {', '.join(config.sync_groups)}")
            return set()

        from grove.sync import discover_sync_submodules, get_parent_repos_for_submodules

        group = config.sync_groups[sync_group_name]
        submodules = discover_sync_submodules(repo_root, group.url_match)
        if not submodules:
            print(Colors.yellow(
                f"Warning: No submodule instances found for sync group '{sync_group_name}'."
            ))
        else:
            parent_repos = get_parent_repos_for_submodules(
                submodules, repo_root, all_repos,
            )
            for repo in parent_repos:
                result.add(repo.path)

    # --- --cascade: repos in the cascade chain from leaf to root ---
    if cascade_path:
        from grove.cascade import _discover_cascade_chain

        target = (repo_root / cascade_path).resolve()
        try:
            chain = _discover_cascade_chain(target, all_repos)
        except ValueError as e:
            print(Colors.red(f"Error: {e}"))
            return set()

        for repo in chain:
            result.add(repo.path)

    return result


def run(args) -> int:
    # Validate execution context
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    # Extract filter args (use getattr for backward compat with old Namespace)
    filter_paths = getattr(args, "paths", []) or []
    sync_group_name = getattr(args, "sync_group", None)
    cascade_path = getattr(args, "cascade", None)
    has_filters = bool(filter_paths or sync_group_name or cascade_path)

    # Discovery phase
    print(Colors.blue("Discovering repositories..."))
    print()

    config = load_config(repo_root)

    if has_filters:
        # When filters are active, discover ALL repos (no sync-group exclusion)
        repos = discover_repos_from_gitmodules(repo_root)
    else:
        # Default: exclude sync-group submodules (managed by grove sync)
        exclude_paths = get_sync_group_exclude_paths(repo_root, config)
        repos = discover_repos_from_gitmodules(
            repo_root, exclude_paths=exclude_paths or None,
        )

    print(f"Found {Colors.green(str(len(repos)))} repositories")
    print()

    # Compute filter set
    filter_set = _compute_push_filter_set(
        filter_paths, sync_group_name, cascade_path,
        repo_root, config, repos,
    )
    # Empty set means an error occurred during filter resolution
    if filter_set is not None and not filter_set:
        return 1

    if filter_set is not None:
        filter_count = len(filter_set)
        print(f"Filter active: targeting {Colors.green(str(filter_count))} repositories")
        print()

    # Validation phase
    print(Colors.blue("Validating repositories..."))
    print()

    validation_failed = False
    repos_to_push: list = []

    for repo in repos:
        # Skip repos not in the filter set
        if filter_set is not None and repo.path not in filter_set:
            continue
        if repo.validate(allow_detached=True, allow_no_remote=True):
            if repo.status == RepoStatus.PENDING:
                repos_to_push.append(repo)
        else:
            print(f"  {Colors.red('✗')} {repo.rel_path}")
            print(f"    {Colors.red(repo.error_message or 'Unknown error')}")
            validation_failed = True

    # Print status table for filtered repos (or all if no filter)
    if filter_set is not None:
        filtered_repos = [r for r in repos if r.path in filter_set]
        print_status_table(filtered_repos)
    else:
        print_status_table(repos)

    # Handle validation failures
    if validation_failed and not args.skip_checks:
        print(Colors.red(
            "Validation failed. Fix the issues above or use --skip-checks to skip validation.",
        ))
        return 1

    if validation_failed and args.skip_checks:
        print(Colors.yellow("Warning: Proceeding despite validation failures (--skip-checks)"))
        print()

    # Sync-group consistency check (skip when filters are active —
    # the user explicitly selected repos and knows what they're doing)
    if not has_filters:
        print(Colors.blue("Checking sync-group consistency..."))
        print()
        sync_ok = check_sync_groups(repo_root, verbose=False)
        if not sync_ok and not args.skip_checks:
            print()
            print(Colors.red(
                "Sync groups are out of sync. Run 'grove sync' first or use --skip-checks to skip.",
            ))
            return 1
        if not sync_ok and args.skip_checks:
            print()
            print(Colors.yellow(
                "Warning: Proceeding despite sync-group inconsistency (--skip-checks)",
            ))
            print()

    # Check if anything to push
    if not repos_to_push:
        print(Colors.green("All repositories are up-to-date. Nothing to push."))
        return 0

    # Sort repos using topological sort (children before parents)
    sorted_repos = topological_sort_repos(repos_to_push)

    # Push phase
    print(Colors.blue(f"Pushing {len(sorted_repos)} repositories (bottom-up)..."))
    if args.dry_run:
        print(Colors.yellow("(dry-run mode - no actual pushes)"))
    print()

    push_failed = False
    pushed_count = 0

    for repo in sorted_repos:
        if repo.push(dry_run=args.dry_run):
            pushed_count += 1
        else:
            push_failed = True
            print(f"  {Colors.red('✗ Failed to push')} {repo.rel_path}")

    print()

    # Final summary
    if args.dry_run:
        print(f"{Colors.yellow('Dry run complete.')} Would push {pushed_count} repositories.")
        print()
        print(Colors.blue("To execute:"))
        # Reconstruct the command with filters
        cmd_parts = ["  grove push"]
        for p in filter_paths:
            cmd_parts.append(p)
        if sync_group_name:
            cmd_parts.append(f"--sync-group {sync_group_name}")
        if cascade_path:
            cmd_parts.append(f"--cascade {cascade_path}")
        print(" ".join(cmd_parts))
    elif push_failed:
        print(Colors.red("Some pushes failed."))
        print()
        print(Colors.blue("Troubleshooting:"))
        print("  - Check remote connectivity: git remote -v")
        print("  - Check authentication: ssh -T git@github.com")
        print("  - Try pushing manually: cd <repo> && git push -v")
        return 1
    else:
        print(Colors.green(f"Successfully pushed {pushed_count} repositories."))
        print()
        print(Colors.blue("Next steps:"))
        print("  1. Verify: grove check")
        print("  2. Check CI status on GitHub")

    return 0
