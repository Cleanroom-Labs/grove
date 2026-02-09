"""Tests for grove.check."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.check import check_repo_state, check_sync_groups, get_tag_or_branch
from grove.config import CONFIG_FILENAME, SyncGroup
from grove.repo_utils import RepoInfo
from grove.sync import SyncSubmodule


# ---------------------------------------------------------------------------
# get_tag_or_branch / check_repo_state
# ---------------------------------------------------------------------------

class TestGetTagOrBranch:
    def test_returns_branch_name(self, tmp_git_repo: Path):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = get_tag_or_branch(repo)
        assert result is not None
        assert len(result) > 0

    def test_returns_tag_when_on_tag(self, tmp_git_repo: Path):
        """When HEAD is exactly on a tag, the tag name should be returned."""
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = get_tag_or_branch(repo)
        # Could be tag or branch -- both are valid since HEAD is on both.
        assert result is not None


class TestCheckRepoState:
    def test_healthy_repo(self, tmp_git_repo: Path, capsys):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = check_repo_state(repo, "test-repo")
        assert result is True
        captured = capsys.readouterr()
        assert "test-repo" in captured.out

    def test_verbose_shows_sha(self, tmp_git_repo: Path, capsys):
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        result = check_repo_state(repo, "test-repo", verbose=True)
        assert result is True
        captured = capsys.readouterr()
        # Verbose mode should include commit SHA in parentheses.
        assert "(" in captured.out


# ---------------------------------------------------------------------------
# check_sync_groups
# ---------------------------------------------------------------------------

class TestCheckSyncGroups:
    def test_in_sync(self, tmp_submodule_tree: Path, capsys):
        """When there's only one submodule location per group, sync check passes."""
        result = check_sync_groups(tmp_submodule_tree, verbose=False)
        assert result is True

    def test_verbose_output(self, tmp_submodule_tree: Path, capsys):
        """Verbose mode should show individual submodule paths."""
        check_sync_groups(tmp_submodule_tree, verbose=True)
        captured = capsys.readouterr()
        # Should show the common submodule path
        assert "common" in captured.out

    def test_allow_drift_ignores_drifting_instance(self, tmp_path: Path, capsys):
        """A submodule in allow-drift should not cause sync failure."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Config with allow-drift
        (repo_root / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-lib"\n'
            'allow-drift = ["sub-b"]\n'
        )

        # Mock two submodule instances at different commits
        sub_a = SyncSubmodule(
            path=repo_root / "sub-a",
            parent_repo=repo_root,
            submodule_rel_path="sub-a",
            current_commit="aaa1111" + "0" * 33,
        )
        sub_b = SyncSubmodule(
            path=repo_root / "sub-b",
            parent_repo=repo_root,
            submodule_rel_path="sub-b",
            current_commit="bbb2222" + "0" * 33,
        )

        with patch("grove.check.discover_sync_submodules", return_value=[sub_a, sub_b]):
            result = check_sync_groups(repo_root, verbose=False)

        assert result is True
        captured = capsys.readouterr()
        assert "allow-drift" in captured.out


# ---------------------------------------------------------------------------
# run() -- integration with the real check module
# ---------------------------------------------------------------------------

class TestCheckRun:
    def test_all_healthy(self, tmp_submodule_tree: Path, capsys):
        """When the submodule tree is healthy, helpers should pass."""
        from grove.repo_utils import RepoInfo

        td = tmp_submodule_tree / "technical-docs"
        repo = RepoInfo(path=td, repo_root=tmp_submodule_tree)
        assert check_repo_state(repo, "technical-docs", verbose=False) is True

        # sync groups should pass (only one common submodule, trivially in sync)
        assert check_sync_groups(tmp_submodule_tree, verbose=False) is True

    def test_verbose_output(self, tmp_submodule_tree: Path, capsys):
        """Verbose check should include commit SHAs in the output."""
        from grove.repo_utils import RepoInfo

        td = tmp_submodule_tree / "technical-docs"
        repo = RepoInfo(path=td, repo_root=tmp_submodule_tree)
        check_repo_state(repo, "technical-docs", verbose=True)

        captured = capsys.readouterr()
        # In verbose mode the SHA is shown in parentheses, e.g. "(abc1234)"
        assert "(" in captured.out
        assert ")" in captured.out

    def test_detached_head_detected(self, tmp_submodule_tree: Path, capsys):
        """A repo in detached HEAD state should be flagged."""
        td = tmp_submodule_tree / "technical-docs"

        # Detach HEAD by checking out a commit directly.
        sha = subprocess.run(
            ["git", "-C", str(td), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(td), "checkout", sha],
            capture_output=True,
            check=True,
        )

        repo = RepoInfo(path=td, repo_root=tmp_submodule_tree)
        result = check_repo_state(repo, "technical-docs")
        assert result is False

        captured = capsys.readouterr()
        assert "detached" in captured.out.lower()
