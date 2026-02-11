"""
grove/sync_merge.py
Merge diverged sync-group submodule instances.

When ``resolve_local_tip()`` detects diverged instances (no linear
ordering), this module attempts to merge the diverged commits in a
workspace (the standalone repo or first instance).

Supports pause/resume on merge conflicts, following the same state
machine pattern as ``worktree_merge.py`` and ``cascade.py``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from grove.filelock import atomic_write_json, locked_open
from grove.repo_utils import (
    Colors,
    find_repo_root,
    get_git_worktree_dir,
    run_git,
)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

@dataclass
class SyncMergeState:
    """Persistent state for sync-group divergence merge."""
    group_name: str
    started_at: str
    workspace_path: str  # where the merge happens
    base_commit: str  # merge-base of all diverged commits
    diverged_commits: list[dict] = field(default_factory=list)
    # [{sha, source_path, status}]
    merged_sha: str | None = None
    conflict_sha: str | None = None

    def save(self, state_path: Path) -> None:
        data = asdict(self)
        atomic_write_json(state_path, json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, state_path: Path) -> SyncMergeState:
        with locked_open(state_path, "r", shared=True) as f:
            data = json.loads(f.read())
        return cls(**data)

    @classmethod
    def remove(cls, state_path: Path) -> None:
        state_path.unlink(missing_ok=True)


def _get_state_path(repo_root: Path) -> Path:
    return get_git_worktree_dir(repo_root) / "grove" / "sync-merge-state.json"


# ---------------------------------------------------------------------------
# Core merge logic
# ---------------------------------------------------------------------------

def attempt_divergence_merge(
    group_name: str,
    submodules: list,
    repo_root: Path,
    standalone_repo: Path | None,
    dry_run: bool,
    force: bool,
) -> tuple[str, Path, str] | None:
    """Attempt to merge diverged sync-group instances.

    Returns ``(merged_sha, workspace_path, description)`` on success,
    or ``None`` if the merge is paused (conflict) or failed.
    """
    state_path = _get_state_path(repo_root)
    if state_path.exists():
        print(Colors.red("A sync merge is already in progress."))
        print(f"Use {Colors.blue('grove sync --status')} to see current state.")
        print(f"Use {Colors.blue('grove sync --abort')} to cancel it.")
        return None

    # Collect unique diverged commits
    commits: dict[str, Path] = {}
    for sub in submodules:
        sha = sub.get_current_commit()
        if sha and sha not in commits:
            commits[sha] = sub.path

    if len(commits) < 2:
        print(Colors.red("Error: Expected diverged commits but found fewer than 2."))
        return None

    # Select workspace: standalone repo (preferred) or first instance
    if standalone_repo and standalone_repo.exists():
        workspace = standalone_repo
        workspace_desc = f"standalone repo ({standalone_repo})"
    else:
        workspace = next(iter(commits.values()))
        workspace_desc = f"instance ({workspace})"

    commit_list = list(commits.items())
    shas = [sha for sha, _ in commit_list]

    print(Colors.blue(f"Attempting to merge {len(shas)} diverged commits..."))
    print(f"Workspace: {workspace_desc}")
    for sha, path in commit_list:
        rel = str(path.relative_to(repo_root)) if path != repo_root else "."
        print(f"  {sha[:8]} from {rel}")
    print()

    if dry_run:
        print(Colors.yellow("(dry-run) Would attempt merge in workspace."))
        # Return a placeholder — the caller will skip update/commit phases
        return (shas[0], workspace, f"dry-run merge of {len(shas)} diverged commits")

    # Fetch all diverged commits into workspace
    for sha, source in commit_list:
        if source.resolve() != workspace.resolve():
            result = run_git(workspace, "fetch", str(source), sha, check=False)
            if result.returncode != 0:
                print(Colors.yellow(
                    f"  Warning: Could not fetch {sha[:8]} from {source}"
                ))

    # Find merge-base of the first two commits
    mb_result = run_git(workspace, "merge-base", shas[0], shas[1], check=False)
    if mb_result.returncode != 0:
        print(Colors.red("Error: Could not find merge-base for diverged commits."))
        return None
    base_commit = mb_result.stdout.strip()

    # Perform the merge
    # Checkout the first diverged commit
    run_git(workspace, "checkout", shas[0], check=False)

    remaining = shas[1:]
    if len(remaining) == 1:
        # Two-way merge
        merge_result = run_git(
            workspace, "merge", remaining[0],
            "-m", f"grove sync: merge diverged instances of '{group_name}'",
            check=False,
        )
    else:
        # Octopus merge
        merge_result = run_git(
            workspace, "merge", *remaining,
            "-m", f"grove sync: merge {len(shas)} diverged instances of '{group_name}'",
            check=False,
        )

    if merge_result.returncode == 0:
        # Clean merge!
        merged_sha_result = run_git(workspace, "rev-parse", "HEAD", check=False)
        merged_sha = merged_sha_result.stdout.strip()
        print(Colors.green(f"Merge successful: {merged_sha[:8]}"))
        return (merged_sha, workspace, f"merged {len(shas)} diverged instances")

    # Merge conflict — save state for resume
    print(Colors.yellow("Merge conflict detected."))
    print()

    diverged_entries = [
        {"sha": sha, "source_path": str(path), "status": "diverged"}
        for sha, path in commit_list
    ]

    state = SyncMergeState(
        group_name=group_name,
        started_at=datetime.now(timezone.utc).isoformat(),
        workspace_path=str(workspace),
        base_commit=base_commit,
        diverged_commits=diverged_entries,
        conflict_sha=remaining[0] if len(remaining) == 1 else None,
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.save(state_path)

    print(f"Resolve conflicts in: {Colors.blue(str(workspace))}")
    print()
    print(f"Then run: {Colors.blue('grove sync --continue')}")
    print(f"Or abort: {Colors.blue('grove sync --abort')}")
    return None


# ---------------------------------------------------------------------------
# Continue / Abort / Status
# ---------------------------------------------------------------------------

def continue_sync_merge() -> int:
    """Resume after resolving merge conflicts."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No sync merge in progress."))
        return 1

    state = SyncMergeState.load(state_path)
    workspace = Path(state.workspace_path)

    # Check for unmerged files
    unmerged = run_git(workspace, "diff", "--name-only", "--diff-filter=U", check=False)
    if unmerged.stdout.strip():
        print(Colors.red("There are still unresolved conflicts:"))
        for f in unmerged.stdout.strip().split("\n"):
            print(f"  {f}")
        print()
        print(f"Resolve them in: {Colors.blue(str(workspace))}")
        print(f"Then run: {Colors.blue('grove sync --continue')}")
        return 1

    # Check if merge was committed
    merge_status = run_git(workspace, "status", "--porcelain", check=False)
    if merge_status.stdout.strip():
        # There are uncommitted changes — commit the merge
        run_git(
            workspace, "commit", "--no-edit", check=False,
        )

    # Get the merged SHA
    merged_sha_result = run_git(workspace, "rev-parse", "HEAD", check=False)
    merged_sha = merged_sha_result.stdout.strip()

    state.merged_sha = merged_sha
    SyncMergeState.remove(state_path)

    print(Colors.green(f"Merge resolved: {merged_sha[:8]}"))
    print()
    print(f"Run {Colors.blue(f'grove sync {state.group_name} {merged_sha}')} "
          f"to sync all instances to the merged commit.")
    return 0


