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


@dataclass
class CascadeState:
    """Persistent cascade state across CLI invocations."""
    submodule_path: str
    started_at: str
    system_mode: str  # "default" | "all" | "none"
    quick: bool
    repos: list[RepoCascadeEntry]

    def save(self, state_path: Path) -> None:
        data = {
            "submodule_path": self.submodule_path,
            "started_at": self.started_at,
            "system_mode": self.system_mode,
            "quick": self.quick,
            "repos": [asdict(r) for r in self.repos],
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
    child_rel_path: str | None,
    config: CascadeConfig,
    state: CascadeState,
    state_path: Path,
    journal_path: Path,
    repo_root: Path,
    dry_run: bool,
) -> int:
    """Process a single repo in the cascade chain.  Returns 0 on success, 1 if paused."""
    print(f"  {Colors.blue(entry.rel_path)} ({entry.role})")

    # Record pre-cascade head
    entry.pre_cascade_head = repo.get_commit_sha(short=False)
    state.save(state_path)

    # Stage child submodule pointer if this is not the leaf
    if child_rel_path is not None:
        if dry_run:
            print(f"    Would stage submodule pointer: {child_rel_path}")
        else:
            repo.git("add", child_rel_path, check=False)

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
            # Test failed — run auto-diagnosis if applicable
            if child_rel_path and tier == "integration-tests":
                _auto_diagnose_integration(
                    entry, child_rel_path, config, repo_root, journal_path,
                )
            elif child_rel_path and tier == "system-tests":
                _auto_diagnose_system(
                    entry, child_rel_path, config, repo_root, journal_path,
                )

            state.save(state_path)
            print()
            print(f"  Paused. Fix the issue, then run: grove cascade --continue")
            return 1

        # Update status after each passing tier
        entry.status = _TIER_STATUS.get(tier, entry.status)
        state.save(state_path)

    # Commit
    if child_rel_path is not None:
        child_sha = run_git(
            repo_root / child_rel_path, "rev-parse", "--short", "HEAD", check=False,
        ).stdout.strip()
        message = f"chore(cascade): update {child_rel_path} submodule to {child_sha}"

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

def run_cascade(
    submodule_path: str,
    dry_run: bool = False,
    system_mode: str = "default",
    quick: bool = False,
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

    # Build cascade chain
    try:
        chain = _discover_cascade_chain(target, repos)
    except ValueError as e:
        print(Colors.red(f"Error: {e}"))
        return 1

    if len(chain) < 2:
        print(Colors.red("Error: Cascade requires at least a leaf and one parent."))
        print("The given path appears to be the root repository itself.")
        return 1

    # Assign roles
    entries: list[RepoCascadeEntry] = []
    for i, repo in enumerate(chain):
        rel = str(repo.path.relative_to(repo_root)) if repo.path != repo_root else "."
        if i == 0:
            role = "leaf"
        elif i == len(chain) - 1:
            role = "root"
        else:
            role = "intermediate"
        entries.append(RepoCascadeEntry(rel_path=rel, role=role))

    # Create state
    state = CascadeState(
        submodule_path=submodule_path,
        started_at=datetime.now(timezone.utc).isoformat(),
        system_mode=system_mode,
        quick=quick,
        repos=entries,
    )

    # Ensure state directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.save(state_path)

    _log(journal_path, f"START cascade from {submodule_path}")

    print(Colors.blue(f"Cascade: {submodule_path} → root"))
    print(f"Chain: {' → '.join(e.rel_path for e in entries)}")
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
    """Execute the cascade chain.  Returns 0 on success, 1 if paused."""
    child_rel_path: str | None = None

    for repo, entry in zip(chain, entries):
        if entry.status == "committed":
            child_rel_path = entry.rel_path
            continue
        if entry.status == "paused":
            # Resuming from paused state — re-run from the failed tier
            pass

        result = _process_repo(
            repo, entry, child_rel_path, config,
            state, state_path, journal_path, repo_root, dry_run,
        )
        if result != 0:
            return result

        child_rel_path = entry.rel_path

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

    # Rebuild the chain
    repos = discover_repos_from_gitmodules(repo_root)
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
    )
