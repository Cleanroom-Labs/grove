"""
grove/cascade.py
Bottom-up cascade integration with tiered testing.

Propagates a change from a leaf submodule upward through the dependency
tree, running tests at each level and committing submodule pointer
updates.  Supports pause/resume on test failures and full rollback.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from grove.config import (
    CASCADE_TIERS,
    CascadeConfig,
    SyncGroup,
    load_config,
)
from grove.filelock import atomic_write_json, locked_open
from grove.repo_utils import (
    Colors,
    RepoInfo,
    discover_repos_from_gitmodules,
    find_repo_root,
    get_git_common_dir,
    get_git_worktree_dir,
    run_git,
)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

@dataclass
class RepoCascadeEntry:
    """Cascade state for a single repository in the chain."""
    rel_path: str
    role: str  # leaf | intermediate | root
    status: str = "pending"
    # pending -> local-passed -> contract-passed -> integration-passed
    #   -> system-passed -> committed
    # or "paused" on failure
    pre_cascade_head: str | None = None
    failed_tier: str | None = None
    diagnosis: list[dict] | None = None  # [{rel_path, tier, passed}]
    child_rel_paths: list[str] | None = None  # paths relative to THIS repo


@dataclass
class CascadeState:
    """Persistent cascade state across CLI invocations."""
    submodule_path: str
    started_at: str
    system_mode: str  # "default" | "all" | "none"
    quick: bool
    repos: list[RepoCascadeEntry]
    sync_group_name: str | None = None
    is_dag: bool = False

    def save(self, state_path: Path) -> None:
        data = {
            "submodule_path": self.submodule_path,
            "started_at": self.started_at,
            "system_mode": self.system_mode,
            "quick": self.quick,
            "repos": [asdict(r) for r in self.repos],
            "sync_group_name": self.sync_group_name,
            "is_dag": self.is_dag,
        }
        atomic_write_json(state_path, json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, state_path: Path) -> CascadeState:
        with locked_open(state_path, "r", shared=True) as f:
            data = json.loads(f.read())
        repos = [RepoCascadeEntry(**r) for r in data["repos"]]
        return cls(
            submodule_path=data["submodule_path"],
            started_at=data["started_at"],
            system_mode=data["system_mode"],
            quick=data["quick"],
            repos=repos,
            sync_group_name=data.get("sync_group_name"),
            is_dag=data.get("is_dag", False),
        )

    @classmethod
    def remove(cls, state_path: Path) -> None:
        state_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_state_path(repo_root: Path) -> Path:
    return get_git_worktree_dir(repo_root) / "grove" / "cascade-state.json"


def _get_journal_path(repo_root: Path) -> Path:
    now = datetime.now(timezone.utc)
    filename = f"cascade-journal-{now.strftime('%Y-%m')}.log"
    return get_git_common_dir(repo_root) / "grove" / filename


def _log(journal_path: Path, message: str) -> None:
    """Append a timestamped entry to the cascade journal."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with locked_open(journal_path, "a") as f:
        f.write(f"[{ts}] {message}\n")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def _run_test(path: Path, test_cmd: str) -> tuple[bool, float]:
    """Run a test command in a directory.  Returns (passed, duration_seconds)."""
    start = time.monotonic()
    result = subprocess.run(
        test_cmd, shell=True, cwd=str(path),
        capture_output=True, text=True,
    )
    duration = time.monotonic() - start
    return (result.returncode == 0, duration)


def _run_tier(
    repo: RepoInfo,
    entry: RepoCascadeEntry,
    tier: str,
    config: CascadeConfig,
    journal_path: Path,
    state: CascadeState,
    state_path: Path,
) -> bool:
    """Run a single test tier for a repo.  Returns True if passed or skipped."""
    cmd = config.get_command(tier, entry.rel_path)
    if cmd is None:
        return True  # tier not configured, skip

    # Empty string means explicitly disabled for this repo
    if cmd == "":
        return True

    print(f"    Running {tier}...")
    passed, duration = _run_test(repo.path, cmd)

    if passed:
        _log(journal_path, f"PASS {entry.rel_path} {tier} ({duration:.1f}s)")
        print(f"    {Colors.green('✓')} {tier} passed ({duration:.1f}s)")
        return True

    _log(journal_path, f"FAIL {entry.rel_path} {tier} ({duration:.1f}s)")
    print(f"    {Colors.red('✗')} {tier} FAILED ({duration:.1f}s)")
    entry.status = "paused"
    entry.failed_tier = tier
    state.save(state_path)
    return False


