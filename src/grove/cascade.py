"""
grove/cascade.py
Bottom-up cascade integration with tiered testing.

Propagates a change from a leaf submodule upward through the dependency
tree, running tests at each level and committing submodule pointer
updates.  Supports pause/resume on test failures and full rollback.
"""
from __future__ import annotations

import json
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
    RepoStatus,
    discover_repos_from_gitmodules,
    find_repo_root,
    get_git_common_dir,
    get_state_path,
    log_to_journal,
    run_git,
    run_test,
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
    sync_peers: list[str] | None = None  # peer rel_paths to sync after commit
    sync_primary_rel: str | None = None  # if set, this is a sync target


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
    intermediate_sync_groups: list[str] | None = None
    deferred_sync_groups: list[str] | None = None
    merge_conflict_peer: str | None = None
    merge_conflict_primary: str | None = None
    push: bool = False

    def save(self, state_path: Path) -> None:
        data = {
            "submodule_path": self.submodule_path,
            "started_at": self.started_at,
            "system_mode": self.system_mode,
            "quick": self.quick,
            "repos": [asdict(r) for r in self.repos],
            "sync_group_name": self.sync_group_name,
            "is_dag": self.is_dag,
            "intermediate_sync_groups": self.intermediate_sync_groups,
            "deferred_sync_groups": self.deferred_sync_groups,
            "merge_conflict_peer": self.merge_conflict_peer,
            "merge_conflict_primary": self.merge_conflict_primary,
            "push": self.push,
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
            intermediate_sync_groups=data.get("intermediate_sync_groups"),
            deferred_sync_groups=data.get("deferred_sync_groups"),
            merge_conflict_peer=data.get("merge_conflict_peer"),
            merge_conflict_primary=data.get("merge_conflict_primary"),
            push=data.get("push", False),
        )

    @classmethod
    def remove(cls, state_path: Path) -> None:
        state_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_state_path(repo_root: Path) -> Path:
    return get_state_path(repo_root, "cascade-state.json")


def _get_journal_path(repo_root: Path) -> Path:
    now = datetime.now(timezone.utc)
    filename = f"cascade-journal-{now.strftime('%Y-%m')}.log"
    return get_git_common_dir(repo_root) / "grove" / filename


