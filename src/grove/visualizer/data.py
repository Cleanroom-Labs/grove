"""
Data serialization for the web-based visualizer.

Converts RepoInfo objects and worktree metadata into JSON-serializable
dictionaries for the frontend API.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo

# Palette of visually distinct colors for sync-group borders
SYNC_GROUP_PALETTE = [
    "#2196F3",  # Blue
    "#9C27B0",  # Purple
    "#009688",  # Teal
    "#E91E63",  # Pink
    "#3F51B5",  # Indigo
    "#00BCD4",  # Cyan
    "#795548",  # Brown
    "#607D8B",  # Blue Gray
]


def repo_to_dict(repo: RepoInfo) -> dict:
    """Convert a RepoInfo to a JSON-serializable dict."""
    return {
        "path": str(repo.path),
        "rel_path": repo.rel_path,
        "name": repo.name,
        "is_root": repo.path == repo.repo_root,
        "branch": repo.branch,
        "commit": repo.get_commit_sha(short=True),
        "ahead": repo.ahead_count or "0",
        "behind": repo.behind_count or "0",
        "status": repo.status.name if repo.status else "OK",
        "is_detached": repo.branch is None,
        "error": repo.error_message,
        "parent_path": str(repo.parent.path) if repo.parent else None,
        "sync_group": repo.sync_group,
        "sync_group_color": repo.sync_group_color,
        "remote_url": repo.get_remote_url(),
        "commit_tag": repo.get_commit_tag(),
        "commit_message": repo.get_commit_message(),
        "changed_files": repo.get_changed_files()
        if repo.status and repo.status.name == "UNCOMMITTED"
        else [],
        "local_branches": repo.get_local_branches(),
        "remote_branches": repo.get_remote_branches(),
    }


def repos_to_json(repos: list[RepoInfo]) -> dict:
    """Convert a full repo list to the JSON payload for /api/repos."""
    return {
        "repos": [repo_to_dict(r) for r in repos],
        "repo_root": str(repos[0].repo_root) if repos else "",
    }


def load_and_validate_repos(repo_path: Path) -> list[RepoInfo]:
    """Load repos from a path, validate them, and populate sync groups."""
    from grove.repo_utils import discover_repos_from_gitmodules

    repos = discover_repos_from_gitmodules(repo_path)
    for repo in repos:
        repo.validate(check_sync=True, allow_detached=True, allow_no_remote=True)

    _populate_sync_groups(repos, repo_path)
    return repos


def _populate_sync_groups(repos: list[RepoInfo], repo_path: Path) -> None:
    """Tag repos with their sync-group name and color."""
    try:
        from grove.config import load_config
        from grove.sync import discover_sync_submodules

        config = load_config(repo_path)
    except (FileNotFoundError, ValueError):
        return

    path_to_group: dict[Path, tuple[str, str]] = {}
    for i, group in enumerate(config.sync_groups.values()):
        color = SYNC_GROUP_PALETTE[i % len(SYNC_GROUP_PALETTE)]
        for sub in discover_sync_submodules(repo_path, group.url_match):
            path_to_group[sub.path] = (group.name, color)

    for repo in repos:
        if repo.path in path_to_group:
            repo.sync_group, repo.sync_group_color = path_to_group[repo.path]


def discover_worktrees(repo_root: Path) -> list[dict]:
    """Discover git worktrees and return metadata for each.

    Returns a list of dicts with keys: path, branch, head, is_bare.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    worktrees: list[dict] = []
    current: dict = {}

    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            # refs/heads/main -> main
            ref = line[7:]
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[11:]
            else:
                current["branch"] = ref
        elif line == "bare":
            current["is_bare"] = True
        elif line == "detached":
            current.setdefault("branch", None)

    if current:
        worktrees.append(current)

    # Fill in defaults
    for wt in worktrees:
        wt.setdefault("branch", None)
        wt.setdefault("head", "")
        wt.setdefault("is_bare", False)

    return worktrees


