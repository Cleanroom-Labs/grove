"""
grove/sync.py
Synchronize submodule sync groups across all locations.

Local-first by default: discovers all submodule instances, finds the most
advanced one (the "tip"), and syncs the rest to match.  Use ``--remote``
to resolve the target from the remote instead.

Usage (via entry point):
    grove sync                        # Sync all groups (local-first)
    grove sync common                 # Sync just "common" group
    grove sync common abc1234         # Sync "common" to specific commit
    grove sync --remote               # Resolve target from remote
    grove sync --dry-run              # Preview changes
    grove sync --no-push              # Commit only, skip pushing
    grove sync --skip-checks                # Skip remote sync validation

This module:
1. Discovers all matching submodule locations for each sync group
2. Resolves the target commit (local tip, explicit SHA, or remote)
3. Validates parent repos are in sync with remotes (prevents divergence)
4. Updates submodules to target commit
5. Commits changes bottom-up through the hierarchy
6. Pushes all changes (unless --no-push)
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from grove.config import SyncGroup, load_config
from grove.repo_utils import (
    Colors,
    RepoInfo,
    RepoStatus,
    find_repo_root,
    parse_gitmodules,
    print_status_table,
    run_git,
    topological_sort_repos,
)


@dataclass
class SyncSubmodule:
    """Information about a sync-group submodule location."""

    path: Path
    parent_repo: Path
    submodule_rel_path: str  # Path relative to parent repo
    current_commit: str | None = None

    def git(
        self, *args: str, check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command in this submodule."""
        return run_git(self.path, *args, check=check, capture=capture)

    def get_current_commit(self) -> str | None:
        """Get current HEAD commit."""
        result = self.git("rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def update_to_commit(
        self,
        commit: str,
        dry_run: bool = False,
        source_path: Path | None = None,
    ) -> bool:
        """Update submodule to target commit.

        Args:
            commit: Target commit SHA.
            dry_run: Preview only.
            source_path: Path to a local repo that has the commit.
                When set, fetches from that path instead of all remotes.
        """
        if dry_run:
            return True

        if source_path:
            self.git("fetch", str(source_path), check=False)
        else:
            self.git("fetch", "--all", "--quiet", check=False)

        result = self.git("checkout", commit, "--quiet", check=False)
        return result.returncode == 0


def resolve_remote_url(repo_root: Path, url_match: str) -> str | None:
    """Return the first matching remote URL from any nested ``.gitmodules`` file."""
    for gitmodules_path in _iter_gitmodules_paths(repo_root):
        entries = parse_gitmodules(gitmodules_path, url_match=url_match)
        if entries:
            _name, _path, url = entries[0]
            return url
    return None


def _iter_gitmodules_paths(repo_root: Path):
    """Yield ``.gitmodules`` paths beneath *repo_root* in stable order."""
    repo_root = repo_root.resolve()
    for gitmodules_path in sorted(repo_root.rglob(".gitmodules")):
        if "node_modules" in gitmodules_path.parts:
            continue
        yield gitmodules_path


def discover_sync_submodules(repo_root: Path, url_match: str) -> list[SyncSubmodule]:
    """Discover all submodule locations matching *url_match* by parsing .gitmodules files."""
    submodules = []

    for gitmodules_path in _iter_gitmodules_paths(repo_root):
        parent_repo = gitmodules_path.parent
        entries = parse_gitmodules(gitmodules_path, url_match=url_match)

        for _name, submodule_path, _url in entries:
            full_path = parent_repo / submodule_path

            if not (full_path / ".git").exists():
                continue

            submodule = SyncSubmodule(
                path=full_path,
                parent_repo=parent_repo,
                submodule_rel_path=submodule_path,
            )
            submodule.current_commit = submodule.get_current_commit()
            submodules.append(submodule)

    return submodules