def _log(journal_path: Path, message: str) -> None:
    """Append a timestamped entry to the cascade journal."""
    log_to_journal(journal_path, message)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def _run_test(path: Path, test_cmd: str) -> tuple[bool, float]:
    """Run a test command in a directory.  Returns (passed, duration_seconds)."""
    return run_test(path, test_cmd)


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
    config=None,
) -> tuple[list[RepoInfo], list[RepoCascadeEntry], list[str]]:
    """Build a DAG cascade plan from all instances of a sync group.

    Discovers all sync-group instances, builds individual cascade chains,
    merges them into a deduplicated plan sorted by depth (deepest first),
    and computes correct parent-relative child paths for each repo.

    When *config* is provided, also expands the plan for intermediate
    repos that belong to their own sync groups (fixed-point iteration).

    Returns (plan_repos, entries, intermediate_sync_groups).
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
                child_path = chain[depth - 1].path.resolve()
                repo_map[rp]["children"].add(child_path)

    # Determine which paths are leaves (the sync-group instances)
    leaf_paths = {sub.path.resolve() for sub in submodules}

    # Expand for intermediate sync groups (fixed-point iteration)
    intermediate_sg_names: list[str] = []
    if config is not None:
        intermediate_sg_names = _expand_plan_for_intermediate_sync_groups(
            repo_map, repo_root, config, all_repos, leaf_paths,
        )

    # Sort by depth ascending (leaves first at depth 0, root last at max depth)
    # Within same depth: primaries before sync targets (ensures primary commits first)
    sorted_items = sorted(
        repo_map.values(),
        key=lambda x: (x["depth"], 1 if x.get("is_sync_target") else 0),
    )

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

        # Sync-group fields
        sync_peers = item.get("sync_peers")
        sync_primary_rel = None
        if item.get("is_sync_target"):
            primary_rp = item["sync_primary"]
            sync_primary_rel = (
                str(primary_rp.relative_to(repo_root))
                if primary_rp != repo_root else "."
            )

        entry = RepoCascadeEntry(
            rel_path=rel, role=role, child_rel_paths=child_rel_paths,
            sync_peers=sync_peers, sync_primary_rel=sync_primary_rel,
        )
        plan_repos.append(repo)
        entries.append(entry)

    return plan_repos, entries, intermediate_sg_names


def _expand_plan_for_intermediate_sync_groups(
    repo_map: dict[Path, dict],
    repo_root: Path,
    config,
    all_repos: list[RepoInfo],
    leaf_paths: set[Path],
) -> list[str]:
    """Expand repo_map with peers of intermediate sync-group members.

    Iterates to a fixed point — each expansion may introduce new
    intermediates that themselves belong to sync groups.

    Returns list of sync-group names encountered.
    """
    from grove.sync import discover_sync_submodules

    encountered_groups: list[str] = []
    checked: set[Path] = set()

    while True:
        new_entries_added = False

        for rp, item in list(repo_map.items()):
            # Skip leaves, already-checked entries, and sync targets
            if rp in leaf_paths or rp in checked:
                continue
            if item.get("is_sync_target"):
                continue
            checked.add(rp)

            sg_match = _find_sync_group_for_path(
                item["repo"].path, repo_root, config,
            )
            if sg_match is None:
                continue

            sg_name, sg_group = sg_match
            if sg_name not in encountered_groups:
                encountered_groups.append(sg_name)

            # Discover all instances of this sync group
            submodules = discover_sync_submodules(repo_root, sg_group.url_match)
            primary_rp = rp
            peers: list[str] = []

            for sub in submodules:
                sub_rp = sub.path.resolve()
                if sub_rp == primary_rp:
                    continue  # this is the primary

                sub_rel = (
                    str(sub_rp.relative_to(repo_root))
                    if sub_rp != repo_root else "."
                )
                peers.append(sub_rel)

                if sub_rp not in repo_map:
                    # Add the peer as a sync target
                    repo_map[sub_rp] = {
                        "repo": RepoInfo(path=sub.path, repo_root=repo_root),
                        "depth": item["depth"],
                        "children": set(),
                        "is_sync_target": True,
                        "sync_primary": primary_rp,
                    }
                    new_entries_added = True
                elif not repo_map[sub_rp].get("is_sync_target"):
                    # Already in plan as a normal entry (e.g., from leaf DAG expansion)
                    # Mark it as a sync target of the primary
                    repo_map[sub_rp]["is_sync_target"] = True
                    repo_map[sub_rp]["sync_primary"] = primary_rp

                # Build cascade chain from peer's parent upward
                try:
                    chain = _discover_cascade_chain(sub.path, all_repos)
                except ValueError:
                    continue

                # Add parent chain (skip the peer itself — it's already added)
                for depth, repo in enumerate(chain):
                    crp = repo.path.resolve()
                    if crp == sub_rp:
                        continue
                    if crp not in repo_map:
                        repo_map[crp] = {
                            "repo": repo,
                            "depth": depth,
                            "children": set(),
                        }
                        new_entries_added = True
                    else:
                        repo_map[crp]["depth"] = max(
                            repo_map[crp]["depth"], depth,
                        )

                    # Record child→parent
                    if depth > 0:
                        child_path = chain[depth - 1].path.resolve()
                        repo_map[crp]["children"].add(child_path)

            # Record peers on the primary
            if peers:
                item.setdefault("sync_peers", []).extend(peers)
                # Deduplicate
                item["sync_peers"] = sorted(set(item["sync_peers"]))

        if not new_entries_added:
            break

    return encountered_groups


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
            f"Warning: Sync group '{group_name}' has inconsistent instances (--skip-checks)."
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
          f"or use {Colors.blue('--skip-checks')} to skip this check.")
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


def _expand_linear_for_intermediate_sync_groups(
    chain: list[RepoInfo],
    entries: list[RepoCascadeEntry],
    repo_root: Path,
    config,
    all_repos: list[RepoInfo],
) -> list[str]:
    """Check a linear chain for intermediate sync-group members and expand.

    If any intermediate (non-leaf, non-root) repo belongs to a sync group,
    discovers peer instances and their parent chains, converts the linear
    entries into a DAG plan, and **mutates** *chain* and *entries* in place.

    Returns list of encountered sync-group names (empty if no expansion).
    """
    if len(chain) < 3:
        # Need at least leaf + intermediate + root for an intermediate to exist
        return []

    # Build repo_map from the existing linear chain
    repo_map: dict[Path, dict] = {}
    leaf_path = chain[0].path.resolve()
    leaf_paths = {leaf_path}

    for depth, repo in enumerate(chain):
        rp = repo.path.resolve()
        repo_map[rp] = {
            "repo": repo,
            "depth": depth,
            "children": set(),
        }
        if depth > 0:
            child_path = chain[depth - 1].path.resolve()
            repo_map[rp]["children"].add(child_path)

    # Run the fixed-point expansion
    intermediate_sg_names = _expand_plan_for_intermediate_sync_groups(
        repo_map, repo_root, config, all_repos, leaf_paths,
    )
    if not intermediate_sg_names:
        return []

    # Rebuild chain and entries from the expanded repo_map
    sorted_items = sorted(
        repo_map.values(),
        key=lambda x: (x["depth"], 1 if x.get("is_sync_target") else 0),
    )

    new_chain: list[RepoInfo] = []
    new_entries: list[RepoCascadeEntry] = []

    for item in sorted_items:
        repo = item["repo"]
        rp = repo.path.resolve()
        rel = str(rp.relative_to(repo_root)) if rp != repo_root else "."

        if rp in leaf_paths:
            role = "leaf"
        elif item == sorted_items[-1]:
            role = "root"
        else:
            role = "intermediate"

        child_rel_paths = None
        if item["children"]:
            child_rel_paths = sorted(
                str(child_path.relative_to(rp))
                for child_path in item["children"]
            )

        sync_peers = item.get("sync_peers")
        sync_primary_rel = None
        if item.get("is_sync_target"):
            primary_rp = item["sync_primary"]
            sync_primary_rel = (
                str(primary_rp.relative_to(repo_root))
                if primary_rp != repo_root else "."
            )

        new_chain.append(repo)
        new_entries.append(RepoCascadeEntry(
            rel_path=rel, role=role, child_rel_paths=child_rel_paths,
            sync_peers=sync_peers, sync_primary_rel=sync_primary_rel,
        ))

    # Mutate in place so the caller sees the changes
    chain.clear()
    chain.extend(new_chain)
    entries.clear()
    entries.extend(new_entries)

    return intermediate_sg_names


def run_cascade(
    submodule_path: str | None = None,
    sync_group_name: str | None = None,
    dry_run: bool = False,
    system_mode: str = "default",
    quick: bool = False,
    force: bool = False,
    push: bool = False,
) -> int:
    """Start a new cascade from a submodule path or sync-group name."""
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

    # Build cascade plan — three entry modes:
    # 1. --sync-group NAME: direct sync-group cascade (always DAG)
    # 2. Path to sync-group leaf: auto-detected DAG
    # 3. Path to non-sync-group leaf: linear chain (may promote to DAG)
    is_dag = False
    sg_name_for_state: str | None = None

    if sync_group_name:
        # Mode 1: Direct sync-group cascade by name
        sg = config.sync_groups.get(sync_group_name)
        if sg is None:
            print(Colors.red(f"Error: Unknown sync group '{sync_group_name}'."))
            print(f"Available groups: {', '.join(config.sync_groups)}")
            return 1

        print(Colors.blue(f"Cascading sync group: '{sync_group_name}'"))
        if not _check_sync_group_consistency(sync_group_name, repo_root, sg.url_match, force):
            return 1
        print()

        sg_name_for_state = sync_group_name
        submodule_path = f"(sync-group: {sync_group_name})"
        chain, entries, intermediate_sg_names = _build_unified_cascade_plan(
            sync_group_name, sg.url_match, repo_root, repos, config=config,
        )
        is_dag = True

        if len(chain) < 2:
            print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
            return 1
    else:
        assert submodule_path is not None
        target = (repo_root / submodule_path).resolve()

        # Mode 2/3: Path-based — check if leaf is a sync-group member
        sg_match = _find_sync_group_for_path(target, repo_root, config)
        if sg_match is not None:
            sg_name, sg_group = sg_match
            print(Colors.blue(f"Sync-group detected: '{sg_name}'"))
            if not _check_sync_group_consistency(sg_name, repo_root, sg_group.url_match, force):
                return 1
            print()

        if sg_match is not None:
            # Mode 2: sync-group leaf DAG
            sg_name, sg_group = sg_match
            sg_name_for_state = sg_name
            chain, entries, intermediate_sg_names = _build_unified_cascade_plan(
                sg_name, sg_group.url_match, repo_root, repos, config=config,
            )
            is_dag = True

            if len(chain) < 2:
                print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
                return 1
        else:
            # Mode 3: linear chain from non-sync-group leaf
            try:
                chain = _discover_cascade_chain(target, repos)
            except ValueError as e:
                print(Colors.red(f"Error: {e}"))
                return 1

            if len(chain) < 2:
                print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
                print("The given path appears to be the root repository itself.")
                return 1

            entries = _build_linear_entries(chain, repo_root)

            # Check for intermediate sync groups — may promote linear to DAG
            intermediate_sg_names = _expand_linear_for_intermediate_sync_groups(
                chain, entries, repo_root, config, repos,
            )
            if intermediate_sg_names:
                is_dag = True

    # Pre-cascade divergence resolution for intermediate sync groups (Phase 1)
    deferred_sg_names: list[str] = []
    if intermediate_sg_names and not force:
        from grove.sync import discover_sync_submodules

        for isg_name in intermediate_sg_names:
            isg = config.sync_groups.get(isg_name)
            if isg is None:
                continue
            subs = discover_sync_submodules(repo_root, isg.url_match)
            commits = {
                run_git(sub.path, "rev-parse", "HEAD", check=False).stdout.strip()
                for sub in subs
            }
            if len(commits) <= 1:
                continue  # already consistent

            if dry_run:
                print(Colors.yellow(
                    f"  Intermediate sync group '{isg_name}' has diverged instances"
                ))
                print("  (dry-run) Would attempt merge.")
                continue

            # Attempt auto-merge
            from grove.sync_merge import attempt_divergence_merge

            merge_result = attempt_divergence_merge(
                isg_name, subs, repo_root,
                Path(isg.standalone_repo) if isg.standalone_repo else None,
                dry_run=False, force=False,
            )
            if merge_result is not None:
                merged_sha, _workspace, _desc = merge_result
                for sub in subs:
                    run_git(sub.path, "checkout", merged_sha, check=False)
                    run_git(sub.path, "submodule", "update", "--recursive", check=False)
                print(Colors.green(
                    f"  Auto-resolved divergence in '{isg_name}': {merged_sha[:8]}"
                ))
            else:
                # Merge conflict — abort the partial merge and defer
                run_git(
                    subs[0].path, "merge", "--abort", check=False,
                )
                deferred_sg_names.append(isg_name)
                print(Colors.yellow(
                    f"  Divergence in '{isg_name}' could not be auto-resolved (deferred)"
                ))

    # Create state
    state = CascadeState(
        submodule_path=submodule_path,
        started_at=datetime.now(timezone.utc).isoformat(),
        system_mode=system_mode,
        quick=quick,
        repos=entries,
        sync_group_name=sg_name_for_state,
        is_dag=is_dag,
        intermediate_sync_groups=intermediate_sg_names or None,
        deferred_sync_groups=deferred_sg_names or None,
        push=push,
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
        if push and not dry_run:
            push_result = _push_cascade_repos(chain, journal_path)
            if push_result != 0:
                return push_result
        elif push and dry_run:
            print(Colors.yellow(
                "(--push: would push all cascade repos after completion)"
            ))
        else:
            print(f"Run {Colors.blue('grove push')} to distribute changes.")

    return result


def _push_cascade_repos(chain: list[RepoInfo], journal_path: Path) -> int:
    """Push repos modified by cascade, in chain order (already topological)."""
    print()
    print(Colors.blue("Pushing cascade repos..."))

    repos_to_push = []
    for repo in chain:
        repo.validate(allow_detached=True, allow_no_remote=True)
        if repo.status == RepoStatus.PENDING:
            repos_to_push.append(repo)

    if not repos_to_push:
        print(Colors.green("All cascade repos are up-to-date. Nothing to push."))
        return 0

    _log(journal_path, f"PUSH {len(repos_to_push)} repos")

    push_failed = False
    for repo in repos_to_push:
        if not repo.push(dry_run=False):
            push_failed = True
            print(f"  {Colors.red('✗ Failed to push')} {repo.rel_path}")

    print()
    if push_failed:
        _log(journal_path, "PUSH failed")
        print(Colors.red("Some pushes failed. Run 'grove push' to retry."))
        return 1

    _log(journal_path, f"PUSH complete ({len(repos_to_push)} repos)")
    print(Colors.green(f"Successfully pushed {len(repos_to_push)} repositories."))
    return 0


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

    Sync-target entries are skipped (they are synced after their primary
    commits).  After a primary commits, its peers are synced to the same
    SHA.  If a peer has diverged and the merge fails, cascade pauses.
    """
    entry_map = {e.rel_path: e for e in entries}

    for repo, entry in zip(chain, entries):
        if entry.status in ("committed", "synced"):
            continue

        # Sync targets are handled by the primary's post-commit step
        if entry.sync_primary_rel is not None:
            if dry_run:
                print(f"  {Colors.blue(entry.rel_path)} (sync target of {entry.sync_primary_rel})")
                print(f"    Would sync to primary's commit")
                print()
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

        # After a primary commits, sync all its peers
        if entry.sync_peers and not dry_run:
            sync_result = _sync_peers_after_commit(
                repo, entry, entries, entry_map,
                state, state_path, repo_root,
            )
            if sync_result != 0:
                return sync_result

    return 0


