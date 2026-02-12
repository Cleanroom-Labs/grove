"""Tests for grove.repo_utils."""

import sys
from pathlib import Path

from grove.repo_utils import (
    Colors,
    RepoInfo,
    RepoStatus,
    discover_repos_from_gitmodules,
    find_repo_root,
    get_git_common_dir,
    get_git_worktree_dir,
    topological_sort_repos,
)


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

class TestColors:
    def setup_method(self):
        """Ensure colors are enabled for testing (pytest stdout is not a TTY)."""
        Colors._enabled = True

    def teardown_method(self):
        """Re-detect TTY state after each test."""
        Colors._enabled = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

    def test_red_formatting(self):
        result = Colors.red("hello")
        assert result == "\033[0;31mhello\033[0m"

    def test_green_formatting(self):
        result = Colors.green("world")
        assert result == "\033[0;32mworld\033[0m"

    def test_yellow_formatting(self):
        result = Colors.yellow("warn")
        assert result == "\033[1;33mwarn\033[0m"

    def test_blue_formatting(self):
        result = Colors.blue("info")
        assert result == "\033[0;34minfo\033[0m"

    def test_empty_string(self):
        result = Colors.red("")
        assert result == "\033[0;31m\033[0m"

    def test_disable(self):
        Colors.disable()
        assert Colors.red("hello") == "hello"
        assert Colors.green("hello") == "hello"
        assert Colors.yellow("hello") == "hello"
        assert Colors.blue("hello") == "hello"


# ---------------------------------------------------------------------------
# RepoStatus
# ---------------------------------------------------------------------------

class TestRepoStatus:
    def test_expected_members(self):
        expected = {
            "OK",
            "PENDING",
            "UP_TO_DATE",
            "UNCOMMITTED",
            "DETACHED",
            "NO_REMOTE",
            "BEHIND",
            "DIVERGED",
        }
        actual = {member.name for member in RepoStatus}
        assert actual == expected

    def test_values(self):
        assert RepoStatus.OK.value == "ok"
        assert RepoStatus.PENDING.value == "pending"
        assert RepoStatus.UP_TO_DATE.value == "up-to-date"
        assert RepoStatus.UNCOMMITTED.value == "uncommitted"
        assert RepoStatus.DETACHED.value == "detached"
        assert RepoStatus.NO_REMOTE.value == "no-remote"
        assert RepoStatus.BEHIND.value == "behind"
        assert RepoStatus.DIVERGED.value == "diverged"


# ---------------------------------------------------------------------------
# RepoInfo.rel_path
# ---------------------------------------------------------------------------

class TestRepoInfoRelPath:
    def test_non_root_repo(self, tmp_git_repo: Path):
        """A submodule path should be expressed relative to repo_root."""
        repo_root = tmp_git_repo.parent
        info = RepoInfo(path=tmp_git_repo, repo_root=repo_root)
        assert info.rel_path == tmp_git_repo.name

    def test_root_repo(self, tmp_git_repo: Path):
        """When path == repo_root the friendly name should be returned."""
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.rel_path == "(root)"

    def test_nested_path(self, tmp_submodule_tree: Path):
        """A deeply nested submodule should show the full relative path."""
        nested = tmp_submodule_tree / "technical-docs" / "common"
        info = RepoInfo(path=nested, repo_root=tmp_submodule_tree)
        assert info.rel_path == "technical-docs/common"