# ---------------------------------------------------------------------------
# Auto-diagnosis
# ---------------------------------------------------------------------------

def _auto_diagnose_integration(
    entry: RepoCascadeEntry,
    child_rel_path: str,
    config: CascadeConfig,
    repo_root: Path,
    journal_path: Path,
) -> list[dict]:
    """Phase 1 diagnosis: run local-tests of the changed child submodule."""
    results: list[dict] = []
    child_path = repo_root / child_rel_path
    if not child_path.exists():
        return results

    cmd = config.get_command("local-tests", child_rel_path)
    if cmd is None or cmd == "":
        return results

    print()
    print(f"  Auto-diagnosis:")
    print(f"    Running local-tests of {child_rel_path}...")

    passed, duration = _run_test(child_path, cmd)
    result = {"rel_path": child_rel_path, "tier": "local-tests", "passed": passed}
    results.append(result)

    if passed:
        _log(journal_path, f"DIAG {child_rel_path} local-tests PASS ({duration:.1f}s)")
        print(f"    {Colors.green('✓')} {child_rel_path} — local-tests passed "
              f"(problem is likely at the interface)")
    else:
        _log(journal_path, f"DIAG {child_rel_path} local-tests FAIL ({duration:.1f}s)")
        print(f"    {Colors.red('✗')} {child_rel_path} — local-tests FAILED "
              f"(problem may be inside this dependency)")

    entry.diagnosis = results
    return results


def _auto_diagnose_system(
    entry: RepoCascadeEntry,
    child_rel_path: str,
    config: CascadeConfig,
    repo_root: Path,
    journal_path: Path,
) -> list[dict]:
    """Two-phase diagnosis for system-test failures."""
    results: list[dict] = []
    child_path = repo_root / child_rel_path
    if not child_path.exists():
        return results

    # Phase 1: local-tests of changed submodule
    print()
    print(f"  Auto-diagnosis (phase 1 — direct submodules):")

    local_cmd = config.get_command("local-tests", child_rel_path)
    if local_cmd and local_cmd != "":
        print(f"    Running local-tests of {child_rel_path}...")
        passed, duration = _run_test(child_path, local_cmd)
        results.append({"rel_path": child_rel_path, "tier": "local-tests", "passed": passed})

        if passed:
            _log(journal_path, f"DIAG {child_rel_path} local-tests PASS ({duration:.1f}s)")
            print(f"    {Colors.green('✓')} {child_rel_path} — local-tests passed")
        else:
            _log(journal_path, f"DIAG {child_rel_path} local-tests FAIL ({duration:.1f}s)")
            print(f"    {Colors.red('✗')} {child_rel_path} — local-tests FAILED "
                  f"(problem may be inside this dependency)")
            entry.diagnosis = results
            return results  # Found culprit, skip phase 2

    # Phase 2: integration-tests of changed submodule
    integ_cmd = config.get_command("integration-tests", child_rel_path)
    if integ_cmd and integ_cmd != "":
        print()
        print(f"  Auto-diagnosis (phase 2 — deeper):")
        print(f"    Running integration-tests of {child_rel_path}...")
        passed, duration = _run_test(child_path, integ_cmd)
        results.append({"rel_path": child_rel_path, "tier": "integration-tests", "passed": passed})

        if passed:
            _log(journal_path, f"DIAG {child_rel_path} integration-tests PASS ({duration:.1f}s)")
            print(f"    {Colors.green('✓')} {child_rel_path} — integration-tests passed")
        else:
            _log(journal_path, f"DIAG {child_rel_path} integration-tests FAIL ({duration:.1f}s)")
            print(f"    {Colors.red('✗')} {child_rel_path} — integration-tests FAILED")
            print(f"      → Problem may be in a transitive dependency of {child_rel_path}")

    entry.diagnosis = results
    return results


# ---------------------------------------------------------------------------
# Chain discovery
# ---------------------------------------------------------------------------

