"""
grove/worktree_merge.py
Automated bottom-up merge across nested submodule repositories.

Merges a feature branch into the current branch across all repos in the
submodule tree, processing leaves first (topological order). Supports
pause/resume on conflicts or test failures, and full abort/rollback.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from grove.filelock import atomic_write_json, locked_open

from grove.config import GroveConfig, MergeConfig, get_sync_group_exclude_paths, load_config
from grove.repo_utils import (
    Colors,
    RepoInfo,
    discover_repos_from_gitmodules,
    find_repo_root,
    get_git_common_dir,
    get_git_worktree_dir,
    parse_gitmodules,
    run_git,
    topological_sort_repos,
)
from grove.topology import TopologyCache


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

@dataclass
class RepoMergeEntry:
    """Merge state for a single repository."""
    rel_path: str
    status: str = "pending"  # pending | skipped | merged | paused
    pre_merge_head: str | None = None
    post_merge_head: str | None = None
    reason: str | None = None  # already-merged | conflict | test-failed | ...
    sync_group: str | None = None  # sync group name if this is a sync-group canonical repo


@dataclass
class MergeState:
    """Persistent merge state across CLI invocations."""
    branch: str
    no_ff: bool
    no_test: bool
    started_at: str
    repos: list[RepoMergeEntry]
    pre_sync_heads: dict[str, str] = field(default_factory=dict)

    def save(self, state_path: Path) -> None:
        data = {
            "branch": self.branch,
            "no_ff": self.no_ff,
            "no_test": self.no_test,
            "started_at": self.started_at,
            "repos": [asdict(r) for r in self.repos],
            "pre_sync_heads": self.pre_sync_heads,
        }
        atomic_write_json(state_path, json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, state_path: Path) -> MergeState:
        with locked_open(state_path, "r", shared=True) as f:
            data = json.loads(f.read())
        repos = [RepoMergeEntry(**r) for r in data["repos"]]
        return cls(
            branch=data["branch"],
            no_ff=data["no_ff"],
            no_test=data["no_test"],
            started_at=data["started_at"],
            repos=repos,
            pre_sync_heads=data.get("pre_sync_heads", {}),
        )

    @classmethod
    def remove(cls, state_path: Path) -> None:
        state_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_state_path(repo_root: Path) -> Path:
    """Per-worktree merge state file."""
    return get_git_worktree_dir(repo_root) / "grove" / "merge-state.json"


def _get_journal_path(repo_root: Path) -> Path:
    """Shared merge journal with monthly rotation."""
    now = datetime.now(timezone.utc)
    filename = f"merge-journal-{now.strftime('%Y-%m')}.log"
    return get_git_common_dir(repo_root) / "grove" / filename


def _log(journal_path: Path, message: str) -> None:
    """Append a timestamped entry to the merge journal."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with locked_open(journal_path, "a") as f:
        f.write(f"[{ts}] {message}\n")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _has_branch(repo: RepoInfo, branch: str) -> bool:
    """Shim — delegates to ``repo.has_local_branch``."""
    return repo.has_local_branch(branch)


def _is_ancestor(repo: RepoInfo, branch: str) -> bool:
    """Shim — delegates to ``repo.is_ancestor``."""
    return repo.is_ancestor(branch)


def _count_divergent_commits(repo: RepoInfo, branch: str) -> tuple[int, int]:
    """Shim — delegates to ``repo.count_divergent_commits``."""
    return repo.count_divergent_commits(branch)


def _get_unmerged_files(repo: RepoInfo) -> list[str]:
    """Shim — delegates to ``repo.get_unmerged_files``."""
    return repo.get_unmerged_files()


def _has_merge_head(repo: RepoInfo) -> bool:
    """Shim — delegates to ``repo.has_merge_head``."""
    return repo.has_merge_head()


# ---------------------------------------------------------------------------
# Conflict prediction
# ---------------------------------------------------------------------------

def _predict_conflicts(repo: RepoInfo, branch: str) -> tuple[bool, list[str]]:
    """Simulate a merge to predict conflicts.

    Returns (clean, conflicting_files). The working tree is restored
    after the simulation.
    """
    # Try a --no-commit merge
    result = repo.git(
        "merge", "--no-commit", "--no-ff", branch, check=False, capture=True
    )
    clean = result.returncode == 0
    conflicting = []
    if not clean:
        conflicting = repo.get_unmerged_files()
    # Abort the simulated merge
    if repo.has_merge_head():
        repo.git("merge", "--abort", check=False)
    elif clean:
        # Clean merge was staged but not committed — reset
        repo.git("reset", "--merge", check=False)
    return (clean, conflicting)