class TestDiscoverReposFromGitmodules:
    def test_finds_all_repos(self, tmp_submodule_tree: Path):
        """Should discover root, child, and grandchild."""
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        paths = {r.path for r in repos}

        assert tmp_submodule_tree in paths
        assert tmp_submodule_tree / "technical-docs" in paths
        assert tmp_submodule_tree / "technical-docs" / "common" in paths

    def test_parent_pointers_set(self, tmp_submodule_tree: Path):
        """Every non-root repo should have parent set."""
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        path_to_repo = {r.path: r for r in repos}

        root = path_to_repo[tmp_submodule_tree]
        child = path_to_repo[tmp_submodule_tree / "technical-docs"]
        grandchild = path_to_repo[tmp_submodule_tree / "technical-docs" / "common"]

        assert root.parent is None
        assert child.parent is root
        assert grandchild.parent is child

    def test_exclude_paths(self, tmp_submodule_tree: Path):
        """Excluded paths should be skipped."""
        common_path = tmp_submodule_tree / "technical-docs" / "common"
        repos = discover_repos_from_gitmodules(
            tmp_submodule_tree, exclude_paths={common_path},
        )
        paths = {r.path for r in repos}

        assert common_path not in paths
        assert tmp_submodule_tree in paths
        assert tmp_submodule_tree / "technical-docs" in paths

    def test_root_always_included(self, tmp_submodule_tree: Path):
        """Root repo should always be in the result."""
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        root_repos = [r for r in repos if r.path == tmp_submodule_tree]
        assert len(root_repos) == 1

    def test_no_gitmodules(self, tmp_git_repo: Path):
        """Repo with no .gitmodules should return only the root."""
        repos = discover_repos_from_gitmodules(tmp_git_repo)
        assert len(repos) == 1
        assert repos[0].path == tmp_git_repo


# ---------------------------------------------------------------------------
# topological_sort_repos
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    def test_children_before_parents(self, tmp_submodule_tree: Path):
        """After topological sort, children must appear before their parents."""
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        sorted_repos = topological_sort_repos(repos)

        # Build an index mapping path -> position
        index = {r.path: i for i, r in enumerate(sorted_repos)}

        # technical-docs should come before parent
        td_path = tmp_submodule_tree / "technical-docs"
        if td_path in index and tmp_submodule_tree in index:
            assert index[td_path] < index[tmp_submodule_tree]

        # common should come before technical-docs
        common_path = tmp_submodule_tree / "technical-docs" / "common"
        if common_path in index and td_path in index:
            assert index[common_path] < index[td_path]


# ---------------------------------------------------------------------------
# RepoInfo.validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_clean_repo(self, tmp_git_repo: Path):
        """A freshly initialised repo with no remote should validate when
        allow_no_remote is True."""
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = info.validate(allow_no_remote=True)
        assert result is True

    def test_uncommitted_changes_detected(self, tmp_git_repo: Path):
        """Modifying a tracked file should cause validation to fail with
        UNCOMMITTED status."""
        # Modify a tracked file (git diff --quiet only sees changes to tracked files).
        readme = tmp_git_repo / "README.md"
        readme.write_text("modified content\n")

        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = info.validate()
        assert result is False
        assert info.status == RepoStatus.UNCOMMITTED

    def test_no_remote_detected(self, tmp_git_repo: Path):
        """A repo without an origin remote should fail validation when
        allow_no_remote is False (default)."""
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = info.validate(allow_no_remote=False)
        assert result is False
        assert info.status == RepoStatus.NO_REMOTE


# ---------------------------------------------------------------------------
# RepoInfo helper methods
# ---------------------------------------------------------------------------