def _discover_cascade_chain(
    submodule_path: Path,
    repos: list[RepoInfo],
) -> list[RepoInfo]:
    """Build the cascade chain from leaf submodule up to root.

    Returns [leaf, parent1, parent2, ..., root] by following
    ``RepoInfo.parent`` pointers set during discovery.
    """
    path_to_repo = {repo.path: repo for repo in repos}

    resolved = submodule_path.resolve()
    if resolved not in path_to_repo:
        raise ValueError(
            f"Path '{submodule_path}' is not a recognized repository in this grove."
        )

    chain: list[RepoInfo] = []
    current = path_to_repo[resolved]
    while current is not None:
        chain.append(current)
        current = current.parent

    return chain


# ---------------------------------------------------------------------------
# Sync-group awareness
# ---------------------------------------------------------------------------

def _find_sync_group_for_path(
    submodule_path: Path,
    repo_root: Path,
    config,
) -> tuple[str, SyncGroup] | None:
    """Return (group_name, SyncGroup) if *submodule_path* belongs to a sync group.

    Iterates configured sync groups, discovers instances via .gitmodules URL
    matching, and checks whether *submodule_path* matches any instance.
    Returns None if the path is not a sync-group submodule.
    """
    from grove.sync import discover_sync_submodules

    resolved = submodule_path.resolve()

    for name, group in config.sync_groups.items():
        submodules = discover_sync_submodules(repo_root, group.url_match)
        for sub in submodules:
            if sub.path.resolve() == resolved:
                return (name, group)

    return None


def _build_unified_cascade_plan(
    sync_group_name: str,
    url_match: str,
    repo_root: Path,
    all_repos: list[RepoInfo],
) -> tuple[list[RepoInfo], list[RepoCascadeEntry]]:
    """Build a DAG cascade plan from all instances of a sync group.

    Discovers all sync-group instances, builds individual cascade chains,
    merges them into a deduplicated plan sorted by depth (deepest first),
    and computes correct parent-relative child paths for each repo.

    Returns (plan_repos, entries) where plan_repos[i] corresponds to entries[i].
    """
    from grove.sync import discover_sync_submodules

    submodules = discover_sync_submodules(repo_root, url_match)

    # Build chains for each instance and collect all repos by path
    # Key: resolved path → {repo, depth, children (resolved paths)}
    repo_map: dict[Path, dict] = {}

    for sub in submodules:
        try:
            chain = _discover_cascade_chain(sub.path, all_repos)
        except ValueError:
            continue

        for depth, repo in enumerate(chain):
            rp = repo.path.resolve()
            if rp not in repo_map:
                repo_map[rp] = {
                    "repo": repo,
                    "depth": depth,
                    "children": set(),
                }
            else:
                # Keep the maximum depth (deepest-first ordering)
                repo_map[rp]["depth"] = max(repo_map[rp]["depth"], depth)

            # Record child→parent relationship
            if depth > 0:
                parent = chain[depth - 1] if depth < len(chain) else None
                # Actually chain is leaf→root, so chain[depth-1] is one level closer to leaf
                # Wait, chain is [leaf, parent1, parent2, ..., root]
                # chain[0] = leaf (depth 0), chain[1] = parent1 (depth 1), etc.
                # For chain[depth], its child is chain[depth-1]
                child_path = chain[depth - 1].path.resolve()
                repo_map[rp]["children"].add(child_path)

    # Sort by depth ascending (leaves first at depth 0, root last at max depth)
    # This matches cascade execution order: process leaf → intermediate → root
    sorted_items = sorted(repo_map.values(), key=lambda x: x["depth"])

    # Determine which paths are leaves (the sync-group instances)
    leaf_paths = {sub.path.resolve() for sub in submodules}

    plan_repos: list[RepoInfo] = []
    entries: list[RepoCascadeEntry] = []

    for item in sorted_items:
        repo = item["repo"]
        rp = repo.path.resolve()
        rel = str(rp.relative_to(repo_root)) if rp != repo_root else "."

        # Assign roles
        if rp in leaf_paths:
            role = "leaf"
        elif item == sorted_items[-1]:
            role = "root"
        else:
            role = "intermediate"

        # Compute child_rel_paths relative to THIS repo (not root)
        child_rel_paths = None
        if item["children"]:
            child_rel_paths = sorted(
                str(child_path.relative_to(rp))
                for child_path in item["children"]
            )

        entry = RepoCascadeEntry(
            rel_path=rel, role=role, child_rel_paths=child_rel_paths,
        )
        plan_repos.append(repo)
        entries.append(entry)

    return plan_repos, entries