# ---------------------------------------------------------------------------
# Submodule conflict auto-resolution
# ---------------------------------------------------------------------------

def _auto_resolve_submodule_conflicts(
    repo: RepoInfo, merged_child_rel_paths: set[str]
) -> bool:
    """Try to auto-resolve submodule pointer conflicts.

    For conflicts that correspond to already-merged child submodules,
    stage the current (just-merged) version. Returns True if all
    conflicts are resolved.
    """
    unmerged = repo.get_unmerged_files()
    if not unmerged:
        return True

    # Get submodule paths from .gitmodules
    gitmodules_path = repo.path / ".gitmodules"
    submodule_paths = set()
    if gitmodules_path.exists():
        for _name, sm_path, _url in parse_gitmodules(gitmodules_path):
            submodule_paths.add(sm_path)

    all_resolved = True
    for conflict_path in unmerged:
        if conflict_path in submodule_paths:
            # Check if this submodule was already merged
            child_rel = str(
                (repo.path / conflict_path).relative_to(repo.repo_root)
            )
            if child_rel in merged_child_rel_paths:
                repo.git("add", conflict_path, check=False)
                continue
        all_resolved = False

    if all_resolved and not repo.get_unmerged_files():
        return True
    return not bool(repo.get_unmerged_files())


def _get_submodule_conflict_guidance(
    repo: RepoInfo, conflicting_files: list[str]
) -> list[str]:
    """Generate resolution guidance for submodule pointer conflicts.

    For each conflicting file that is a submodule, returns formatted
    guidance showing ``git update-index --cacheinfo`` commands to
    accept either side. Returns an empty list when no submodule
    conflicts are found.
    """
    gitmodules_path = repo.path / ".gitmodules"
    if not gitmodules_path.exists():
        return []

    submodule_paths = set()
    for _name, sm_path, _url in parse_gitmodules(gitmodules_path):
        submodule_paths.add(sm_path)

    sm_conflicts = [f for f in conflicting_files if f in submodule_paths]
    if not sm_conflicts:
        return []

    lines: list[str] = []
    lines.append("")
    lines.append(
        f"  {Colors.yellow('Submodule pointer conflicts')} "
        "(git checkout --ours/--theirs will NOT work):"
    )

    for sm_path in sm_conflicts:
        result = repo.git("ls-files", "-u", "--", sm_path, check=False, capture=True)
        ours_sha = theirs_sha = None
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                stage = parts[2]
                if stage == "2":
                    ours_sha = parts[1]
                elif stage == "3":
                    theirs_sha = parts[1]

        lines.append(f"    {Colors.blue(sm_path)}:")
        if ours_sha:
            lines.append(
                f"      Accept ours:   git update-index --cacheinfo 160000,{ours_sha},{sm_path}"
            )
        if theirs_sha:
            lines.append(
                f"      Accept theirs: git update-index --cacheinfo 160000,{theirs_sha},{sm_path}"
            )

    return lines


# ---------------------------------------------------------------------------
# Sync-group helpers
# ---------------------------------------------------------------------------

def _find_canonical_sync_instance(
    submodules,  # list[SyncSubmodule] from grove.sync
    standalone_repo: Path | None,
    branch: str,
    repo_root: Path,
) -> RepoInfo | None:
    """Find the sync-group instance to merge for a feature branch.

    Priority:
    1. *standalone_repo* if configured and has the branch
    2. Shallowest submodule instance (closest to root) with the branch
    3. ``None`` if no instance has the branch
    """
    if standalone_repo and standalone_repo.exists():
        repo = RepoInfo(path=standalone_repo, repo_root=repo_root)
        if repo.has_local_branch(branch):
            return repo

    sorted_subs = sorted(submodules, key=lambda s: len(s.path.parts))
    for sub in sorted_subs:
        repo = RepoInfo(path=sub.path, repo_root=repo_root)
        if repo.has_local_branch(branch):
            return repo

    return None


