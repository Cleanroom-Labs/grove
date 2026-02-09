"""
grove/repo_utils.py
Shared utilities for repository operations.

Provides:

- Colors: ANSI color helpers for terminal output
- RepoStatus: Enum for repository validation states
- RepoInfo: Dataclass representing a git repository with validation/push methods
- parse_gitmodules(): Parse .gitmodules files with optional URL filtering
- discover_repos(): Find all git repos in a submodule tree
- topological_sort_repos(): Sort repos for bottom-up operations
- print_status_table(): Formatted status output
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from graphlib import TopologicalSorter
from pathlib import Path



class Colors:
    """ANSI color codes for terminal output."""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color
    _enabled: bool = True

    @classmethod
    def disable(cls):
        """Disable colored output."""
        cls._enabled = False

    @classmethod
    def red(cls, text: str) -> str:
        return f"{cls.RED}{text}{cls.NC}" if cls._enabled else text

    @classmethod
    def green(cls, text: str) -> str:
        return f"{cls.GREEN}{text}{cls.NC}" if cls._enabled else text

    @classmethod
    def yellow(cls, text: str) -> str:
        return f"{cls.YELLOW}{text}{cls.NC}" if cls._enabled else text

    @classmethod
    def blue(cls, text: str) -> str:
        return f"{cls.BLUE}{text}{cls.NC}" if cls._enabled else text


# Auto-detect TTY for color output
if not (hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()):
    Colors.disable()


class RepoStatus(Enum):
    """Validation status for a repository."""
    OK = "ok"
    PENDING = "pending"
    UP_TO_DATE = "up-to-date"
    UNCOMMITTED = "uncommitted"
    DETACHED = "detached"
    NO_REMOTE = "no-remote"
    BEHIND = "behind"
    DIVERGED = "diverged"


def run_git(path: Path, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    cmd = ["git", "-C", str(path)] + list(args)
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def parse_gitmodules(
    gitmodules_path: Path,
    url_match: str | None = None,
) -> list[tuple[str, str, str]]:
    """Parse a .gitmodules file and return submodule entries.

    Returns a list of ``(name, path, url)`` tuples.  When *url_match* is
    provided, only entries whose URL contains that string are included.

    Returns an empty list when the file is missing or empty.
    """
    if not gitmodules_path.exists():
        return []

    content = gitmodules_path.read_text()
    results: list[tuple[str, str, str]] = []

    current_name: str | None = None
    current_path: str | None = None
    current_url: str | None = None

    for line in content.split("\n"):
        line = line.strip()

        if line.startswith("[submodule"):
            # Save previous section
            if current_name and current_path and current_url is not None:
                if url_match is None or url_match in current_url:
                    results.append((current_name, current_path, current_url))
            current_name = None
            current_path = None
            current_url = None
            m = re.search(r'"(.+)"', line)
            if m:
                current_name = m.group(1)
        elif line.startswith("path = "):
            current_path = line[7:].strip()
        elif line.startswith("url = "):
            current_url = line[6:].strip()

    # Don't forget the last section
    if current_name and current_path and current_url is not None:
        if url_match is None or url_match in current_url:
            results.append((current_name, current_path, current_url))

    return results


@dataclass
class RepoInfo:
    """Information about a git repository."""
    path: Path
    repo_root: Path

    # Populated during validation
    branch: str | None = None
    ahead_count: str | None = None
    behind_count: str | None = None
    status: RepoStatus = RepoStatus.OK
    error_message: str | None = None
    parent: RepoInfo | None = field(default=None, repr=False)

    # Populated by visualizer for sync-group display
    sync_group: str | None = None
    sync_group_color: str | None = None

    @cached_property
    def rel_path(self) -> str:
        """Get path relative to repo root, or friendly name for root."""
        if self.path == self.repo_root:
            return "(root)"
        return str(self.path.relative_to(self.repo_root))

    @property
    def depth(self) -> int:
        """Get directory depth for sorting."""
        return len(self.path.parts)

    def git(self, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in this repository."""
        return run_git(self.path, *args, check=check, capture=capture)

    def has_uncommitted_changes(self) -> bool:
        """Check if repo has uncommitted changes."""
        diff_result = self.git("diff", "--quiet", check=False)
        cached_result = self.git("diff", "--cached", "--quiet", check=False)
        return diff_result.returncode != 0 or cached_result.returncode != 0

    def get_branch(self) -> str | None:
        """Get current branch name, or None if detached HEAD."""
        result = self.git("branch", "--show-current", check=False)
        branch = result.stdout.strip()
        return branch if branch else None

    def has_remote(self) -> bool:
        """Check if origin remote exists."""
        result = self.git("remote", "get-url", "origin", check=False)
        return result.returncode == 0

    def get_ahead_behind_count(self, branch: str) -> tuple[str, str]:
        """
        Get count of commits ahead/behind remote.
        Returns tuple of (ahead, behind) as strings.
        Returns ('new-branch', '0') if remote branch doesn't exist.
        """
        # Check if upstream is configured
        result = self.git("rev-parse", "--abbrev-ref", "@{upstream}", check=False)
        if result.returncode == 0:
            count_result = self.git("rev-list", "--count", "--left-right", "@{upstream}...HEAD", check=False)
            if count_result.returncode == 0:
                parts = count_result.stdout.strip().split()
                if len(parts) == 2:
                    return (parts[1], parts[0])  # ahead, behind
            return ("0", "0")

        # No upstream - check if remote branch exists
        ls_result = self.git("ls-remote", "--heads", "origin", branch, check=False)
        if f"refs/heads/{branch}" in ls_result.stdout:
            count_result = self.git("rev-list", "--count", "--left-right", f"origin/{branch}...HEAD", check=False)
            if count_result.returncode == 0:
                parts = count_result.stdout.strip().split()
                if len(parts) == 2:
                    return (parts[1], parts[0])  # ahead, behind
            return ("0", "0")

        return ("new-branch", "0")

    def get_ahead_count(self, branch: str) -> str:
        """Get count of commits ahead of remote. Returns 'new-branch' if remote branch doesn't exist."""
        ahead, _ = self.get_ahead_behind_count(branch)
        return ahead

    def validate(
        self,
        check_sync: bool = False,
        allow_detached: bool = False,
        allow_no_remote: bool = False,
    ) -> bool:
        """
        Validate repository state. Returns True if valid for pushing.

        Args:
            check_sync: If True, also check that repo is in sync with remote (not behind)
            allow_detached: If True, treat detached HEAD repos as non-fatal (useful for submodules)
            allow_no_remote: If True, treat missing origin remote as non-fatal
        """
        # Check for uncommitted changes
        if self.has_uncommitted_changes():
            self.status = RepoStatus.UNCOMMITTED
            self.error_message = f"Has uncommitted changes. Run: cd {self.rel_path} && git status"
            return False

        # Check for detached HEAD
        self.branch = self.get_branch()
        if not self.branch:
            self.status = RepoStatus.DETACHED
            self.error_message = f"Detached HEAD state. Run: cd {self.rel_path} && git checkout <branch>"
            # Detached HEAD is normal for submodules pinned to a commit. It's only
            # fatal if the caller intends to push from this repo.
            return allow_detached

        # Check for remote
        if not self.has_remote():
            self.status = RepoStatus.NO_REMOTE
            self.error_message = "No remote 'origin' configured"
            # Some submodules may not have a remote configured.
            return allow_no_remote

        # Check commits ahead/behind
        self.ahead_count, self.behind_count = self.get_ahead_behind_count(self.branch)

        # Check if behind remote (if requested)
        if check_sync and self.behind_count != "0":
            if self.ahead_count and self.ahead_count not in ("0", "new-branch"):
                self.status = RepoStatus.DIVERGED
                self.error_message = (
                    f"Diverged from remote ({self.ahead_count} ahead, {self.behind_count} behind). "
                    f"Run: cd {self.rel_path} && git pull --rebase"
                )
            else:
                self.status = RepoStatus.BEHIND
                self.error_message = (
                    f"Behind remote by {self.behind_count} commits. "
                    f"Run: cd {self.rel_path} && git pull"
                )
            return False

        if self.ahead_count == "0":
            self.status = RepoStatus.UP_TO_DATE
        else:
            self.status = RepoStatus.PENDING

        return True

    def push(self, dry_run: bool = False) -> bool:
        """Push repository to remote. Returns True on success."""
        if self.branch is None:
            raise RuntimeError("Cannot push without a branch (call validate() first)")

        if self.ahead_count == "new-branch":
            print(f"  {Colors.blue('Pushing')} {self.rel_path} {Colors.yellow(f'(new branch: {self.branch})')}")
        else:
            print(f"  {Colors.blue('Pushing')} {self.rel_path} {Colors.green(f'({self.ahead_count} commits on {self.branch})')}")

        if dry_run:
            return True

        # Try regular push first, then with -u if needed
        result = self.git("push", check=False, capture=False)
        if result.returncode != 0:
            result = self.git("push", "-u", "origin", self.branch, check=False, capture=False)

        return result.returncode == 0

    def fetch(self, all_remotes: bool = True) -> bool:
        """
        Fetch from remote(s). Returns True on success.

        Args:
            all_remotes: If True, fetch from all remotes (--all flag)
        """
        args = ["fetch"]
        if all_remotes:
            args.append("--all")
        result = self.git(*args, check=False, capture=True)
        return result.returncode == 0

    def checkout(self, branch: str) -> tuple[bool, str]:
        """
        Checkout a branch. Returns (success, error_message).

        Args:
            branch: Branch name to checkout
        """
        result = self.git("checkout", branch, check=False, capture=True)
        if result.returncode == 0:
            self.branch = branch
            return (True, "")
        return (False, result.stderr.strip())

    def get_local_branches(self) -> list[str]:
        """Get list of local branch names."""
        result = self.git("branch", "--format=%(refname:short)", check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

    def get_remote_branches(self) -> list[str]:
        """Get list of remote tracking branch names (without 'origin/' prefix)."""
        result = self.git("branch", "-r", "--format=%(refname:short)", check=False)
        if result.returncode != 0:
            return []
        branches = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line and not line.endswith('/HEAD'):
                # Remove 'origin/' prefix
                if line.startswith('origin/'):
                    branches.append(line[7:])
                else:
                    branches.append(line)
        return branches

    def get_commit_sha(self, short: bool = True) -> str:
        """
        Get current commit SHA.

        Args:
            short: If True, return short SHA (7 chars), else full SHA
        """
        args = ["rev-parse"]
        if short:
            args.append("--short")
        args.append("HEAD")
        result = self.git(*args, check=False)
        if result.returncode != 0:
            return "unknown"
        return result.stdout.strip()

    def get_remote_commit_sha(self, branch: str, short: bool = True) -> str | None:
        """
        Get the commit SHA of the remote branch.

        Args:
            branch: Branch name (without 'origin/' prefix)
            short: If True, return short SHA (7 chars), else full SHA

        Returns:
            Commit SHA or None if remote branch doesn't exist
        """
        args = ["rev-parse"]
        if short:
            args.append("--short")
        args.append(f"origin/{branch}")
        result = self.git(*args, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    @property
    def name(self) -> str:
        """Get the repository directory name."""
        return self.path.name


def discover_repos(
    repo_root: Path,
    exclude_paths: set[Path] | None = None,
) -> list[RepoInfo]:
    """
    Discover all git repositories (main repo + submodules).

    Args:
        repo_root: Root directory of the main repository
        exclude_paths: Absolute paths to skip (e.g. sync-group submodules)
    """
    repos = [RepoInfo(path=repo_root, repo_root=repo_root)]

    # Find all submodule .git files
    for git_file in repo_root.rglob(".git"):
        # Skip node_modules
        if "node_modules" in git_file.parts:
            continue

        # Submodules have .git as a file, not directory
        if git_file.is_file():
            submodule_path = git_file.parent

            if exclude_paths and submodule_path in exclude_paths:
                continue

            repos.append(RepoInfo(path=submodule_path, repo_root=repo_root))

    return repos


def build_dependency_graph(repos: list[RepoInfo]) -> dict[Path, set[Path]]:
    """
    Build a dependency graph where children must be pushed before parents.
    Returns dict mapping repo path -> set of repo paths that must be pushed first.
    """
    paths = {repo.path for repo in repos}

    # Build graph: for each repo, find its parent (if any)
    graph: dict[Path, set[Path]] = {repo.path: set() for repo in repos}

    for repo in repos:
        # Walk up the directory tree to find parent repo
        for parent_path in repo.path.parents:
            if parent_path in paths:
                # Parent depends on child being pushed first
                graph[parent_path].add(repo.path)
                break

    return graph


def set_parent_relationships(repos: list[RepoInfo]) -> None:
    """
    Set the parent attribute on each repo based on directory hierarchy.
    Modifies repos in place.
    """
    path_to_repo = {repo.path: repo for repo in repos}

    for repo in repos:
        # Walk up the directory tree to find parent repo
        for parent_path in repo.path.parents:
            if parent_path in path_to_repo:
                repo.parent = path_to_repo[parent_path]
                break


def topological_sort_repos(repos: list[RepoInfo]) -> list[RepoInfo]:
    """Sort repositories so children come before parents (bottom-up)."""
    graph = build_dependency_graph(repos)

    # Create path -> repo lookup
    path_to_repo = {repo.path: repo for repo in repos}

    # TopologicalSorter gives us nodes in dependency order
    # We need children first, so the graph edges point parent -> child
    sorter = TopologicalSorter(graph)
    sorted_paths = list(sorter.static_order())

    return [path_to_repo[path] for path in sorted_paths]


def print_status_table(repos: list[RepoInfo], show_behind: bool = False) -> None:
    """Print a formatted status table."""
    print(f"\n{Colors.blue('Repository Status:')}")
    print("  " + "─" * 70)

    if show_behind:
        print(f"  {'Repository':<40} {'Branch':<10} {'Ahead':<8} {'Behind':<8} {'Status':<12}")
    else:
        print(f"  {'Repository':<45} {'Branch':<12} {'Ahead':<10} {'Status':<12}")

    print("  " + "─" * 70)

    for repo in repos:
        status_str = repo.status.value
        branch_str = repo.branch or "?"
        ahead_str = repo.ahead_count or "-"
        behind_str = repo.behind_count or "-"

        if repo.status == RepoStatus.PENDING:
            status_colored = Colors.yellow(status_str)
        elif repo.status == RepoStatus.UP_TO_DATE:
            status_colored = Colors.green(status_str)
        else:
            status_colored = Colors.red(status_str)

        if show_behind:
            print(f"  {repo.rel_path:<40} {branch_str:<10} {ahead_str:<8} {behind_str:<8} {status_colored}")
        else:
            print(f"  {repo.rel_path:<45} {branch_str:<12} {ahead_str:<10} {status_colored}")

    print("  " + "─" * 70)
    print()


def find_repo_root(start: Path | None = None) -> Path:
    """Find the git repository root using ``git rev-parse --show-toplevel``.

    Args:
        start: Directory to resolve from (default: cwd).

    Raises:
        FileNotFoundError: If not inside a git repository.
    """
    import subprocess

    cwd = str((start or Path.cwd()).resolve())
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        raise FileNotFoundError(
            f"Could not find git repository root.\n"
            f"Searched from: {cwd}"
        )
    return Path(result.stdout.strip())