def _check_sync_group_consistency(
    group_name: str,
    repo_root: Path,
    url_match: str,
    force: bool,
) -> bool:
    """Check all instances of a sync group are at the same commit.

    Returns True if the cascade should proceed, False to abort.
    """
    from grove.sync import discover_sync_submodules

    submodules = discover_sync_submodules(repo_root, url_match)
    if len(submodules) < 2:
        return True  # nothing to check with fewer than 2 instances

    commits = {}
    for sub in submodules:
        sha = sub.get_current_commit()
        rel = str(sub.path.relative_to(repo_root))
        commits[rel] = sha

    unique_shas = set(commits.values())
    if len(unique_shas) <= 1:
        return True  # all instances at the same commit

    # Inconsistent
    if force:
        print(Colors.yellow(
            f"Warning: Sync group '{group_name}' has inconsistent instances (--force)."
        ))
        for rel, sha in sorted(commits.items()):
            print(f"  {rel}: {sha[:8] if sha else '(none)'}")
        print()
        return True

    print(Colors.red(
        f"Error: Sync group '{group_name}' instances are not in sync."
    ))
    for rel, sha in sorted(commits.items()):
        print(f"  {rel}: {sha[:8] if sha else '(none)'}")
    print()
    print(f"Run {Colors.blue(f'grove sync {group_name}')} first, "
          f"or use {Colors.blue('--force')} to skip this check.")
    return False


# ---------------------------------------------------------------------------
# Core cascade execution
# ---------------------------------------------------------------------------

def _determine_tiers(
    role: str,
    system_mode: str,
    quick: bool,
) -> list[str]:
    """Determine which test tiers to run based on role and flags."""
    if quick:
        return ["local-tests", "contract-tests"]

    tiers = ["local-tests", "contract-tests"]

    if role in ("intermediate", "root"):
        tiers.append("integration-tests")

    if system_mode == "all":
        tiers.append("system-tests")
    elif system_mode == "default" and role == "root":
        tiers.append("system-tests")
    # system_mode == "none": never add system-tests

    return tiers


# Status names in order, mapping tier completion to status values
_TIER_STATUS = {
    "local-tests": "local-passed",
    "contract-tests": "contract-passed",
    "integration-tests": "integration-passed",
    "system-tests": "system-passed",
}