def get_parent_repos_for_submodules(
    submodules: list[SyncSubmodule],
    repo_root: Path,
    all_repos: list[RepoInfo] | None = None,
) -> list[RepoInfo]:
    """
    Get the parent repos that will need commits after updating submodules.
    Returns repos sorted by depth (deepest first for bottom-up commits).

    When *all_repos* is provided (from ``discover_repos_from_gitmodules``),
    parent pointers are followed directly.  Otherwise the repo tree is
    discovered on the fly.
    """
    if all_repos is None:
        from grove.repo_utils import discover_repos_from_gitmodules

        all_repos = discover_repos_from_gitmodules(repo_root)

    path_to_repo = {r.path: r for r in all_repos}
    collected: dict[Path, RepoInfo] = {}

    for submodule in submodules:
        repo = path_to_repo.get(submodule.parent_repo)
        while repo is not None and repo.path not in collected:
            collected[repo.path] = repo
            repo = repo.parent

    # Always include root
    root_repo = path_to_repo.get(repo_root)
    if root_repo is not None and repo_root not in collected:
        collected[repo_root] = root_repo

    repos = list(collected.values())
    repos.sort(key=lambda r: -r.depth)

    return repos


def resolve_target_commit(
    commit_arg: str | None,
    standalone_repo: Path | None,
    remote_url: str | None = None,
) -> tuple[str, str]:
    """
    Resolve the target commit SHA.

    Resolution order:
    1. Explicit CLI SHA (``commit_arg``)
    2. Local standalone repo (``standalone_repo``)
    3. ``git ls-remote`` against *remote_url*

    Args:
        commit_arg: Explicit commit SHA from CLI, or None for latest.
        standalone_repo: Path to a local clone, or None.
        remote_url: Submodule remote URL for ``git ls-remote`` fallback.

    Returns:
        Tuple of (full_sha, source_description)
    """
    if commit_arg:
        if not re.match(r"^[a-f0-9]{7,40}$", commit_arg):
            raise ValueError(f"Invalid commit SHA: {commit_arg}")
        return (commit_arg, "CLI argument")

    # --- local standalone repo ---
    if standalone_repo is not None:
        if not standalone_repo.exists():
            raise ValueError(
                f"Standalone repo not found at {standalone_repo}\n"
                "Please specify a commit SHA explicitly."
            )

        result = run_git(standalone_repo, "remote", "get-url", "origin", check=False)

        if result.returncode == 0:
            run_git(standalone_repo, "fetch", "origin", "main", "--quiet", check=False)
            result = run_git(standalone_repo, "rev-parse", "origin/main", check=False)
            if result.returncode == 0:
                return (result.stdout.strip(), f"origin/main from {standalone_repo}")

            result = run_git(standalone_repo, "rev-parse", "origin/HEAD", check=False)
            if result.returncode == 0:
                return (result.stdout.strip(), f"origin/HEAD from {standalone_repo}")

        # Fallback to local main
        result = run_git(standalone_repo, "rev-parse", "main", check=False)
        if result.returncode == 0:
            return (result.stdout.strip(), f"main from {standalone_repo}")

        result = run_git(standalone_repo, "rev-parse", "HEAD", check=False)
        if result.returncode == 0:
            return (result.stdout.strip(), f"HEAD from {standalone_repo}")

        raise ValueError(f"Could not resolve commit from {standalone_repo}")

    # --- git ls-remote fallback ---
    if remote_url is not None:
        result = subprocess.run(
            ["git", "ls-remote", remote_url, "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(
                f"git ls-remote failed for {remote_url}\n"
                "Check network connectivity or specify a commit SHA explicitly."
            )
        line = result.stdout.strip()
        if not line:
            raise ValueError(
                f"No HEAD found at {remote_url}\nSpecify a commit SHA explicitly."
            )
        sha = line.split()[0]
        return (sha, f"HEAD from {remote_url}")

    raise ValueError(
        "Cannot resolve target commit: no standalone-repo configured and no remote URL found.\n"
        "Please specify a commit SHA explicitly."
    )


def resolve_local_tip(
    submodules: list[SyncSubmodule],
    repo_root: Path,
) -> tuple[str, Path, str] | None:
    """Find the most advanced commit among local submodule instances.

    Returns ``(commit_sha, source_path, description)`` when a single tip
    exists, or ``None`` when commits have diverged (no linear ordering).
    """
    commits: dict[str, SyncSubmodule] = {}
    for sub in submodules:
        if sub.current_commit and sub.current_commit not in commits:
            commits[sub.current_commit] = sub

    if not commits:
        return None

    if len(commits) == 1:
        sha, sub = next(iter(commits.items()))
        rel = str(sub.path.relative_to(repo_root))
        return (sha, sub.path, f"local tip from {rel}")

    # Find the tip: the commit that is a descendant of all others.
    items = list(commits.items())
    tip_sha, tip_sub = items[0]

    for sha, sub in items[1:]:
        # Is current tip an ancestor of sha?  Then sha is more advanced.
        result = run_git(
            sub.path,
            "merge-base",
            "--is-ancestor",
            tip_sha,
            sha,
            check=False,
        )
        if result.returncode == 0:
            tip_sha, tip_sub = sha, sub
            continue

        # Is sha an ancestor of current tip?  Then tip stays.
        result = run_git(
            tip_sub.path,
            "merge-base",
            "--is-ancestor",
            sha,
            tip_sha,
            check=False,
        )
        if result.returncode == 0:
            continue

        # Diverged — no linear ordering.
        return None

    # Verify tip is actually a descendant of every commit (handles non-transitive edge cases).
    for sha in commits:
        if sha == tip_sha:
            continue
        result = run_git(
            tip_sub.path,
            "merge-base",
            "--is-ancestor",
            sha,
            tip_sha,
            check=False,
        )
        if result.returncode != 0:
            return None

    rel = str(tip_sub.path.relative_to(repo_root))
    return (tip_sha, tip_sub.path, f"local tip from {rel}")


def commit_submodule_changes(
    parent_repo: RepoInfo,
    submodule_paths: list[str],
    message: str,
    dry_run: bool = False,
) -> bool:
    """
    Commit submodule changes in a parent repo.
    Returns True if a commit was made.
    """
    has_changes = False
    for subpath in submodule_paths:
        result = parent_repo.git("diff", "--quiet", subpath, check=False)
        if result.returncode != 0:
            has_changes = True
            break

    if not has_changes:
        return False

    if dry_run:
        print(f"  {Colors.yellow('Would commit')} in {parent_repo.rel_path}: {message}")
        return True

    for subpath in submodule_paths:
        parent_repo.git("add", subpath, check=False)

    result = parent_repo.git("diff", "--cached", "--quiet", check=False)
    if result.returncode == 0:
        return False

    parent_repo.git("commit", "-m", message, check=False)
    print(f"  {Colors.green('Committed')} in {parent_repo.rel_path}: {message}")
    return True


def push_ahead_submodules(
    submodules: list[SyncSubmodule],
    dry_run: bool = False,
) -> bool:
    """
    Push any sync-group submodules that are ahead of their remotes.

    Ensures changes are available on the remote before syncing to other
    locations in the tree.

    Returns True if any were pushed.
    """
    pushed_any = False

    for submodule in submodules:
        branch_result = submodule.git("branch", "--show-current", check=False)
        branch = branch_result.stdout.strip()
        if not branch:
            continue

        submodule.git("fetch", "origin", "--quiet", check=False)

        ahead_result = submodule.git(
            "rev-list", "--count", f"origin/{branch}..HEAD", check=False
        )
        if ahead_result.returncode != 0:
            continue

        ahead_count = ahead_result.stdout.strip()
        if ahead_count and ahead_count != "0":
            rel_path = submodule.submodule_rel_path
            if dry_run:
                print(
                    f"  {Colors.yellow('Would push')} {rel_path} ({ahead_count} commits ahead)"
                )
            else:
                result = submodule.git(
                    "push", "origin", branch, check=False, capture=False
                )
                if result.returncode == 0:
                    print(
                        f"  {Colors.green('Pushed')} {rel_path} ({ahead_count} commits)"
                    )
                    pushed_any = True
                else:
                    print(f"  {Colors.red('Failed to push')} {rel_path}")

    return pushed_any


def _display_group_discovery(
    quiet: bool,
    repo_root: Path,
    target_commit: str,
    all_submodules: list[SyncSubmodule],
    allow_drift: set[str],
) -> None:
    if quiet:
        return

    print(f"Found {Colors.green(str(len(all_submodules)))} submodule locations:")
    target_short = target_commit[:7]
    for submodule in all_submodules:
        rel_path = str(submodule.path.relative_to(repo_root))
        current = (
            submodule.current_commit[:7] if submodule.current_commit else "unknown"
        )
        if rel_path in allow_drift:
            print(
                f"  {Colors.yellow('~')} {rel_path} ({current}) {Colors.yellow('(allow-drift, skipped)')}"
            )
        elif current == target_short:
            print(f"  {Colors.green('✓')} {rel_path} (already at {current})")
        else:
            print(f"  {Colors.yellow('→')} {rel_path} ({current} → {target_short})")
    print()


def _resolve_group_target(
    group: SyncGroup,
    repo_root: Path,
    commit_arg: str | None,
    remote: bool,
    submodules: list[SyncSubmodule],
    dry_run: bool,
    force: bool,
    quiet: bool,
    source_path: Path | None,
) -> tuple[str, str, Path | None]:
    if commit_arg:
        if not quiet:
            print(Colors.blue("Resolving target commit..."))
        target_commit, commit_source = resolve_target_commit(commit_arg, None)
        return target_commit, commit_source, source_path

    if remote:
        if not quiet:
            print(Colors.blue("Checking for ahead submodules..."))
        if push_ahead_submodules(submodules, dry_run) and not quiet:
            print()

        if not quiet:
            print(Colors.blue("Resolving target commit from remote..."))
        remote_url = resolve_remote_url(repo_root, group.url_match)
        target_commit, commit_source = resolve_target_commit(
            None,
            group.standalone_repo,
            remote_url=remote_url,
        )
        return target_commit, commit_source, source_path

    if not quiet:
        print(Colors.blue("Resolving target commit from local instances..."))
    tip = resolve_local_tip(submodules, repo_root)
    if tip is not None:
        target_commit, tip_source_path, commit_source = tip
        return target_commit, commit_source, tip_source_path

    from grove.sync_merge import attempt_divergence_merge

    merge_result = attempt_divergence_merge(
        group.name,
        submodules,
        repo_root,
        group.standalone_repo,
        dry_run,
        force,
    )
    if merge_result is None:
        raise ValueError("__PAUSED_OR_FAILED__")

    target_commit, merge_source_path, commit_source = merge_result
    return target_commit, commit_source, merge_source_path


def _submodules_needing_update(
    submodules: list[SyncSubmodule],
    target_commit: str,
) -> list[SyncSubmodule]:
    return [
        s
        for s in submodules
        if not s.current_commit or s.current_commit[:7] != target_commit[:7]
    ]


def _validate_parent_repositories(
    submodules: list[SyncSubmodule],
    repo_root: Path,
    quiet: bool,
    force: bool,
) -> tuple[int, list[RepoInfo]]:
    if not quiet:
        print(Colors.blue("Validating parent repositories..."))
    parent_repos = get_parent_repos_for_submodules(submodules, repo_root)

    if not quiet:
        print("  Fetching from remotes...")
    for repo in parent_repos:
        repo.git("fetch", "--quiet", check=False)

    validation_failed = False
    for repo in parent_repos:
        if repo.validate(check_sync=True):
            continue
        if not quiet:
            print(f"  {Colors.red('✗')} {repo.rel_path}")
            print(f"    {Colors.red(repo.error_message or 'Unknown error')}")
        validation_failed = True

    if not quiet:
        print_status_table(parent_repos, show_behind=True)

    if validation_failed and not force:
        print(
            Colors.red(
                "Validation failed. Fix the issues above or use --skip-checks to skip."
            )
        )
        if not quiet:
            print()
            print(Colors.blue("Common fixes:"))
            print("  - Pull latest: cd <repo> && git pull")
            print("  - Checkout branch: cd <repo> && git checkout main")
        return 1, parent_repos

    if validation_failed and force and not quiet:
        print(
            Colors.yellow(
                "Warning: Proceeding despite validation failures (--skip-checks)"
            )
        )
        print()

    return 0, parent_repos


def _update_group_submodules(
    submodules_to_update: list[SyncSubmodule],
    repo_root: Path,
    group_name: str,
    target_commit: str,
    dry_run: bool,
    quiet: bool,
    source_path: Path | None,
) -> list[SyncSubmodule]:
    if not quiet:
        print(Colors.blue(f"Updating {group_name} submodules..."))
    updated_submodules: list[SyncSubmodule] = []

    for submodule in submodules_to_update:
        rel_path = str(submodule.path.relative_to(repo_root))
        if dry_run:
            if not quiet:
                print(f"  {Colors.yellow('Would update')} {rel_path}")
            updated_submodules.append(submodule)
            continue

        if submodule.update_to_commit(target_commit, source_path=source_path):
            if not quiet:
                print(f"  {Colors.green('Updated')} {rel_path}")
            updated_submodules.append(submodule)
        else:
            print(f"  {Colors.red('Failed to update')} {rel_path}")

    if not quiet:
        print()
    return updated_submodules


def _commit_group_updates(
    updated_submodules: list[SyncSubmodule],
    parent_repos: list[RepoInfo],
    commit_message: str,
    dry_run: bool,
    quiet: bool,
) -> list[RepoInfo]:
    if not quiet:
        print(Colors.blue("Committing changes bottom-up..."))

    parent_to_subpaths: dict[Path, list[str]] = {}
    for submodule in updated_submodules:
        parent_to_subpaths.setdefault(submodule.parent_repo, []).append(
            submodule.submodule_rel_path
        )

    committed_repos: list[RepoInfo] = []
    for repo in parent_repos:
        subpaths = parent_to_subpaths.get(repo.path, [])

        # Also pick up indirect changes (e.g. child repos that received
        # sync commits, updating the parent's submodule pointers).
        result = repo.git("diff", "--name-only", check=False)
        if result.returncode == 0 and result.stdout.strip():
            existing = set(subpaths)
            for changed_path in result.stdout.strip().split("\n"):
                if changed_path and changed_path not in existing:
                    subpaths.append(changed_path)

        if not subpaths:
            continue

        if commit_submodule_changes(
            repo,
            subpaths,
            commit_message,
            dry_run=dry_run,
        ):
            committed_repos.append(repo)

    if not quiet:
        print()
    return committed_repos


def _handle_no_push(
    group: SyncGroup,
    target_commit: str,
    updated_submodules: list[SyncSubmodule],
    quiet: bool,
) -> int:
    if quiet:
        print(
            f"    Synced {group.name} to {target_commit[:7]} "
            f"across {len(updated_submodules)} locations"
        )
    else:
        print(Colors.yellow("Skipping push (--no-push specified)"))
        print()
        print(Colors.blue("Next steps:"))
        print("  1. Verify: grove check")
        print("  2. Push:   grove push")
    return 0


def _collect_repos_to_push(parent_repos: list[RepoInfo]) -> list[RepoInfo]:
    repos_to_push: list[RepoInfo] = []
    for repo in parent_repos:
        repo.ahead_count = None
        repo.behind_count = None
        repo.status = RepoStatus.OK
        if repo.validate() and repo.status == RepoStatus.PENDING:
            repos_to_push.append(repo)
    return repos_to_push


def _push_group_repositories(
    repos_to_push: list[RepoInfo],
    dry_run: bool,
    quiet: bool,
) -> tuple[bool, int]:
    if not quiet:
        print(Colors.blue(f"Pushing {len(repos_to_push)} repositories..."))
        if dry_run:
            print(Colors.yellow("(dry-run mode - no actual pushes)"))
        print()

    push_failed = False
    pushed_count = 0
    for repo in topological_sort_repos(repos_to_push):
        if repo.push(dry_run=dry_run):
            pushed_count += 1
        else:
            push_failed = True
            print(f"  {Colors.red('✗ Failed to push')} {repo.rel_path}")

    if not quiet:
        print()
    return push_failed, pushed_count


def _print_sync_final_summary(
    *,
    group: SyncGroup,
    target_commit: str,
    updated_submodules: list[SyncSubmodule],
    committed_repos: list[RepoInfo],
    repos_to_push: list[RepoInfo],
    dry_run: bool,
    push_failed: bool,
    pushed_count: int,
    quiet: bool,
) -> int:
    if dry_run:
        if not quiet:
            print(f"{Colors.yellow('Dry run complete.')}")
            print()
            print(Colors.blue("Summary:"))
            print(f"  Target commit: {target_commit[:7]}")
            print(f"  Submodules to update: {len(updated_submodules)}")
            print(f"  Commits to make: {len(committed_repos)}")
            print(f"  Repos to push: {len(repos_to_push)}")
            print()
            print(Colors.blue("To execute:"))
            print(f"  grove sync {group.name}")
        return 0

    if push_failed:
        print(Colors.red("Some pushes failed."))
        if not quiet:
            print()
            print(Colors.blue("Troubleshooting:"))
            print("  - Check remote connectivity: git remote -v")
            print("  - Try pushing manually: grove push")
        return 1

    if quiet:
        print(
            f"    Synced {group.name} to {target_commit[:7]} "
            f"({len(updated_submodules)} updated, {pushed_count} pushed)"
        )
    else:
        print(Colors.green(f"Sync complete for {group.name}!"))
        print()
        print(Colors.blue("Summary:"))
        print(f"  Target commit: {target_commit[:7]}")
        print(f"  Submodules updated: {len(updated_submodules)}")
        print(f"  Repos pushed: {pushed_count}")
        print()
        print(Colors.blue("Next steps:"))
        print("  1. Verify: grove check")
    return 0


def _sync_group(
    group: SyncGroup,
    repo_root: Path,
    commit_arg: str | None,
    dry_run: bool,
    no_push: bool,
    force: bool,
    remote: bool = False,
    quiet: bool = False,
    source_path: Path | None = None,
) -> int:
    """Sync a single sync group. Returns 0 on success, 1 on failure.

    Args:
        quiet: Suppress verbose output (used when called from merge).
        source_path: Local path to fetch the target commit from.  When set
            with *commit_arg*, each submodule instance fetches from this path
            instead of ``--all`` remotes.
    """
    if not quiet:
        print(Colors.blue(f"=== Syncing group: {group.name} ==="))
        print()

    # Phase 1: Discover submodules
    if not quiet:
        print(Colors.blue(f"Discovering {group.name} submodule locations..."))
    all_submodules = discover_sync_submodules(repo_root, group.url_match)

    if not all_submodules:
        print(Colors.red(f"Error: No submodules found matching '{group.url_match}'"))
        return 1

    allow_drift = set(group.allow_drift)
    submodules = [
        s
        for s in all_submodules
        if str(s.path.relative_to(repo_root)) not in allow_drift
    ]

    # Phase 2: Resolve target commit
    try:
        target_commit, commit_source, source_path = _resolve_group_target(
            group,
            repo_root,
            commit_arg,
            remote,
            submodules,
            dry_run,
            force,
            quiet,
            source_path,
        )
    except ValueError as e:
        if str(e) == "__PAUSED_OR_FAILED__":
            return 1
        print(Colors.red(f"Error: {e}"))
        return 1

    if not quiet:
        print(f"Target: {Colors.green(target_commit[:7])} ({commit_source})")
        print()
    _display_group_discovery(
        quiet,
        repo_root,
        target_commit,
        all_submodules,
        allow_drift,
    )

    # Check if any updates needed
    submodules_to_update = _submodules_needing_update(submodules, target_commit)

    if not submodules_to_update:
        if not quiet:
            print(
                Colors.green(
                    f"All {group.name} submodules already at target commit. Nothing to do."
                )
            )
        return 0

    # Phase 3: Validate parent repos
    validation_result, parent_repos = _validate_parent_repositories(
        submodules,
        repo_root,
        quiet,
        force,
    )
    if validation_result != 0:
        return validation_result

    if dry_run and not quiet:
        print(Colors.yellow("Dry run mode - previewing changes:"))
        print()

    # Phase 4: Update submodules
    updated_submodules = _update_group_submodules(
        submodules_to_update,
        repo_root,
        group.name,
        target_commit,
        dry_run,
        quiet,
        source_path,
    )

    if not updated_submodules:
        if not quiet:
            print(Colors.yellow("No submodules were updated."))
        return 0

    # Phase 5: Commit bottom-up
    commit_message = group.commit_message.format(
        group=group.name, sha=target_commit[:7]
    )

    committed_repos = _commit_group_updates(
        updated_submodules,
        parent_repos,
        commit_message,
        dry_run,
        quiet,
    )

    # Phase 6: Push (unless --no-push)
    if no_push:
        return _handle_no_push(group, target_commit, updated_submodules, quiet)

    if not committed_repos and not dry_run:
        if quiet:
            print(f"    Synced {group.name} to {target_commit[:7]} (no changes needed)")
        else:
            print(Colors.green("No commits made - nothing to push."))
        return 0

    # Re-validate repos to get accurate ahead counts
    repos_to_push = _collect_repos_to_push(parent_repos)

    if not repos_to_push and not dry_run:
        if quiet:
            print(f"    Synced {group.name} to {target_commit[:7]} (all up-to-date)")
        else:
            print(Colors.green("All repositories up-to-date. Nothing to push."))
        return 0

    push_failed, pushed_count = _push_group_repositories(repos_to_push, dry_run, quiet)
    return _print_sync_final_summary(
        group=group,
        target_commit=target_commit,
        updated_submodules=updated_submodules,
        committed_repos=committed_repos,
        repos_to_push=repos_to_push,
        dry_run=dry_run,
        push_failed=push_failed,
        pushed_count=pushed_count,
        quiet=quiet,
    )


def run(args) -> int:
    # Dispatch sync merge operations
    if getattr(args, "continue_sync", False):
        from grove.sync_merge import continue_sync_merge

        return continue_sync_merge()
    if getattr(args, "abort", False):
        from grove.sync_merge import abort_sync_merge

        return abort_sync_merge()
    if getattr(args, "status", False):
        from grove.sync_merge import show_sync_merge_status

        return show_sync_merge_status()

    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    config = load_config(repo_root)

    if not config.sync_groups:
        print(Colors.yellow("No sync groups configured. Nothing to sync."))
        return 0

    # Disambiguate: if group arg doesn't match a sync group name and
    # looks like a commit SHA, treat it as a commit for all groups.
    group_name = args.group
    commit_arg = args.commit

    if group_name and group_name not in config.sync_groups:
        if re.match(r"^[a-f0-9]{7,40}$", group_name):
            commit_arg = group_name
            group_name = None
        else:
            print(Colors.red(f"Unknown sync group: {group_name}"))
            print(f"Available groups: {', '.join(config.sync_groups)}")
            return 1

    if group_name:
        groups = [config.sync_groups[group_name]]
    else:
        groups = list(config.sync_groups.values())

    exit_code = 0
    for group in groups:
        result = _sync_group(
            group,
            repo_root,
            commit_arg,
            dry_run=args.dry_run,
            no_push=args.no_push,
            force=args.skip_checks,
            remote=args.remote,
        )
        if result != 0:
            exit_code = result

    return exit_code
