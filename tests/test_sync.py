"""Tests for grove.sync."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from grove.config import SyncGroup
from grove.repo_utils import parse_gitmodules
from grove.sync import _sync_group, resolve_remote_url, resolve_target_commit


class TestParseGitmodules:
    def test_extracts_matching_submodule(self, tmp_path: Path):
        """parse_gitmodules with url_match should return only matching entries."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "common"]\n'
            "    path = common\n"
            "    url = git@github.com:Org/my-shared-lib.git\n"
        )
        results = parse_gitmodules(gitmodules, url_match="my-shared-lib")
        assert len(results) == 1
        name, path, url = results[0]
        assert name == "common"
        assert path == "common"
        assert "my-shared-lib" in url

    def test_returns_all_without_url_match(self, tmp_path: Path):
        """parse_gitmodules without url_match should return all entries."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "common"]\n'
            "    path = common\n"
            "    url = git@github.com:Org/my-shared-lib.git\n"
            '[submodule "other"]\n'
            "    path = other\n"
            "    url = git@github.com:Org/other-repo.git\n"
        )
        results = parse_gitmodules(gitmodules)
        assert len(results) == 2

    def test_ignores_non_matching_submodule(self, tmp_path: Path):
        """Submodules whose URL does not contain the url_match string
        should be excluded."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "other"]\n'
            "    path = other\n"
            "    url = git@github.com:Org/other-repo.git\n"
        )
        results = parse_gitmodules(gitmodules, url_match="my-shared-lib")
        assert len(results) == 0

    def test_multiple_submodules_mixed(self, tmp_path: Path):
        """Only submodules matching url_match should appear."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "common"]\n'
            "    path = source/common\n"
            "    url = git@github.com:Org/my-shared-lib.git\n"
            '[submodule "other"]\n'
            "    path = other\n"
            "    url = git@github.com:Org/other-repo.git\n"
            '[submodule "theme2"]\n'
            "    path = theme2\n"
            "    url = https://github.com/Org/my-shared-lib.git\n"
        )
        results = parse_gitmodules(gitmodules, url_match="my-shared-lib")
        assert len(results) == 2
        paths = [r[1] for r in results]
        assert "source/common" in paths
        assert "theme2" in paths

    def test_missing_file(self, tmp_path: Path):
        """A nonexistent .gitmodules file should return an empty list."""
        gitmodules = tmp_path / ".gitmodules"
        results = parse_gitmodules(gitmodules)
        assert results == []

    def test_empty_file(self, tmp_path: Path):
        """An empty .gitmodules file should return an empty list."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text("")
        results = parse_gitmodules(gitmodules)
        assert results == []

    def test_returns_name_path_url_tuples(self, tmp_path: Path):
        """Each entry should be a (name, path, url) tuple."""
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "my-sub"]\n'
            "    path = libs/my-sub\n"
            "    url = git@github.com:Org/my-sub.git\n"
        )
        results = parse_gitmodules(gitmodules)
        assert len(results) == 1
        name, path, url = results[0]
        assert name == "my-sub"
        assert path == "libs/my-sub"
        assert url == "git@github.com:Org/my-sub.git"


# ---------------------------------------------------------------------------
# resolve_target_commit
# ---------------------------------------------------------------------------