def _process_repo(
    repo: RepoInfo,
    entry: RepoCascadeEntry,
    child_rel_paths: list[str] | None,
    config: CascadeConfig,
    state: CascadeState,
    state_path: Path,
    journal_path: Path,
    repo_root: Path,
    dry_run: bool,
) -> int:
    """Process a single repo in the cascade chain.  Returns 0 on success, 1 if paused.

    child_rel_paths are paths relative to THIS repo (not root).
    """
    print(f"  {Colors.blue(entry.rel_path)} ({entry.role})")

    # Record pre-cascade head
    entry.pre_cascade_head = repo.get_commit_sha(short=False)
    state.save(state_path)

    # Stage child submodule pointer(s) if this is not the leaf
    if child_rel_paths:
        for crp in child_rel_paths:
            if dry_run:
                print(f"    Would stage submodule pointer: {crp}")
            else:
                repo.git("add", crp, check=False)

    # Determine which tiers to run
    tiers = _determine_tiers(entry.role, state.system_mode, state.quick)

    # Resume support: skip tiers already passed
    start_from = 0
    if entry.status != "pending":
        for i, tier in enumerate(tiers):
            status_name = _TIER_STATUS.get(tier)
            if entry.status == status_name:
                start_from = i + 1
                break

    # Run tiers
    for tier in tiers[start_from:]:
        if dry_run:
            cmd = config.get_command(tier, entry.rel_path)
            if cmd and cmd != "":
                print(f"    Would run {tier}: {cmd}")
            continue

        if not _run_tier(repo, entry, tier, config, journal_path, state, state_path):
            # Test failed — run auto-diagnosis against the first child
            first_child = child_rel_paths[0] if child_rel_paths else None
            if first_child and tier == "integration-tests":
                _auto_diagnose_integration(
                    entry, first_child, config, repo_root, journal_path,
                )
            elif first_child and tier == "system-tests":
                _auto_diagnose_system(
                    entry, first_child, config, repo_root, journal_path,
                )

            state.save(state_path)
            print()
            print(f"  Paused. Fix the issue, then run: grove cascade --continue")
            return 1

        # Update status after each passing tier
        entry.status = _TIER_STATUS.get(tier, entry.status)
        state.save(state_path)

    # Commit
    if child_rel_paths:
        if len(child_rel_paths) == 1:
            crp = child_rel_paths[0]
            child_sha = run_git(
                repo.path / crp, "rev-parse", "--short", "HEAD", check=False,
            ).stdout.strip()
            message = f"chore(cascade): update {crp} submodule to {child_sha}"
        else:
            message = (
                f"chore(cascade): update {len(child_rel_paths)} submodule pointers"
            )

        if dry_run:
            print(f"    Would commit: {message}")
        else:
            # Check if there are staged changes to commit
            diff_result = repo.git("diff", "--cached", "--quiet", check=False)
            if diff_result.returncode != 0:
                repo.git("commit", "-m", message, check=False)
                _log(journal_path, f"COMMIT {entry.rel_path}: {message}")
                print(f"    {Colors.green('✓')} Committed: {message}")
            else:
                _log(journal_path, f"SKIP {entry.rel_path}: no staged changes")
                print(f"    {Colors.yellow('⊘')} No changes to commit")

    entry.status = "committed"
    state.save(state_path)
    print()
    return 0


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _build_linear_entries(
    chain: list[RepoInfo],
    repo_root: Path,
) -> list[RepoCascadeEntry]:
    """Build RepoCascadeEntry list for a linear chain with correct child_rel_paths.

    chain is [leaf, parent1, ..., root].  Each non-leaf entry gets
    child_rel_paths set to the path of its child relative to itself.
    """
    entries: list[RepoCascadeEntry] = []
    for i, repo in enumerate(chain):
        rel = str(repo.path.relative_to(repo_root)) if repo.path != repo_root else "."
        if i == 0:
            role = "leaf"
        elif i == len(chain) - 1:
            role = "root"
        else:
            role = "intermediate"

        # Compute child_rel_path relative to THIS repo
        child_rel_paths = None
        if i > 0:
            child = chain[i - 1]
            child_rel_paths = [str(child.path.relative_to(repo.path))]

        entries.append(RepoCascadeEntry(
            rel_path=rel, role=role, child_rel_paths=child_rel_paths,
        ))
    return entries