def _run_sync_propagation(
    group_name: str,
    canonical_path: Path,
    merged_sha: str,
    repo_root: Path,
    config: GroveConfig,
    journal_path: Path,
    merged_child_rel_paths: set[str],
) -> int:
    """Run sync propagation after a sync-group canonical instance is merged.

    Updates all instances of the sync group to *merged_sha* and adds their
    rel_paths to *merged_child_rel_paths*.  Returns 0 on success, 1 on failure.
    """
    from grove.sync import _sync_group as run_sync_group, discover_sync_submodules

    group = config.sync_groups[group_name]

    _log(journal_path,
         f"SYNC-GROUP {group_name}: syncing to {merged_sha[:8]}")

    sync_rc = run_sync_group(
        group, repo_root,
        commit_arg=merged_sha,
        dry_run=False,
        no_push=True,
        force=True,
        quiet=True,
        source_path=canonical_path,
    )
    if sync_rc != 0:
        print(f"    {Colors.red('✗')} sync failed for group '{group_name}'")
        _log(journal_path, f"SYNC-GROUP {group_name}: sync FAILED")
        return 1

    _log(journal_path,
         f"SYNC-GROUP {group_name}: synced to {merged_sha[:8]}")

    # Add all instance paths to merged_child_rel_paths
    submodules = discover_sync_submodules(repo_root, group.url_match)
    for sub in submodules:
        merged_child_rel_paths.add(str(sub.path.relative_to(repo_root)))

    return 0


def _merge_and_sync_groups(
    repo_root: Path,
    config: GroveConfig,
    branch: str,
    state: MergeState,
    state_path: Path,
    journal_path: Path,
    merged_child_rel_paths: set[str],
) -> int:
    """Merge sync-group submodules and propagate via grove sync.

    For each sync group whose canonical instance has *branch*, merges the
    feature branch there, then runs sync to propagate the merged result to
    all instances in the tree.

    Returns 0 on success, 1 if paused (conflict/test failure).
    """
    from grove.sync import discover_sync_submodules, get_parent_repos_for_submodules

    if not config.sync_groups:
        return 0

    found_any = False

    for group_name, group in config.sync_groups.items():
        submodules = discover_sync_submodules(repo_root, group.url_match)
        if not submodules:
            continue

        canonical = _find_canonical_sync_instance(
            submodules, group.standalone_repo, branch, repo_root,
        )
        if canonical is None:
            print(f"  {Colors.yellow('·')} sync group '{group_name}': "
                  f"branch '{branch}' not found, skipping")
            continue

        found_any = True
        canonical_rel = canonical.rel_path
        print(f"  {Colors.green('→')} sync group '{group_name}': "
              f"merging in {canonical_rel}")

        # Record pre-sync HEADs for all repos that sync will touch
        parent_repos = get_parent_repos_for_submodules(submodules, repo_root)
        for rp_info in parent_repos:
            rel = rp_info.rel_path
            sha = rp_info.get_commit_sha(short=False)
            if sha and sha != "unknown" and rel not in state.pre_sync_heads:
                state.pre_sync_heads[rel] = sha

        for sub in submodules:
            rel = str(sub.path.relative_to(repo_root))
            sha = sub.get_current_commit()
            if sha and rel not in state.pre_sync_heads:
                state.pre_sync_heads[rel] = sha

        # Validate canonical instance
        if canonical.has_uncommitted_changes():
            print(f"    {Colors.red('✗')} {canonical_rel}: has uncommitted changes")
            return 1

        # Create entry and add to state
        entry = RepoMergeEntry(
            rel_path=canonical_rel,
            sync_group=group_name,
        )
        state.repos.append(entry)

        # Merge the feature branch
        rc = _execute_merge_for_repo(
            canonical, entry, state, state_path, journal_path,
            config.merge, merged_child_rel_paths,
        )
        if rc != 0:
            return rc  # Paused on conflict or test failure

        # Propagate via sync
        merged_sha = canonical.get_commit_sha(short=False)
        sync_rc = _run_sync_propagation(
            group_name, canonical.path, merged_sha, repo_root,
            config, journal_path, merged_child_rel_paths,
        )
        if sync_rc != 0:
            return 1

        merged_child_rel_paths.add(canonical_rel)

    if not found_any:
        print(f"  {Colors.yellow('·')} no sync groups have branch '{branch}'")

    return 0


# ---------------------------------------------------------------------------
# Test command resolution and execution
# ---------------------------------------------------------------------------

def _get_test_command(root_config: MergeConfig, repo: RepoInfo) -> str | None:
    """Resolve test command using priority order:

    1. Root's test-overrides[repo's rel_path]
    2. Repo's own .grove.toml test-command
    3. Root's test-command
    4. None (skip)
    """
    # Use "." for root repo rel_path
    rel = repo.rel_path if repo.path != repo.repo_root else "."

    # 1. Root override
    if rel in root_config.test_overrides:
        cmd = root_config.test_overrides[rel]
        return cmd if cmd else None  # empty string means skip

    # 2. Repo's own config
    if repo.path != repo.repo_root:
        local_config = load_config(repo.path)
        if local_config.merge.test_command is not None:
            return local_config.merge.test_command

    # 3. Root default
    return root_config.test_command


