"""Tests for grove.push."""

from pathlib import Path
from unittest.mock import patch

from grove.repo_utils import RepoInfo, RepoStatus, discover_repos


class TestPushDiscovery:
    def test_discovers_repos_in_tree(self, tmp_submodule_tree: Path):
        """discover_repos should find repos in the submodule tree."""
        repos = discover_repos(tmp_submodule_tree)
        # Should find at least the parent and technical-docs
        assert len(repos) >= 2
        paths = [r.path for r in repos]
        assert tmp_submodule_tree in paths
        assert tmp_submodule_tree / "technical-docs" in paths

    def test_validation_detects_uncommitted(self, tmp_submodule_tree: Path):
        """Repos with uncommitted changes should fail validation."""
        (tmp_submodule_tree / ".grove.toml").write_text("# modified\n")
        info = RepoInfo(path=tmp_submodule_tree, repo_root=tmp_submodule_tree)
        result = info.validate(allow_no_remote=True)
        assert result is False
        assert info.status == RepoStatus.UNCOMMITTED


class TestPushRun:
    def test_nothing_to_push(self, tmp_submodule_tree: Path):
        """When all repos are up-to-date, push should return 0."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=False, force=False)
        with patch("grove.push.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        # No remotes set up, so repos validate as up-to-date or no-remote
        assert result == 0

    def test_dry_run_returns_zero(self, tmp_submodule_tree: Path):
        """Dry run should complete without errors."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=True, force=False)
        with patch("grove.push.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        assert result == 0

    def test_sync_check_blocks_push_when_out_of_sync(self, tmp_submodule_tree: Path):
        """Push should fail when sync groups are out of sync."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=False, force=False)
        with (
            patch("grove.push.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.push.check_sync_groups", return_value=False),
        ):
            result = run(args)
        assert result == 1

    def test_force_bypasses_sync_check(self, tmp_submodule_tree: Path):
        """--force should allow push even when sync groups are out of sync."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=False, force=True)
        with (
            patch("grove.push.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.push.check_sync_groups", return_value=False),
        ):
            result = run(args)
        assert result == 0