def _sync_peers_after_commit(
    repo: RepoInfo,
    entry: RepoCascadeEntry,
    entries: list[RepoCascadeEntry],
    entry_map: dict[str, RepoCascadeEntry],
    state: CascadeState,
    state_path: Path,
    repo_root: Path,
) -> int:
    """Sync peer instances to the primary's committed SHA.

    Returns 0 on success, 1 if paused due to merge conflict.
    """
    assert entry.sync_peers is not None

    committed_sha = run_git(
        repo.path, "rev-parse", "HEAD", check=False,
    ).stdout.strip()

    for peer_rel in entry.sync_peers:
        peer_entry = entry_map.get(peer_rel)
        if peer_entry is None:
            continue
        peer_path = repo_root / peer_rel

        # Record pre-cascade head for abort support
        peer_head = run_git(
            peer_path, "rev-parse", "HEAD", check=False,
        ).stdout.strip()
        peer_entry.pre_cascade_head = peer_head

        if peer_head == committed_sha:
            # Already at the right commit
            peer_entry.status = "synced"
            state.save(state_path)
            continue

        # Check if peer is an ancestor of the primary (simple fast-forward)
        is_ancestor = run_git(
            peer_path, "merge-base", "--is-ancestor",
            peer_head, committed_sha, check=False,
        )
        if is_ancestor.returncode == 0:
            # Not diverged — simple checkout
            run_git(peer_path, "checkout", committed_sha, check=False)
        else:
            # Diverged — attempt merge of primary's commit into peer
            run_git(peer_path, "fetch", str(repo.path), committed_sha, check=False)
            merge_result = run_git(
                peer_path, "merge", committed_sha,
                "-m", f"grove cascade: merge {committed_sha[:8]} into {peer_rel}",
                check=False,
            )
            if merge_result.returncode != 0:
                # MERGE CONFLICT — pause cascade
                entry.status = "committed"
                peer_entry.status = "merge-conflict"
                state.merge_conflict_peer = peer_rel
                state.merge_conflict_primary = entry.rel_path
                state.save(state_path)
                print(Colors.yellow(f"  Merge conflict syncing {peer_rel}"))
                print(f"  Resolve conflicts in: {Colors.blue(str(peer_path))}")
                print(f"  Then run: {Colors.blue('grove cascade --continue')}")
                return 1
            # Clean merge — update committed_sha to the merge result
            committed_sha = run_git(
                peer_path, "rev-parse", "HEAD", check=False,
            ).stdout.strip()

        run_git(peer_path, "submodule", "update", "--recursive", check=False)
        peer_entry.status = "synced"
        print(f"    {Colors.green('↔')} Synced {peer_rel} to {committed_sha[:8]}")
        state.save(state_path)

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

    # Check if we're resuming from a merge conflict (not a test failure)
    if state.merge_conflict_peer:
        peer_rel = state.merge_conflict_peer
        peer_path = repo_root / peer_rel
        peer_entry = None
        for entry in state.repos:
            if entry.rel_path == peer_rel:
                peer_entry = entry
                break

        if peer_entry is None:
            print(Colors.red(f"Cannot find merge-conflict entry '{peer_rel}'. State may be corrupt."))
            return 1

        # Check if conflict is resolved (no unmerged files)
        unmerged = run_git(peer_path, "diff", "--name-only", "--diff-filter=U", check=False)
        if unmerged.stdout.strip():
            print(Colors.red("There are still unresolved conflicts:"))
            for f in unmerged.stdout.strip().split("\n"):
                print(f"  {f}")
            return 1

        # Commit the merge if needed (user may have resolved and committed)
        status = run_git(peer_path, "status", "--porcelain", check=False)
        if status.stdout.strip():
            run_git(peer_path, "commit", "--no-edit", check=False)

        # Update nested submodules
        run_git(peer_path, "submodule", "update", "--recursive", check=False)
        peer_entry.status = "synced"
        merged_sha = run_git(peer_path, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
        print(Colors.green(f"Merge resolved in {peer_rel} ({merged_sha})"))
        print()

        # Clear merge conflict state and continue
        state.merge_conflict_peer = None
        state.merge_conflict_primary = None
        state.save(state_path)
        # Fall through to rebuild chain and continue execution

    else:
        # Find the paused repo (test failure case)
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
        chain, _, _ = _build_unified_cascade_plan(
            state.sync_group_name, sg.url_match, repo_root, repos,
            config=grove_config,
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
        push = state.push
        CascadeState.remove(state_path)
        _log(journal_path, "DONE cascade completed successfully")
        print(Colors.green("Cascade complete."))
        if push:
            push_result = _push_cascade_repos(chain, journal_path)
            if push_result != 0:
                return push_result
        else:
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

    # Reverse committed/synced/conflicted repos (skip the leaf — it has no cascade commit)
    for entry in reversed(state.repos):
        if entry.status in ("committed", "synced", "merge-conflict", "paused") and entry.pre_cascade_head:
            if entry.rel_path == ".":
                rp = repo_root
            else:
                rp = repo_root / entry.rel_path
            if rp.exists():
                repo = RepoInfo(path=rp, repo_root=repo_root)
                if entry.status == "merge-conflict" or entry.sync_primary_rel is not None:
                    # Sync target or merge conflict: abort in-progress merge, restore
                    run_git(rp, "merge", "--abort", check=False)
                    run_git(rp, "checkout", entry.pre_cascade_head, check=False)
                    print(f"  {Colors.yellow('↺')} {entry.rel_path}: "
                          f"restored to {entry.pre_cascade_head[:8]}")
                elif entry.role != "leaf":
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
        "synced": Colors.green("↔"),
        "merge-conflict": Colors.red("⚡"),
        "paused": Colors.red("⏸"),
    }

    for entry in state.repos:
        icon = status_icons.get(entry.status, "?")
        role_tag = f" ({entry.role})" if entry.role else ""
        sync_tag = " (sync)" if entry.sync_primary_rel else ""
        print(f"  {icon} {entry.rel_path}{role_tag}{sync_tag}: {entry.status}")

        if entry.status == "paused" and entry.failed_tier:
            print(f"      Failed at: {entry.failed_tier}")

        if entry.status == "merge-conflict":
            print(f"      Resolve conflicts in: {repo_root / entry.rel_path}")

        if entry.diagnosis:
            for diag in entry.diagnosis:
                diag_icon = Colors.green("✓") if diag["passed"] else Colors.red("✗")
                print(f"      {diag_icon} {diag['rel_path']} {diag['tier']}")

    print()
    if any(e.status == "paused" for e in state.repos):
        print(f"Fix the issue, then run: {Colors.blue('grove cascade --continue')}")
        print(f"Or abort with: {Colors.blue('grove cascade --abort')}")
    elif any(e.status == "merge-conflict" for e in state.repos):
        print(f"Resolve the merge conflict, then run: {Colors.blue('grove cascade --continue')}")
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
    sync_group = getattr(args, "sync_group", None)

    if path and sync_group:
        print(Colors.red("Error: Specify either a path or --sync-group, not both."))
        return 2
    if not path and not sync_group:
        print(Colors.red("Usage: grove cascade <path>  or  grove cascade --sync-group <name>"))
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
        sync_group_name=sync_group,
        dry_run=getattr(args, "dry_run", False),
        system_mode=system_mode,
        quick=getattr(args, "quick", False),
        force=getattr(args, "skip_checks", False),
        push=getattr(args, "push", False),
    )