def worktrees_to_json(repo_root: Path) -> dict:
    """Build the JSON payload for /api/worktrees.

    Includes diff counts showing how many submodules differ from the
    main worktree (first in git worktree list).
    """
    worktrees = discover_worktrees(repo_root)
    if not worktrees:
        return {"worktrees": []}

    # Determine current worktree
    resolved_root = str(repo_root.resolve())
    for wt in worktrees:
        wt["is_current"] = str(Path(wt["path"]).resolve()) == resolved_root

    # Compute diff counts relative to the first (main) worktree
    main_path = Path(worktrees[0]["path"])
    main_commits = _submodule_commits(main_path)

    for wt in worktrees:
        if wt["path"] == str(main_path):
            wt["diff_count"] = 0
        else:
            other_commits = _submodule_commits(Path(wt["path"]))
            wt["diff_count"] = _count_differences(main_commits, other_commits)

    return {"worktrees": worktrees}


def _submodule_commits(repo_path: Path) -> dict[str, str]:
    """Get a mapping of submodule relative path -> current commit SHA."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "submodule", "status", "--recursive"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}

    commits: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        # Format: " <sha> <path> (<describe>)" or "+<sha> <path> (<describe>)"
        line = line.strip()
        if line.startswith("+") or line.startswith("-") or line.startswith("U"):
            line = line[1:]
        parts = line.split(None, 2)
        if len(parts) >= 2:
            commits[parts[1]] = parts[0]

    return commits


def _count_differences(base: dict[str, str], other: dict[str, str]) -> int:
    """Count submodules that differ between two commit maps."""
    all_paths = set(base) | set(other)
    diff = 0
    for path in all_paths:
        if base.get(path) != other.get(path):
            diff += 1
    return diff


def compare_worktrees(base_path: Path, other_path: Path) -> dict:
    """Compare two worktrees and return a diff summary."""
    base_repos = load_and_validate_repos(base_path)
    other_repos = load_and_validate_repos(other_path)

    base_map = {r.rel_path: r for r in base_repos}
    other_map = {r.rel_path: r for r in other_repos}

    all_paths = sorted(set(base_map) | set(other_map))

    same = []
    different = []
    only_base = []
    only_other = []

    for rel_path in all_paths:
        in_base = rel_path in base_map
        in_other = rel_path in other_map

        if in_base and in_other:
            b = base_map[rel_path]
            o = other_map[rel_path]
            b_commit = b.get_commit_sha(short=True)
            o_commit = o.get_commit_sha(short=True)

            if b_commit == o_commit and b.branch == o.branch:
                same.append(
                    {
                        "rel_path": rel_path,
                        "branch": b.branch,
                        "commit": b_commit,
                    }
                )
            else:
                different.append(
                    {
                        "rel_path": rel_path,
                        "base": {
                            "branch": b.branch,
                            "commit": b_commit,
                            "status": b.status.name,
                        },
                        "other": {
                            "branch": o.branch,
                            "commit": o_commit,
                            "status": o.status.name,
                        },
                    }
                )
        elif in_base:
            b = base_map[rel_path]
            only_base.append(
                {
                    "rel_path": rel_path,
                    "branch": b.branch,
                    "commit": b.get_commit_sha(short=True),
                }
            )
        else:
            o = other_map[rel_path]
            only_other.append(
                {
                    "rel_path": rel_path,
                    "branch": o.branch,
                    "commit": o.get_commit_sha(short=True),
                }
            )

    # Get branch names for display
    base_wts = discover_worktrees(base_path)
    other_wts = discover_worktrees(other_path)
    base_branch = base_wts[0]["branch"] if base_wts else None
    other_branch = other_wts[0]["branch"] if other_wts else None

    return {
        "base_branch": base_branch,
        "other_branch": other_branch,
        "same": same,
        "different": different,
        "only_base": only_base,
        "only_other": only_other,
    }
