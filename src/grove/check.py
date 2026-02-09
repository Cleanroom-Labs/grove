"""
grove/check.py
Verifies all submodules are correctly configured: on a branch (not detached HEAD)
and all sync-group submodules at the same commit.

When no sync groups are configured (no .grove.toml), the sync-group
consistency check is skipped with a warning.

Usage (via entry point):
    grove check           # Basic check
    grove check --verbose # Show additional details
"""

import argparse
from collections import Counter
from pathlib import Path

from grove.config import get_sync_group_exclude_paths, load_config
from grove.repo_utils import Colors, RepoInfo, find_repo_root, parse_gitmodules
from grove.sync import discover_sync_submodules


def get_tag_or_branch(repo: RepoInfo) -> str | None:
    """
    Get current tag or branch name.
    Returns tag if on an exact tag, otherwise branch name, or None if detached.
    """
    # Try to get exact tag first
    result = repo.git("describe", "--exact-match", "--tags", check=False)
    if result.returncode == 0:
        return result.stdout.strip()

    # Fall back to branch name
    return repo.get_branch()


def check_repo_state(repo: RepoInfo, name: str, verbose: bool = False) -> bool:
    """
    Check if a repo is on a branch or tag.
    Returns True if healthy (on branch/tag), False if detached HEAD.
    """
    current = get_tag_or_branch(repo)

    if current:
        commit_info = ""
        if verbose:
            commit_info = f" ({repo.get_commit_sha(short=True)})"
        print(f"  {Colors.green('✓')} {name} is on: {current}{commit_info}")
        return True
    else:
        print(f"  {Colors.red('✗')} {name} is in detached HEAD state")
        print(f"      Current commit: {repo.get_commit_sha(short=True)}")
        return False


def _discover_branch_check_repos(
    repo_root: Path,
    exclude_paths: set[Path],
) -> list[tuple[str, RepoInfo]]:
    """Recursively discover submodule repos that should be on a branch.

    Walks .gitmodules at each level and returns ``(display_name, RepoInfo)``
    pairs for every submodule whose path is not in *exclude_paths*.
    """
    results: list[tuple[str, RepoInfo]] = []

    def _walk(parent: Path) -> None:
        gitmodules = parent / ".gitmodules"
        if not gitmodules.exists():
            return

        entries = parse_gitmodules(gitmodules)
        for _name, subpath, _url in entries:
            full_path = parent / subpath
            if not (full_path / ".git").exists():
                continue
            if full_path in exclude_paths:
                continue

            rel = str(full_path.relative_to(repo_root))
            results.append((rel, RepoInfo(path=full_path, repo_root=repo_root)))

            # Recurse into this submodule's own submodules
            _walk(full_path)

    _walk(repo_root)
    return results


def check_sync_groups(repo_root: Path, verbose: bool = False) -> bool:
    """
    Verify all sync-group submodules are at the same commit within each group.
    Returns True if all groups are in sync, False if any differ.
    """
    config = load_config(repo_root)

    if not config.sync_groups:
        return True

    all_ok = True
    for group in config.sync_groups.values():
        submodules = discover_sync_submodules(repo_root, group.url_match)

        if not submodules:
            print(f"  {Colors.yellow('⚠')} No submodules found for group '{group.name}'")
            continue

        allow_drift = set(group.allow_drift)

        commits: dict[str, str] = {}
        drifting: dict[str, str] = {}
        for sub in submodules:
            rel = str(sub.path.relative_to(repo_root))
            sha = sub.current_commit[:7] if sub.current_commit else "unknown"
            if rel in allow_drift:
                drifting[rel] = sha
            else:
                commits[rel] = sha

        unique_commits = set(commits.values())

        if len(unique_commits) <= 1:
            commit = next(iter(unique_commits)) if unique_commits else "—"
            print(f"  {Colors.green('✓')} All {len(commits)} {group.name} submodules at {commit}")
            if verbose:
                for rel_path in sorted(commits):
                    print(f"      {rel_path:<40} {commits[rel_path]}")
            for rel_path in sorted(drifting):
                print(f"      {rel_path:<40} {drifting[rel_path]}  {Colors.yellow('(allow-drift)')}")
        else:
            all_ok = False
            commit_counts = Counter(commits.values())
            majority_commit = commit_counts.most_common(1)[0][0]

            print(f"  {Colors.red('✗')} {group.name} submodules are NOT in sync "
                  f"({len(unique_commits)} unique commits across {len(commits)} locations)")

            for rel_path in sorted(commits):
                sha = commits[rel_path]
                if sha != majority_commit:
                    print(f"      {rel_path:<40} {sha}  {Colors.red('← differs')}")
                else:
                    print(f"      {rel_path:<40} {sha}")
            for rel_path in sorted(drifting):
                print(f"      {rel_path:<40} {drifting[rel_path]}  {Colors.yellow('(allow-drift)')}")

    return all_ok


def run(args=None) -> int:
    if not isinstance(args, argparse.Namespace):
        parser = argparse.ArgumentParser(
            description="Verify all submodules are correctly configured and in sync."
        )
        parser.add_argument(
            "--verbose", "-v",
            action="store_true",
            help="Show additional details (commits, remotes)"
        )
        args = parser.parse_args(args)

    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    config = load_config(repo_root)
    has_sync_groups = bool(config.sync_groups)

    all_healthy = True
    issues: list[str] = []

    # Collect sync-group submodule paths to exclude from branch checks
    # (sync-group submodules are expected to be on detached HEAD)
    sync_submodule_paths = get_sync_group_exclude_paths(repo_root, config)

    # Section 1: Check project submodules are on branches
    print(Colors.blue("Checking submodule branches..."))

    branch_repos = _discover_branch_check_repos(repo_root, sync_submodule_paths)

    if not branch_repos:
        print(f"  {Colors.yellow('⚠')} No submodules found")
    else:
        for name, repo in branch_repos:
            if not check_repo_state(repo, name, args.verbose):
                all_healthy = False
                issues.append("detached-head")

    print()

    # Section 2: Check sync group sync
    print(Colors.blue("Checking sync group consistency..."))

    if not has_sync_groups:
        print(f"  {Colors.yellow('⚠')} No sync groups configured — skipping sync-group checks")
    elif not check_sync_groups(repo_root, args.verbose):
        all_healthy = False
        issues.append("sync-group-out-of-sync")

    print()

    # Section 3: Summary and remediation
    if all_healthy:
        print(Colors.green("All checks passed."))
    else:
        print(Colors.red("Issues found:"))

        if "detached-head" in issues:
            print()
            print(f"  {Colors.yellow('Detached HEAD fix:')}")
            print("    cd <submodule>")
            print("    git checkout <branch-or-tag>")

        if "sync-group-out-of-sync" in issues:
            print()
            print(f"  {Colors.yellow('Sync group fix:')}")
            print("    grove sync")

    return 0 if all_healthy else 1