class TestRepoInfoHelpers:
    def test_get_branch(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        branch = info.get_branch()
        # Default branch is usually "main" or "master".
        assert branch is not None
        assert len(branch) > 0

    def test_has_uncommitted_changes_clean(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.has_uncommitted_changes() is False

    def test_has_uncommitted_changes_dirty(self, tmp_git_repo: Path):
        (tmp_git_repo / "README.md").write_text("modified\n")
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.has_uncommitted_changes() is True

    def test_has_uncommitted_changes_untracked(self, tmp_git_repo: Path):
        (tmp_git_repo / "untracked.txt").write_text("new\n")
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.has_uncommitted_changes() is True

    def test_get_commit_sha(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        sha = info.get_commit_sha(short=True)
        assert sha != "unknown"
        assert len(sha) >= 7

    def test_get_commit_sha_full(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        sha = info.get_commit_sha(short=False)
        assert len(sha) == 40

    def test_name_property(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.name == tmp_git_repo.name

    def test_depth_property(self, tmp_submodule_tree: Path):
        root = RepoInfo(path=tmp_submodule_tree, repo_root=tmp_submodule_tree)
        child = RepoInfo(
            path=tmp_submodule_tree / "technical-docs",
            repo_root=tmp_submodule_tree,
        )
        assert child.depth > root.depth

    def test_has_remote_false(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.has_remote() is False

    def test_get_commit_message(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        msg = info.get_commit_message()
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_get_changed_files_clean(self, tmp_git_repo: Path):
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert info.get_changed_files() == []

    def test_get_changed_files_modified(self, tmp_git_repo: Path):
        (tmp_git_repo / "README.md").write_text("modified\n")
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        files = info.get_changed_files()
        assert len(files) >= 1
        assert any("README.md" in f for f in files)

    def test_get_changed_files_untracked(self, tmp_git_repo: Path):
        (tmp_git_repo / "newfile.txt").write_text("new\n")
        info = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        files = info.get_changed_files()
        assert any("newfile.txt" in f for f in files)

    def test_get_changed_files_excludes_submodules(self, tmp_submodule_tree: Path):
        """Submodule paths should be excluded by default."""
        info = RepoInfo(path=tmp_submodule_tree, repo_root=tmp_submodule_tree)
        files = info.get_changed_files()
        # None of the returned files should be a submodule path
        for f in files:
            assert "technical-docs" not in f


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------

class TestFindRepoRoot:
    def test_finds_root_from_subdirectory(self, tmp_submodule_tree: Path):
        """Should find the repo root from a plain subdirectory."""
        subdir = tmp_submodule_tree / "some-subdir"
        subdir.mkdir()
        result = find_repo_root(start=subdir)
        assert result == tmp_submodule_tree

    def test_finds_root_from_root(self, tmp_submodule_tree: Path):
        """Should find the repo root when starting from root itself."""
        result = find_repo_root(start=tmp_submodule_tree)
        assert result == tmp_submodule_tree

    def test_raises_when_not_found(self, tmp_path: Path):
        """Should raise FileNotFoundError when no git repo exists."""
        import pytest
        with pytest.raises(FileNotFoundError, match="Could not find"):
            find_repo_root(start=tmp_path)

    def test_finds_root_without_config(self, tmp_git_repo: Path):
        """Should find the repo root even without .grove.toml."""
        result = find_repo_root(start=tmp_git_repo)
        assert result == tmp_git_repo


# ---------------------------------------------------------------------------
# Git dir resolution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# RepoInfo merge-related methods (Phase 3)
# ---------------------------------------------------------------------------

class TestHasLocalBranch:
    def test_existing_branch(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        assert repo.has_local_branch("my-feature") is True

    def test_nonexistent_branch(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert repo.has_local_branch("nonexistent") is False


class TestIsAncestor:
    def test_head_is_ancestor_of_itself(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert repo.is_ancestor("HEAD") is True

    def test_branch_not_ancestor_when_diverged(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        # my-feature has a commit that main doesn't, so it's not an ancestor of HEAD (main)
        assert repo.is_ancestor("my-feature") is False


class TestCountDivergentCommits:
    def test_diverged_branches(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        _, behind = repo.count_divergent_commits("my-feature")
        # main is 0 ahead of feature; feature has 1 commit beyond the merge-base
        assert behind >= 1

    def test_same_branch(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        ahead, behind = repo.count_divergent_commits("HEAD")
        assert ahead == 0
        assert behind == 0


class TestGetUnmergedFiles:
    def test_no_conflicts(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert repo.get_unmerged_files() == []


class TestHasMergeHead:
    def test_no_merge_in_progress(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert repo.has_merge_head() is False


# ---------------------------------------------------------------------------
# Git dir resolution
# ---------------------------------------------------------------------------

class TestGetGitCommonDir:
    def test_returns_git_dir(self, tmp_git_repo: Path):
        result = get_git_common_dir(tmp_git_repo)
        assert result.name == ".git"
        assert result.is_dir()

    def test_returns_absolute_path(self, tmp_git_repo: Path):
        result = get_git_common_dir(tmp_git_repo)
        assert result.is_absolute()


class TestGetGitWorktreeDir:
    def test_returns_git_dir(self, tmp_git_repo: Path):
        result = get_git_worktree_dir(tmp_git_repo)
        assert result.name == ".git"
        assert result.is_dir()

    def test_returns_absolute_path(self, tmp_git_repo: Path):
        result = get_git_worktree_dir(tmp_git_repo)
        assert result.is_absolute()