class TestResolveTargetCommit:
    def test_explicit_sha_returned_as_is(self):
        """An explicit commit SHA should be returned without modification."""
        sha = "abc1234"
        result_sha, source = resolve_target_commit(sha, Path("/nonexistent"))
        assert result_sha == sha
        assert source == "CLI argument"

    def test_full_sha_returned(self):
        """A full 40-char SHA should be accepted."""
        sha = "a" * 40
        result_sha, _ = resolve_target_commit(sha, Path("/nonexistent"))
        assert result_sha == sha

    def test_invalid_sha_raises(self):
        """A non-hex string should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid commit SHA"):
            resolve_target_commit("not-a-sha!", Path("/nonexistent"))

    def test_too_short_sha_raises(self):
        """A SHA shorter than 7 chars should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid commit SHA"):
            resolve_target_commit("abc12", Path("/nonexistent"))

    def test_missing_standalone_repo_raises(self, tmp_path: Path):
        """When no commit is given and standalone repo doesn't exist, should raise."""
        with pytest.raises(ValueError, match="not found"):
            resolve_target_commit(None, tmp_path / "nonexistent")

    def test_none_standalone_and_no_remote_url_raises(self):
        """When standalone_repo is None and no remote_url, should raise."""
        with pytest.raises(ValueError, match="Cannot resolve target commit"):
            resolve_target_commit(None, None)

    def test_ls_remote_resolves_commit(self):
        """When standalone_repo is None and remote_url is given, should use git ls-remote."""
        fake_sha = "a" * 40
        fake_output = f"{fake_sha}\trefs/heads/main\n"

        with patch("grove.sync.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=fake_output, stderr=""
            )
            sha, source = resolve_target_commit(None, None, remote_url="https://example.com/repo.git")

        assert sha == fake_sha
        assert "example.com" in source
        mock_run.assert_called_once()

    def test_ls_remote_failure_raises(self):
        """When git ls-remote fails, should raise ValueError."""
        with patch("grove.sync.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr="fatal: could not read"
            )
            with pytest.raises(ValueError, match="ls-remote failed"):
                resolve_target_commit(None, None, remote_url="https://example.com/repo.git")

    def test_ls_remote_no_main_branch_raises(self):
        """When ls-remote returns empty output (no main branch), should raise."""
        with patch("grove.sync.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            with pytest.raises(ValueError, match="No 'main' branch"):
                resolve_target_commit(None, None, remote_url="https://example.com/repo.git")


# ---------------------------------------------------------------------------
# resolve_remote_url
# ---------------------------------------------------------------------------

class TestResolveRemoteUrl:
    def test_returns_matching_url(self, tmp_path: Path):
        """Should return the URL of the first matching submodule."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "common"]\n'
            "    path = common\n"
            "    url = https://github.com/Org/my-shared-lib.git\n"
        )
        url = resolve_remote_url(tmp_path, "my-shared-lib")
        assert url == "https://github.com/Org/my-shared-lib.git"

    def test_returns_none_no_match(self, tmp_path: Path):
        """Should return None when no submodule matches."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "other"]\n'
            "    path = other\n"
            "    url = https://github.com/Org/other-repo.git\n"
        )
        url = resolve_remote_url(tmp_path, "my-shared-lib")
        assert url is None

    def test_returns_none_no_gitmodules(self, tmp_path: Path):
        """Should return None when .gitmodules doesn't exist."""
        url = resolve_remote_url(tmp_path, "anything")
        assert url is None


# ---------------------------------------------------------------------------
# Parent pointer propagation
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=True,
    )


class TestSyncParentPointerPropagation:
    def test_sync_propagates_child_pointer_updates_to_parent(
        self, tmp_sync_group_multi_instance: Path,
    ):
        """After syncing common, the root should also update its pointers
        to frontend/backend/shared (which received sync commits)."""
        root = tmp_sync_group_multi_instance
        common_origin = root.parent / "common_origin"

        # Add a new commit to common_origin so sync has something to do
        (common_origin / "new_feature.py").write_text("# new feature\n")
        _git(common_origin, "add", "new_feature.py")
        _git(common_origin, "commit", "-m", "Add new feature")

        target_sha = _git(common_origin, "rev-parse", "HEAD").stdout.strip()

        group = SyncGroup(
            name="common",
            url_match="common_origin",
            standalone_repo=common_origin,
        )

        result = _sync_group(
            group, root, commit_arg=target_sha,
            dry_run=False, no_push=True, force=True,
        )

        assert result == 0

        # Root should have no modified submodules — all pointers up to date
        status = _git(root, "status", "--porcelain")
        assert status.stdout.strip() == "", (
            f"Root has uncommitted submodule changes after sync:\n{status.stdout}"
        )
