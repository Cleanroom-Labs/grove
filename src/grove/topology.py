"""
grove/topology.py
Topology caching for submodule tree structure.

Captures the full submodule tree — parent-child nesting, commit hashes,
and both absolute and relative URLs — indexed by root commit SHA.
The cache is stored at .git/grove-topology.json and incrementally
built by discover_repos_from_gitmodules() when a TopologyCache is provided.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from grove.filelock import atomic_write_json, locked_open


DEFAULT_MAX_SNAPSHOTS = 500


@dataclass
class SubmoduleEntry:
    """A single submodule in a topology snapshot."""

    rel_path: str
    parent_rel_path: str
    url: str
    relative_url: str | None
    commit: str

    def structure_key(self) -> tuple[str, str, str]:
        """Key used for topology hashing (excludes commit)."""
        return (self.rel_path, self.parent_rel_path, self.url)


@dataclass
class TopologySnapshot:
    """A point-in-time snapshot of the submodule tree structure."""

    root_commit: str
    timestamp: str
    topology_hash: str
    entries: list[SubmoduleEntry]


@dataclass
class TopologyDiff:
    """Differences between two topology snapshots."""

    added: list[SubmoduleEntry] = field(default_factory=list)
    removed: list[SubmoduleEntry] = field(default_factory=list)
    changed_url: list[tuple[SubmoduleEntry, SubmoduleEntry]] = field(
        default_factory=list
    )
    changed_relative_url: list[tuple[SubmoduleEntry, SubmoduleEntry]] = field(
        default_factory=list
    )
    changed_commit: list[tuple[SubmoduleEntry, SubmoduleEntry]] = field(
        default_factory=list
    )
    reparented: list[tuple[SubmoduleEntry, SubmoduleEntry]] = field(
        default_factory=list
    )

    @property
    def has_structural_changes(self) -> bool:
        """True if there are changes beyond just commit hash differences."""
        return bool(
            self.added
            or self.removed
            or self.changed_url
            or self.changed_relative_url
            or self.reparented
        )

    @property
    def is_empty(self) -> bool:
        return not (
            self.added
            or self.removed
            or self.changed_url
            or self.changed_relative_url
            or self.changed_commit
            or self.reparented
        )


def compute_topology_hash(entries: list[SubmoduleEntry]) -> str:
    """Compute SHA-256 of sorted (rel_path, parent_rel_path, url) tuples.

    Commit hashes are intentionally excluded — they change on every commit
    and don't reflect structural changes.
    """
    keys = sorted(e.structure_key() for e in entries)
    raw = json.dumps(keys, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def diff_snapshots(old: TopologySnapshot, new: TopologySnapshot) -> TopologyDiff:
    """Compare two topology snapshots and return a diff."""
    old_by_path = {e.rel_path: e for e in old.entries}
    new_by_path = {e.rel_path: e for e in new.entries}

    old_paths = set(old_by_path)
    new_paths = set(new_by_path)

    result = TopologyDiff()

    # Added / removed
    for p in sorted(new_paths - old_paths):
        result.added.append(new_by_path[p])
    for p in sorted(old_paths - new_paths):
        result.removed.append(old_by_path[p])

    # Changed (present in both)
    for p in sorted(old_paths & new_paths):
        o = old_by_path[p]
        n = new_by_path[p]
        if o.url != n.url:
            result.changed_url.append((o, n))
        if o.relative_url != n.relative_url:
            result.changed_relative_url.append((o, n))
        if o.parent_rel_path != n.parent_rel_path:
            result.reparented.append((o, n))
        if o.commit != n.commit:
            result.changed_commit.append((o, n))

    return result


def _is_relative_url(url: str) -> bool:
    """Check whether a .gitmodules URL is relative (starts with ./ or ../)."""
    return url.startswith("./") or url.startswith("../")


def build_entries(repos, repo_root: Path) -> list[SubmoduleEntry]:
    """Build SubmoduleEntry list from discovered RepoInfo objects.

    For each non-root repo, uses the ``RepoInfo.parent`` pointer to
    determine the parent, then parses the parent's .gitmodules for URL info.

    Args:
        repos: List of RepoInfo objects (from discover_repos_from_gitmodules).
        repo_root: Root repository path.
    """
    from grove.repo_utils import parse_gitmodules, run_git

    entries = []

    for repo in repos:
        if repo.path == repo_root:
            continue

        # Use parent pointer set during discovery
        if repo.parent is None:
            continue
        parent_path = repo.parent.path

        parent_rel = (
            "." if parent_path == repo_root else str(parent_path.relative_to(repo_root))
        )

        # Parse parent's .gitmodules for this submodule's URL
        gitmodules_path = parent_path / ".gitmodules"
        sub_entries = parse_gitmodules(gitmodules_path)

        url = ""
        relative_url: str | None = None
        submodule_rel = str(repo.path.relative_to(parent_path))

        for _name, sm_path, sm_url in sub_entries:
            if sm_path == submodule_rel:
                if _is_relative_url(sm_url):
                    relative_url = sm_url
                    # Resolve to absolute by getting the remote URL of the parent
                    result = run_git(
                        parent_path, "remote", "get-url", "origin", check=False
                    )
                    if result.returncode == 0:
                        parent_url = result.stdout.strip()
                        # Resolve relative URL against parent's remote
                        url = _resolve_relative_url(parent_url, sm_url)
                    else:
                        url = sm_url
                else:
                    url = sm_url
                    relative_url = None
                break

        # Get current commit hash
        result = run_git(repo.path, "rev-parse", "--short", "HEAD", check=False)
        commit = result.stdout.strip() if result.returncode == 0 else "unknown"

        rel_path = str(repo.path.relative_to(repo_root))

        entries.append(
            SubmoduleEntry(
                rel_path=rel_path,
                parent_rel_path=parent_rel,
                url=url,
                relative_url=relative_url,
                commit=commit,
            )
        )

    return entries


def _resolve_relative_url(parent_url: str, relative: str) -> str:
    """Resolve a relative submodule URL against a parent remote URL.

    For example:
        parent_url = "git@github.com:Org/parent.git"
        relative   = "../child.git"
        result     = "git@github.com:Org/child.git"
    """
    # Strip trailing .git for resolution
    base = parent_url
    if base.endswith(".git"):
        base = base[:-4]

    # Handle SSH URLs (git@host:path/to/repo)
    if ":" in base and not base.startswith(("http://", "https://", "/")):
        # SSH-style URL: git@github.com:Org/repo
        host_part, path_part = base.split(":", 1)
        parts = path_part.rstrip("/").split("/")
        for segment in relative.split("/"):
            if segment == "..":
                if parts:
                    parts.pop()
            elif segment and segment != ".":
                parts.append(segment)
        resolved_path = "/".join(parts)
        # Ensure .git suffix if original relative had it
        return f"{host_part}:{resolved_path}"

    # Handle HTTP(S) URLs
    if base.startswith(("http://", "https://")):
        from urllib.parse import urljoin

        # urljoin needs a trailing slash on the "directory"
        if not base.endswith("/"):
            base = base.rsplit("/", 1)[0] + "/"
        return urljoin(base, relative)

    # Local path fallback
    resolved = Path(base).parent
    for segment in relative.split("/"):
        if segment == "..":
            resolved = resolved.parent
        elif segment and segment != ".":
            resolved = resolved / segment
    return str(resolved)


class TopologyCache:
    """Manages the .git/grove-topology.json cache."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.snapshots: list[TopologySnapshot] = []

    @classmethod
    def for_repo(cls, repo_root: Path) -> TopologyCache:
        """Create a TopologyCache for a repository, resolving the git dir.

        Uses ``--git-common-dir`` so the cache is shared across worktrees.
        """
        from grove.repo_utils import get_git_common_dir

        return cls(get_git_common_dir(repo_root) / "grove" / "topology.json")

    def load(self) -> None:
        """Load snapshots from disk."""
        if not self.cache_path.exists():
            self.snapshots = []
            return

        with locked_open(self.cache_path, "r", shared=True) as f:
            data = json.loads(f.read())
        self.snapshots = []
        for snap_data in data.get("snapshots", []):
            entries = [SubmoduleEntry(**e) for e in snap_data.get("entries", [])]
            self.snapshots.append(
                TopologySnapshot(
                    root_commit=snap_data["root_commit"],
                    timestamp=snap_data["timestamp"],
                    topology_hash=snap_data["topology_hash"],
                    entries=entries,
                )
            )

    def save(self) -> None:
        """Persist snapshots to disk."""
        data = {
            "snapshots": [
                {
                    "root_commit": s.root_commit,
                    "timestamp": s.timestamp,
                    "topology_hash": s.topology_hash,
                    "entries": [asdict(e) for e in s.entries],
                }
                for s in self.snapshots
            ]
        }
        atomic_write_json(self.cache_path, json.dumps(data, indent=2) + "\n")

    def record(self, root_commit: str, repos, repo_root: Path) -> None:
        """Record a topology snapshot from discovered repos.

        Skips recording if the root commit is already cached.
        """
        if self.get(root_commit) is not None:
            return

        entries = build_entries(repos, repo_root)
        topology_hash = compute_topology_hash(entries)
        timestamp = datetime.now(timezone.utc).isoformat()

        self.snapshots.append(
            TopologySnapshot(
                root_commit=root_commit,
                timestamp=timestamp,
                topology_hash=topology_hash,
                entries=entries,
            )
        )

    def get(self, commit: str) -> TopologySnapshot | None:
        """Look up a snapshot by root commit SHA."""
        for snap in self.snapshots:
            if snap.root_commit == commit:
                return snap
        return None

    def compare(self, sha1: str, sha2: str) -> TopologyDiff | None:
        """Compare two snapshots by root commit SHA.

        Returns None if either commit is not cached.
        """
        snap1 = self.get(sha1)
        snap2 = self.get(sha2)
        if snap1 is None or snap2 is None:
            return None
        return diff_snapshots(snap1, snap2)

    def prune(self, max_entries: int = DEFAULT_MAX_SNAPSHOTS) -> None:
        """Remove oldest snapshots beyond the cap."""
        if len(self.snapshots) > max_entries:
            self.snapshots = self.snapshots[-max_entries:]