def run_cascade(
    submodule_path: str,
    dry_run: bool = False,
    system_mode: str = "default",
    quick: bool = False,
    force: bool = False,
) -> int:
    """Start a new cascade from the given submodule path."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    if state_path.exists():
        print(Colors.red("A cascade is already in progress."))
        print(f"Use {Colors.blue('grove cascade --status')} to see current state.")
        print(f"Use {Colors.blue('grove cascade --abort')} to cancel it.")
        return 1

    config = load_config(repo_root)

    # Warn if no test tiers configured
    cc = config.cascade
    if not any([cc.local_tests, cc.contract_tests, cc.integration_tests, cc.system_tests]):
        print(Colors.yellow(
            "Warning: No cascade test tiers configured. "
            "Cascade will commit without testing."
        ))
        print(f"Configure tests in .grove.toml under [cascade].")
        print()

    # Discover repos
    repos = discover_repos_from_gitmodules(repo_root)

    # Resolve submodule path
    target = (repo_root / submodule_path).resolve()

    # Sync-group consistency check
    sg_match = _find_sync_group_for_path(target, repo_root, config)
    if sg_match is not None:
        sg_name, sg_group = sg_match
        print(Colors.blue(f"Sync-group detected: '{sg_name}'"))
        if not _check_sync_group_consistency(sg_name, repo_root, sg_group.url_match, force):
            return 1
        print()

    # Build cascade plan — DAG for sync-group submodules, linear chain otherwise
    is_dag = False
    sg_name_for_state: str | None = None

    if sg_match is not None:
        sg_name, sg_group = sg_match
        sg_name_for_state = sg_name
        chain, entries = _build_unified_cascade_plan(
            sg_name, sg_group.url_match, repo_root, repos,
        )
        is_dag = True

        if len(chain) < 2:
            print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
            return 1
    else:
        try:
            chain = _discover_cascade_chain(target, repos)
        except ValueError as e:
            print(Colors.red(f"Error: {e}"))
            return 1

        if len(chain) < 2:
            print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
            print("The given path appears to be the root repository itself.")
            return 1

        # Build linear entries with correct parent-relative child_rel_paths
        entries = _build_linear_entries(chain, repo_root)

    # Create state
    state = CascadeState(
        submodule_path=submodule_path,
        started_at=datetime.now(timezone.utc).isoformat(),
        system_mode=system_mode,
        quick=quick,
        repos=entries,
        sync_group_name=sg_name_for_state,
        is_dag=is_dag,
    )

    # Ensure state directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.save(state_path)

    _log(journal_path, f"START cascade from {submodule_path}")

    if is_dag:
        leaf_entries = [e for e in entries if e.role == "leaf"]
        print(Colors.blue(
            f"Cascade (DAG): {len(leaf_entries)} sync-group instances → root"
        ))
    else:
        print(Colors.blue(f"Cascade: {submodule_path} → root"))
    print(f"Plan: {' → '.join(e.rel_path for e in entries)}")
    if dry_run:
        print(Colors.yellow("(dry-run mode — no changes will be made)"))
    print()

    # Execute the cascade
    result = _execute_cascade(chain, entries, state, state_path, journal_path,
                              config.cascade, repo_root, dry_run)

    if result == 0:
        CascadeState.remove(state_path)
        _log(journal_path, "DONE cascade completed successfully")
        print(Colors.green("Cascade complete."))
        print(f"Run {Colors.blue('grove push')} to distribute changes.")

    return result


def _execute_cascade(
    chain: list[RepoInfo],
    entries: list[RepoCascadeEntry],
    state: CascadeState,
    state_path: Path,
    journal_path: Path,
    config: CascadeConfig,
    repo_root: Path,
    dry_run: bool,
) -> int:
    """Execute the cascade chain/DAG.  Returns 0 on success, 1 if paused.

    Each entry carries its own ``child_rel_paths`` (relative to that repo),
    so this function works for both linear chains and DAGs.
    """
    for repo, entry in zip(chain, entries):
        if entry.status == "committed":
            continue
        if entry.status == "paused":
            # Resuming from paused state — re-run from the failed tier
            pass

        result = _process_repo(
            repo, entry, entry.child_rel_paths, config,
            state, state_path, journal_path, repo_root, dry_run,
        )
        if result != 0:
            return result

    return 0


def continue_cascade() -> int:
    """Resume a paused cascade."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No cascade in progress."))
        return 1

    state = CascadeState.load(state_path)
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

    print(Colors.blue(f"Resuming cascade from {paused_entry.rel_path}"))
    print(f"Previously failed tier: {paused_entry.failed_tier}")
    print()

    # Clear pause state so the repo is re-processed
    # Set status to the last passing tier (one before the failed one)
    failed_tier = paused_entry.failed_tier
    if failed_tier:
        tier_list = list(CASCADE_TIERS)
        idx = tier_list.index(failed_tier) if failed_tier in tier_list else 0
        if idx > 0:
            paused_entry.status = _TIER_STATUS[tier_list[idx - 1]]
        else:
            paused_entry.status = "pending"
    paused_entry.failed_tier = None
    paused_entry.diagnosis = None
    state.save(state_path)

    # Rebuild the chain/DAG (repo objects, not entries — entries come from state)
    repos = discover_repos_from_gitmodules(repo_root)

    if state.is_dag and state.sync_group_name:
        grove_config = load_config(repo_root)
        sg = grove_config.sync_groups.get(state.sync_group_name)
        if sg is None:
            print(Colors.red(f"Error: sync group '{state.sync_group_name}' no longer in config."))
            return 1
        chain, _ = _build_unified_cascade_plan(
            state.sync_group_name, sg.url_match, repo_root, repos,
        )
    else:
        target = (repo_root / state.submodule_path).resolve()
        try:
            chain = _discover_cascade_chain(target, repos)
        except ValueError as e:
            print(Colors.red(f"Error rebuilding chain: {e}"))
            return 1

    result = _execute_cascade(
        chain, state.repos, state, state_path, journal_path,
        config.cascade, repo_root, dry_run=False,
    )

    if result == 0:
        CascadeState.remove(state_path)
        _log(journal_path, "DONE cascade completed successfully")
        print(Colors.green("Cascade complete."))
        print(f"Run {Colors.blue('grove push')} to distribute changes.")

    return result