def _run_test(repo: RepoInfo, test_cmd: str) -> tuple[bool, float]:
    """Run a test command. Returns (passed, duration_seconds)."""
    start = time.monotonic()
    result = subprocess.run(
        test_cmd, shell=True, cwd=str(repo.path),
        capture_output=True, text=True,
    )
    duration = time.monotonic() - start
    return (result.returncode == 0, duration)


# ---------------------------------------------------------------------------
# Structural consistency check
# ---------------------------------------------------------------------------

def _check_structural_consistency(
    repo_root: Path, branch: str, cache: TopologyCache
) -> None:
    """Compare submodule tree structure between HEAD and the target branch.

    Prints warnings if the topology differs. Does not block the merge.
    """
    # Get current and branch head commits
    current = run_git(repo_root, "rev-parse", "--short", "HEAD", check=False)
    branch_result = run_git(
        repo_root, "rev-parse", "--short", branch, check=False
    )
    if current.returncode != 0 or branch_result.returncode != 0:
        return

    current_sha = current.stdout.strip()
    branch_sha = branch_result.stdout.strip()

    td = cache.compare(current_sha, branch_sha)
    if td is None:
        # Fall back to gitmodules diff
        result = run_git(
            repo_root, "diff", f"{branch}..HEAD", "--", ".gitmodules",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(Colors.yellow(
                "  Warning: .gitmodules differs between branches "
                "(topology cache miss — cannot show detailed diff)"
            ))
        return

    if td.is_empty:
        return

    if td.has_structural_changes:
        print(Colors.yellow("  Warning: submodule structure differs between branches:"))
        for e in td.added:
            print(f"    + {Colors.green(e.rel_path)} (added)")
        for e in td.removed:
            print(f"    - {Colors.red(e.rel_path)} (removed)")
        for o, n in td.changed_url:
            print(f"    ~ {o.rel_path}: URL changed ({o.url} → {n.url})")
        for o, n in td.changed_relative_url:
            print(f"    ~ {o.rel_path}: relative URL changed ({o.relative_url} → {n.relative_url})")
        for o, n in td.reparented:
            print(f"    ~ {o.rel_path}: reparented ({o.parent_rel_path} → {n.parent_rel_path})")
        print()


# ---------------------------------------------------------------------------
# Core merge execution for a single repo
# ---------------------------------------------------------------------------

def _execute_merge_for_repo(
    repo: RepoInfo,
    entry: RepoMergeEntry,
    state: MergeState,
    state_path: Path,
    journal_path: Path,
    root_config: MergeConfig,
    merged_child_rel_paths: set[str],
) -> int:
    """Merge a single repo. Returns 0 on success, 1 if paused."""
    branch = state.branch
    no_ff = state.no_ff

    # Record pre-merge head
    entry.pre_merge_head = repo.get_commit_sha(short=False)
    state.save(state_path)

    # Perform the merge
    merge_args = ["merge", branch]
    if no_ff:
        merge_args.append("--no-ff")
    result = repo.git(*merge_args, check=False, capture=True)

    if result.returncode != 0:
        # Try auto-resolving submodule pointer conflicts
        if _auto_resolve_submodule_conflicts(repo, merged_child_rel_paths):
            # All conflicts resolved — commit
            repo.git("commit", "--no-edit", check=False)
            _log(journal_path,
                 f"MERGE {entry.rel_path}: clean merge (submodule pointers auto-resolved)")
        else:
            # Unresolvable conflicts
            conflicting = repo.get_unmerged_files()
            _log(journal_path,
                 f"MERGE {entry.rel_path}: CONFLICT ({', '.join(conflicting)})")
            entry.status = "paused"
            entry.reason = "conflict"
            state.save(state_path)
            _log(journal_path, f"PAUSED: conflict in {entry.rel_path}")
            print(f"  {Colors.red('CONFLICT')} in {entry.rel_path}")
            print(f"    Conflicting files: {', '.join(conflicting)}")
            for line in _get_submodule_conflict_guidance(repo, conflicting):
                print(line)
            print()
            print(f"  Resolve conflicts in: {repo.path}")
            print(f"  Then run: grove worktree merge --continue")
            return 1
    else:
        pre = entry.pre_merge_head[:8] if entry.pre_merge_head else "?"
        post = repo.get_commit_sha(short=True)
        _log(journal_path, f"MERGE {entry.rel_path}: clean merge ({pre} → {post})")
        print(f"  {Colors.green('✓')} {entry.rel_path}: merged")

    # Run tests
    if not state.no_test:
        test_cmd = _get_test_command(root_config, repo)
        if test_cmd:
            print(f"    Running tests: {test_cmd}")
            passed, duration = _run_test(repo, test_cmd)
            if passed:
                _log(journal_path,
                     f"TEST {entry.rel_path}: PASSED ({test_cmd}, {duration:.1f}s)")
            else:
                _log(journal_path,
                     f"TEST {entry.rel_path}: FAILED ({test_cmd}, {duration:.1f}s)")
                entry.status = "paused"
                entry.reason = "test-failed"
                entry.post_merge_head = repo.get_commit_sha(short=False)
                state.save(state_path)
                _log(journal_path, f"PAUSED: test failed in {entry.rel_path}")
                print(f"    {Colors.red('TEST FAILED')} in {entry.rel_path}")
                print(f"    Fix the issue, then run: grove worktree merge --continue")
                return 1

    # Mark as merged
    entry.status = "merged"
    entry.post_merge_head = repo.get_commit_sha(short=False)
    state.save(state_path)
    return 0


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def start_merge(
    branch: str,
    *,
    dry_run: bool = False,
    no_recurse: bool = False,
    no_ff: bool = False,
    no_test: bool = False,
) -> int:
    """Start a new merge of *branch* into the current branch."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    # Phase 0 — Guard
    if state_path.exists():
        print(Colors.red(
            "A merge is already in progress. "
            "Use --continue, --abort, or --status."
        ))
        return 1

    # Phase 1 — Discovery
    print(Colors.blue("Discovering repositories..."))
    config = load_config(repo_root)
    exclude_paths = get_sync_group_exclude_paths(repo_root, config)

    repos = discover_repos_from_gitmodules(repo_root, exclude_paths=exclude_paths or None)

    # Record topology
    cache = TopologyCache.for_repo(repo_root)
    cache.load()
    root_commit_result = run_git(repo_root, "rev-parse", "--short", "HEAD", check=False)
    if root_commit_result.returncode == 0:
        cache.record(root_commit_result.stdout.strip(), repos, repo_root)
        cache.prune()
        cache.save()

    sorted_repos = topological_sort_repos(repos)

    if no_recurse:
        sorted_repos = [r for r in sorted_repos if r.path == repo_root]

    print(f"  Found {len(sorted_repos)} repositories")
    print()

    # Phase 2 — Structural verification
    print(Colors.blue("Checking structural consistency..."))
    _check_structural_consistency(repo_root, branch, cache)
    print()

    # Phase 3 — Pre-flight
    print(Colors.blue("Pre-flight checks..."))
    entries: list[RepoMergeEntry] = []
    has_errors = False
    needs_merge_repos: list[tuple[RepoInfo, RepoMergeEntry]] = []

    for repo in sorted_repos:
        rel = repo.rel_path if repo.path != repo.repo_root else "."

        # Check uncommitted changes
        if repo.has_uncommitted_changes():
            print(f"  {Colors.red('✗')} {rel}: has uncommitted changes")
            has_errors = True
            continue

        # Check detached HEAD
        current_branch = repo.get_branch()
        if not current_branch:
            entry = RepoMergeEntry(rel_path=rel, status="skipped", reason="detached-head")
            entries.append(entry)
            print(f"  {Colors.yellow('·')} {rel}: skipped (detached HEAD)")
            continue

        # Check if branch exists
        if not repo.has_local_branch(branch):
            entry = RepoMergeEntry(rel_path=rel, status="skipped", reason="branch-not-found")
            entries.append(entry)
            print(f"  {Colors.yellow('·')} {rel}: skipped (branch '{branch}' not found)")
            continue

        # Check if already merged
        if repo.is_ancestor(branch):
            entry = RepoMergeEntry(rel_path=rel, status="skipped", reason="already-merged")
            entries.append(entry)
            print(f"  {Colors.yellow('·')} {rel}: skipped (already up-to-date)")
            continue

        entry = RepoMergeEntry(rel_path=rel)
        entries.append(entry)
        needs_merge_repos.append((repo, entry))
        _, behind = repo.count_divergent_commits(branch)
        print(f"  {Colors.green('→')} {rel}: needs merge ({behind} commits from {branch})")

    if has_errors:
        print()
        print(Colors.red("Cannot proceed: fix uncommitted changes first."))
        return 1

    if not needs_merge_repos:
        print()
        print(Colors.green("Nothing to merge — all repositories are up-to-date."))
        return 0

    print()

    # Phase 4 — Conflict prediction
    print(Colors.blue("Predicting conflicts..."))
    predictions: list[tuple[RepoInfo, RepoMergeEntry, bool, list[str]]] = []
    for repo, entry in needs_merge_repos:
        clean, conflicts = _predict_conflicts(repo, branch)
        predictions.append((repo, entry, clean, conflicts))
        if clean:
            print(f"  {Colors.green('✓')} {entry.rel_path}: clean merge expected")
        else:
            print(f"  {Colors.yellow('⚠')} {entry.rel_path}: conflicts expected in {', '.join(conflicts)}")
    print()

    # Phase 4.5 — Sync-group prediction (dry-run only)
    if dry_run and not no_recurse and config.sync_groups:
        from grove.sync import discover_sync_submodules as _discover_subs

        print(Colors.blue("Sync-group predictions..."))
        for gname, grp in config.sync_groups.items():
            subs = _discover_subs(repo_root, grp.url_match)
            if not subs:
                continue
            canon = _find_canonical_sync_instance(
                subs, grp.standalone_repo, branch, repo_root,
            )
            if canon is None:
                print(f"  {Colors.yellow('·')} sync group '{gname}': "
                      f"branch '{branch}' not found, skipping")
                continue
            clean, conflicts = _predict_conflicts(canon, branch)
            if clean:
                print(f"  {Colors.green('✓')} sync group '{gname}' ({canon.rel_path}): "
                      f"clean merge expected")
            else:
                print(f"  {Colors.yellow('⚠')} sync group '{gname}' ({canon.rel_path}): "
                      f"conflicts expected in {', '.join(conflicts)}")
            instance_paths = [str(s.path.relative_to(repo_root)) for s in subs]
            print(f"    Would sync to: {', '.join(instance_paths)}")
        print()

    if dry_run:
        print(Colors.yellow("Dry run complete."))
        return 0

    # Phase 5 — Execute
    state = MergeState(
        branch=branch,
        no_ff=no_ff,
        no_test=no_test,
        started_at=datetime.now(timezone.utc).isoformat(),
        repos=entries,
    )
    state.save(state_path)

    current_branch = run_git(repo_root, "branch", "--show-current", check=False).stdout.strip()
    _log(journal_path, f"MERGE START: {branch} into {current_branch}")
    _log(journal_path,
         f"DISCOVER: {len(sorted_repos)} repos found, {len(needs_merge_repos)} need merging")

    merged_child_rel_paths: set[str] = set()

    # Phase 5a — Sync-group pre-merge
    if not no_recurse and config.sync_groups:
        print(Colors.blue("Merging sync-group submodules..."))
        rc = _merge_and_sync_groups(
            repo_root, config, branch, state, state_path, journal_path,
            merged_child_rel_paths,
        )
        if rc != 0:
            return rc
        print()

    # Phase 5b — Normal merge loop
    print(Colors.blue(f"Merging {len(needs_merge_repos)} repositories..."))
    print()

    for repo, entry in needs_merge_repos:
        rc = _execute_merge_for_repo(
            repo, entry, state, state_path, journal_path,
            config.merge, merged_child_rel_paths,
        )
        if rc != 0:
            return rc
        merged_child_rel_paths.add(entry.rel_path)

    # All done
    MergeState.remove(state_path)
    all_entries = state.repos
    merged_count = sum(1 for e in all_entries if e.status == "merged")
    skipped_count = sum(1 for e in all_entries if e.status == "skipped")
    _log(journal_path, f"MERGE COMPLETE: {merged_count} repos merged, {skipped_count} skipped")

    print()
    print(Colors.green(f"Merge complete: {merged_count} repos merged, {skipped_count} skipped."))
    return 0


def continue_merge() -> int:
    """Resume a paused merge."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No merge in progress."))
        return 1

    state = MergeState.load(state_path)
    config = load_config(repo_root)
    _log(journal_path, "CONTINUE")

    # Find the paused repo
    paused_entry = None
    for entry in state.repos:
        if entry.status == "paused":
            paused_entry = entry
            break

    if paused_entry is None:
        print(Colors.red("No paused repo found. State may be corrupt."))
        return 1

    # Locate the actual repo
    if paused_entry.rel_path == ".":
        repo_path = repo_root
    else:
        repo_path = repo_root / paused_entry.rel_path
    repo = RepoInfo(path=repo_path, repo_root=repo_root)

    if paused_entry.reason == "conflict":
        # Verify conflicts are resolved
        unmerged = repo.get_unmerged_files()
        if unmerged:
            print(Colors.red(f"Unresolved conflicts in {paused_entry.rel_path}:"))
            for f in unmerged:
                print(f"  - {f}")
            for line in _get_submodule_conflict_guidance(repo, unmerged):
                print(line)
            print()
            print("Resolve conflicts, then run: grove worktree merge --continue")
            return 1

        # If merge was in progress, commit it
        if repo.has_merge_head():
            repo.git("commit", "--no-edit", check=False)

        # Run tests
        if not state.no_test:
            test_cmd = _get_test_command(config.merge, repo)
            if test_cmd:
                print(f"  Running tests: {test_cmd}")
                passed, duration = _run_test(repo, test_cmd)
                if not passed:
                    _log(journal_path,
                         f"TEST {paused_entry.rel_path}: FAILED ({test_cmd}, {duration:.1f}s)")
                    paused_entry.reason = "test-failed"
                    state.save(state_path)
                    print(f"  {Colors.red('TEST FAILED')} in {paused_entry.rel_path}")
                    return 1
                _log(journal_path,
                     f"TEST {paused_entry.rel_path}: PASSED ({test_cmd}, {duration:.1f}s)")

    elif paused_entry.reason == "test-failed":
        # Re-run tests
        test_cmd = _get_test_command(config.merge, repo)
        if test_cmd:
            print(f"  Re-running tests: {test_cmd}")
            passed, duration = _run_test(repo, test_cmd)
            if not passed:
                _log(journal_path,
                     f"TEST {paused_entry.rel_path}: FAILED ({test_cmd}, {duration:.1f}s)")
                state.save(state_path)
                print(f"  {Colors.red('TEST STILL FAILING')} in {paused_entry.rel_path}")
                return 1
            _log(journal_path,
                 f"TEST {paused_entry.rel_path}: PASSED ({test_cmd}, {duration:.1f}s)")

    # Mark resolved
    paused_entry.status = "merged"
    paused_entry.post_merge_head = repo.get_commit_sha(short=False)
    state.save(state_path)
    print(f"  {Colors.green('✓')} {paused_entry.rel_path}: merged")

    # If this was a sync-group entry, run sync propagation
    if paused_entry.sync_group:
        merged_sha = repo.get_commit_sha(short=False)
        merged_child_rel_paths: set[str] = set()
        sync_rc = _run_sync_propagation(
            paused_entry.sync_group, repo.path, merged_sha, repo_root,
            config, journal_path, merged_child_rel_paths,
        )
        if sync_rc != 0:
            return 1
        merged_child_rel_paths.add(paused_entry.rel_path)
    else:
        merged_child_rel_paths = set()

    # Collect already-merged child paths (including sync-group instances)
    for entry in state.repos:
        if entry.status == "merged":
            merged_child_rel_paths.add(entry.rel_path)

    # Add sync-group instance paths from already-completed sync groups
    if config.sync_groups:
        from grove.sync import discover_sync_submodules as _disc_subs
        for entry in state.repos:
            if entry.sync_group and entry.status == "merged":
                grp = config.sync_groups.get(entry.sync_group)
                if grp:
                    for sub in _disc_subs(repo_root, grp.url_match):
                        merged_child_rel_paths.add(
                            str(sub.path.relative_to(repo_root))
                        )

    # Continue with remaining pending repos
    # Re-discover repos to get RepoInfo objects
    exclude_paths = get_sync_group_exclude_paths(repo_root, config)
    all_repos = discover_repos_from_gitmodules(repo_root, exclude_paths=exclude_paths or None)
    path_to_repo = {r.path: r for r in all_repos}

    for entry in state.repos:
        if entry.status != "pending":
            continue

        if entry.rel_path == ".":
            rp = repo_root
        else:
            rp = repo_root / entry.rel_path
        r = path_to_repo.get(rp)
        if r is None:
            r = RepoInfo(path=rp, repo_root=repo_root)

        # Sync-group entries need merge + sync propagation
        if entry.sync_group:
            rc = _execute_merge_for_repo(
                r, entry, state, state_path, journal_path,
                config.merge, merged_child_rel_paths,
            )
            if rc != 0:
                return rc
            merged_sha = r.get_commit_sha(short=False)
            sync_rc = _run_sync_propagation(
                entry.sync_group, r.path, merged_sha, repo_root,
                config, journal_path, merged_child_rel_paths,
            )
            if sync_rc != 0:
                return 1
        else:
            rc = _execute_merge_for_repo(
                r, entry, state, state_path, journal_path,
                config.merge, merged_child_rel_paths,
            )
            if rc != 0:
                return rc
        merged_child_rel_paths.add(entry.rel_path)

    # All done
    MergeState.remove(state_path)
    merged_count = sum(1 for e in state.repos if e.status == "merged")
    skipped_count = sum(1 for e in state.repos if e.status == "skipped")
    _log(journal_path, f"MERGE COMPLETE: {merged_count} repos merged, {skipped_count} skipped")

    print()
    print(Colors.green(f"Merge complete: {merged_count} repos merged, {skipped_count} skipped."))
    return 0