def abort_sync_merge() -> int:
    """Abort the in-progress sync merge."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)

    if not state_path.exists():
        print(Colors.red("No sync merge in progress."))
        return 1

    state = SyncMergeState.load(state_path)
    workspace = Path(state.workspace_path)

    # Abort the merge
    run_git(workspace, "merge", "--abort", check=False)
    # Restore to the first diverged commit (or base)
    if state.diverged_commits:
        first_sha = state.diverged_commits[0]["sha"]
        run_git(workspace, "checkout", first_sha, check=False)

    SyncMergeState.remove(state_path)

    print(Colors.green("Sync merge aborted."))
    return 0


def show_sync_merge_status() -> int:
    """Display current sync merge state."""
    repo_root = find_repo_root()
    state_path = _get_state_path(repo_root)

    if not state_path.exists():
        print("No sync merge in progress.")
        return 0

    state = SyncMergeState.load(state_path)

    print(Colors.blue(f"Sync merge: {state.group_name}"))
    print(f"Started: {state.started_at}")
    print(f"Workspace: {state.workspace_path}")
    print(f"Base commit: {state.base_commit[:8]}")
    print()
    print("Diverged commits:")
    for entry in state.diverged_commits:
        print(f"  {entry['sha'][:8]} from {entry['source_path']}")
    print()

    if state.merged_sha:
        print(f"Merged to: {Colors.green(state.merged_sha[:8])}")
    else:
        print(f"Status: {Colors.yellow('merge in progress (conflicts)')}")
        print()
        print(f"Resolve conflicts in: {Colors.blue(state.workspace_path)}")
        print(f"Then run: {Colors.blue('grove sync --continue')}")
        print(f"Or abort: {Colors.blue('grove sync --abort')}")

    return 0