def abort_cascade() -> int:
    """Abort the in-progress cascade and restore all repos."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)
    journal_path = _get_journal_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No cascade in progress."))
        return 1

    state = CascadeState.load(state_path)
    _log(journal_path, "ABORT")

    print(Colors.blue("Aborting cascade..."))

    # Reverse committed repos (skip the leaf — it has no cascade commit)
    for entry in reversed(state.repos):
        if entry.status in ("committed", "paused") and entry.pre_cascade_head:
            if entry.rel_path == ".":
                rp = repo_root
            else:
                rp = repo_root / entry.rel_path
            if rp.exists():
                repo = RepoInfo(path=rp, repo_root=repo_root)
                if entry.role != "leaf":
                    repo.git("reset", "--hard", entry.pre_cascade_head, check=False)
                    print(f"  {Colors.yellow('↺')} {entry.rel_path}: "
                          f"restored to {entry.pre_cascade_head[:8]}")

    CascadeState.remove(state_path)
    _log(journal_path, "DONE cascade aborted")
    print()
    print(Colors.green("Cascade aborted. All pointer commits have been rolled back."))
    return 0


def show_cascade_status() -> int:
    """Display the current cascade status."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)

    if not state_path.exists():
        print("No cascade in progress.")
        return 0

    state = CascadeState.load(state_path)

    print(Colors.blue(f"Cascade: {state.submodule_path}"))
    print(f"Started: {state.started_at}")
    print(f"Mode: system={state.system_mode}, quick={state.quick}")
    print()

    status_icons = {
        "pending": Colors.yellow("○"),
        "local-passed": Colors.green("◔"),
        "contract-passed": Colors.green("◑"),
        "integration-passed": Colors.green("◕"),
        "system-passed": Colors.green("●"),
        "committed": Colors.green("✓"),
        "paused": Colors.red("⏸"),
    }

    for entry in state.repos:
        icon = status_icons.get(entry.status, "?")
        role_tag = f" ({entry.role})" if entry.role else ""
        print(f"  {icon} {entry.rel_path}{role_tag}: {entry.status}")

        if entry.status == "paused" and entry.failed_tier:
            print(f"      Failed at: {entry.failed_tier}")

        if entry.diagnosis:
            for diag in entry.diagnosis:
                diag_icon = Colors.green("✓") if diag["passed"] else Colors.red("✗")
                print(f"      {diag_icon} {diag['rel_path']} {diag['tier']}")

    print()
    if any(e.status == "paused" for e in state.repos):
        print(f"Fix the issue, then run: {Colors.blue('grove cascade --continue')}")
        print(f"Or abort with: {Colors.blue('grove cascade --abort')}")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run(args) -> int:
    """Dispatch to the appropriate cascade action from CLI args."""
    if getattr(args, "continue_cascade", False):
        return continue_cascade()
    if getattr(args, "abort", False):
        return abort_cascade()
    if getattr(args, "status", False):
        return show_cascade_status()

    path = getattr(args, "path", None)
    if not path:
        print(Colors.red("Usage: grove cascade <path>"))
        print("  Or use --continue, --abort, or --status")
        return 2

    # Resolve system mode from mutually exclusive flags
    if getattr(args, "system", False):
        system_mode = "all"
    elif getattr(args, "no_system", False):
        system_mode = "none"
    else:
        system_mode = "default"

    return run_cascade(
        submodule_path=path,
        dry_run=getattr(args, "dry_run", False),
        system_mode=system_mode,
        quick=getattr(args, "quick", False),
        force=getattr(args, "force", False),
    )