def abort_merge() -> int:
    """Abort the in-progress merge and restore all repos."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No merge in progress."))
        return 1

    state = MergeState.load(state_path)

    # Handle paused repo — abort mid-merge if needed
    for entry in state.repos:
        if entry.status == "paused":
            if entry.rel_path == ".":
                rp = repo_root
            else:
                rp = repo_root / entry.rel_path
            repo = RepoInfo(path=rp, repo_root=repo_root)
            if repo.has_merge_head():
                repo.git("merge", "--abort", check=False)
            # Reset to pre-merge state if we have it
            if entry.pre_merge_head:
                repo.git("reset", "--hard", entry.pre_merge_head, check=False)
            break

    # Reverse merged repos (to pre_merge_head)
    merged_entries = [e for e in state.repos if e.status == "merged"]
    for entry in reversed(merged_entries):
        if entry.pre_merge_head:
            if entry.rel_path == ".":
                rp = repo_root
            else:
                rp = repo_root / entry.rel_path
            repo = RepoInfo(path=rp, repo_root=repo_root)
            repo.git("reset", "--hard", entry.pre_merge_head, check=False)
            print(f"  {Colors.yellow('↺')} {entry.rel_path}: restored to {entry.pre_merge_head[:8]}")

    # Reverse sync propagation (to pre_sync_heads)
    # This overrides the above for repos that were both synced and merged,
    # restoring them to the true original state before any sync or merge.
    if state.pre_sync_heads:
        for rel_path, pre_sha in state.pre_sync_heads.items():
            if rel_path == ".":
                rp = repo_root
            else:
                rp = repo_root / rel_path
            if rp.exists():
                repo = RepoInfo(path=rp, repo_root=repo_root)
                repo.git("reset", "--hard", pre_sha, check=False)
                print(f"  {Colors.yellow('↺')} {rel_path}: restored to {pre_sha[:8]} (pre-sync)")

    MergeState.remove(state_path)
    _log(journal_path, "MERGE ABORTED")

    print()
    print(Colors.green("Merge aborted. All repositories restored to pre-merge state."))
    return 0


def status_merge() -> int:
    """Show current merge progress."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)

    if not state_path.exists():
        print("No merge in progress.")
        return 0

    state = MergeState.load(state_path)

    print(f"Merge in progress: {Colors.blue(state.branch)}")
    print(f"  Started: {state.started_at}")
    if state.no_ff:
        print(f"  Options: --no-ff")
    if state.no_test:
        print(f"  Options: --no-test")
    print()

    for entry in state.repos:
        if entry.status == "merged":
            icon = Colors.green("✓")
            detail = "merged"
        elif entry.status == "skipped":
            icon = Colors.yellow("·")
            detail = f"skipped ({entry.reason})"
        elif entry.status == "paused":
            icon = Colors.red("⚠")
            detail = f"PAUSED ({entry.reason})"
        else:
            icon = " "
            detail = "pending"

        label = "(root)" if entry.rel_path == "." else entry.rel_path
        sync_tag = f" [sync:{entry.sync_group}]" if entry.sync_group else ""
        print(f"  {icon} {label}{sync_tag}: {detail}")

    print()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args) -> int:
    """Dispatch to the appropriate merge sub-action."""
    if getattr(args, "continue_merge", False):
        return continue_merge()
    if getattr(args, "abort", False):
        return abort_merge()
    if getattr(args, "status", False):
        return status_merge()

    branch = getattr(args, "branch", None)
    if not branch:
        print(Colors.red("Usage: grove worktree merge <branch>"))
        print("  Or use --continue, --abort, or --status")
        return 2

    return start_merge(
        branch,
        dry_run=getattr(args, "dry_run", False),
        no_recurse=getattr(args, "no_recurse", False),
        no_ff=getattr(args, "no_ff", False),
        no_test=getattr(args, "no_test", False),
    )
