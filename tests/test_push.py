"""Tests for grove.push."""

import argparse
from pathlib import Path
from unittest.mock import patch

from grove.config import load_config
from grove.push import _compute_push_filter_set
from grove.repo_utils import RepoInfo, RepoStatus, discover_repos_from_gitmodules


class TestMultiInstanceFixture:
    """Smoke tests for the tmp_sync_group_multi_instance fixture."""

    def test_fixture_creates_expected_layout(self, tmp_sync_group_multi_instance: Path):
        """The multi-instance fixture should create three sync-group instances."""
        root = tmp_sync_group_multi_instance
        for parent_name in ["frontend", "backend", "shared"]:
            common = root / parent_name / "libs" / "common"
            assert common.exists(), f"{parent_name}/libs/common should exist"
            assert (common / ".git").exists(), f"{parent_name}/libs/common should be a git repo"

    def test_fixture_discovers_all_repos(self, tmp_sync_group_multi_instance: Path):
        """Discovery should find root + 3 parents + 3 common instances = 7 repos."""
        repos = discover_repos_from_gitmodules(tmp_sync_group_multi_instance)
        assert len(repos) == 7
        rel_paths = {r.rel_path for r in repos}
        assert "(root)" in rel_paths
        for name in ["frontend", "backend", "shared"]:
            assert name in rel_paths
            assert f"{name}/libs/common" in rel_paths

    def test_diverged_fixture_has_different_commits(self, tmp_sync_group_diverged: Path):
        """The diverged fixture should have different HEADs in frontend/backend common."""
        import subprocess

        def get_head(repo_path):
            return subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()

        root = tmp_sync_group_diverged
        fe_sha = get_head(root / "frontend" / "libs" / "common")
        be_sha = get_head(root / "backend" / "libs" / "common")
        sh_sha = get_head(root / "shared" / "libs" / "common")

        # frontend and backend diverged from shared
        assert fe_sha != sh_sha
        assert be_sha != sh_sha
        # frontend and backend are different from each other
        assert fe_sha != be_sha


class TestPushDiscovery:
    def test_discovers_repos_in_tree(self, tmp_submodule_tree: Path):
        """discover_repos_from_gitmodules should find repos in the submodule tree."""
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
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

        args = argparse.Namespace(dry_run=False, skip_checks=False)
        with patch("grove.push.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        # No remotes set up, so repos validate as up-to-date or no-remote
        assert result == 0

    def test_dry_run_returns_zero(self, tmp_submodule_tree: Path):
        """Dry run should complete without errors."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=True, skip_checks=False)
        with patch("grove.push.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        assert result == 0

    def test_sync_check_blocks_push_when_out_of_sync(self, tmp_submodule_tree: Path):
        """Push should fail when sync groups are out of sync."""
        from grove.push import run
        import argparse

        args = argparse.Namespace(dry_run=False, skip_checks=False)
        with (
            patch("grove.push.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.push.check_sync_groups", return_value=False),
        ):
            result = run(args)
        assert result == 1

    def test_skip_checks_bypasses_sync_check(self, tmp_submodule_tree: Path):
        """--skip-checks should allow push even when sync groups are out of sync."""
        from grove.push import run

        args = argparse.Namespace(dry_run=False, skip_checks=True)
        with (
            patch("grove.push.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.push.check_sync_groups", return_value=False),
        ):
            result = run(args)
        assert result == 0


class TestPushFilterSet:
    """Unit tests for _compute_push_filter_set()."""

    def test_no_filters_returns_none(self, tmp_sync_group_multi_instance: Path):
        """When no filters are specified, return None (push all)."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set([], None, None, root, config, repos)
        assert result is None

    def test_positional_path_exact_match(self, tmp_sync_group_multi_instance: Path):
        """Positional paths should match repos exactly by rel_path."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(["frontend"], None, None, root, config, repos)
        assert result is not None
        assert len(result) == 1
        assert root / "frontend" in result

    def test_positional_path_multiple(self, tmp_sync_group_multi_instance: Path):
        """Multiple positional paths should all be included."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(
            ["frontend", "backend"], None, None, root, config, repos,
        )
        assert result is not None
        assert len(result) == 2
        assert root / "frontend" in result
        assert root / "backend" in result

    def test_unknown_path_returns_empty(self, tmp_sync_group_multi_instance: Path):
        """An unknown path should return an empty set (error)."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(
            ["nonexistent"], None, None, root, config, repos,
        )
        assert result == set()

    def test_sync_group_filter_includes_parents(self, tmp_sync_group_multi_instance: Path):
        """--sync-group should include all parent repos of the sync group."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set([], "common", None, root, config, repos)
        assert result is not None
        # Parents are: frontend, backend, shared, and root
        paths = {r.relative_to(root) if r != root else Path(".") for r in result}
        assert Path("frontend") in paths
        assert Path("backend") in paths
        assert Path("shared") in paths

    def test_unknown_sync_group_returns_empty(self, tmp_sync_group_multi_instance: Path):
        """An unknown sync group name should return empty set (error)."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set([], "nonexistent", None, root, config, repos)
        assert result == set()

    def test_cascade_filter_includes_chain(self, tmp_sync_group_multi_instance: Path):
        """--cascade should include the entire chain from leaf to root."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(
            [], None, "frontend/libs/common", root, config, repos,
        )
        assert result is not None
        # Chain: frontend/libs/common → frontend → root
        assert root / "frontend" / "libs" / "common" in result
        assert root / "frontend" in result
        assert root in result
        assert len(result) == 3

    def test_cascade_filter_invalid_path_returns_empty(
        self, tmp_sync_group_multi_instance: Path,
    ):
        """--cascade with an invalid path should return empty set (error)."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(
            [], None, "nonexistent/path", root, config, repos,
        )
        assert result == set()

    def test_filters_compose_with_union(self, tmp_sync_group_multi_instance: Path):
        """Multiple filter types should use union semantics."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        # Combine positional ("shared") + cascade (frontend chain)
        result = _compute_push_filter_set(
            ["shared"], None, "frontend/libs/common", root, config, repos,
        )
        assert result is not None
        # Union: {shared} ∪ {frontend/libs/common, frontend, root}
        assert root / "shared" in result
        assert root / "frontend" / "libs" / "common" in result
        assert root / "frontend" in result
        assert root in result

    def test_cascade_includes_sync_group_leaf(self, tmp_sync_group_multi_instance: Path):
        """--cascade should include a sync-group submodule as the leaf."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        repos = discover_repos_from_gitmodules(root)
        result = _compute_push_filter_set(
            [], None, "frontend/libs/common", root, config, repos,
        )
        assert result is not None
        # The leaf (frontend/libs/common) IS a sync-group submodule,
        # but --cascade includes it anyway
        assert root / "frontend" / "libs" / "common" in result


class TestPushRunWithFilters:
    """Integration tests for push run() with filter arguments."""

    def test_no_filters_preserves_original_behavior(self, tmp_submodule_tree: Path):
        """Without filters, push should work exactly as before."""
        from grove.push import run

        args = argparse.Namespace(dry_run=False, skip_checks=False)
        with patch("grove.push.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        assert result == 0

    def test_filters_skip_sync_group_check(self, tmp_sync_group_multi_instance: Path):
        """When filters are active, sync-group consistency check is skipped."""
        from grove.push import run

        args = argparse.Namespace(
            dry_run=True, skip_checks=False,
            paths=["frontend"], sync_group=None, cascade=None,
        )
        with patch("grove.push.find_repo_root", return_value=tmp_sync_group_multi_instance):
            # This should NOT call check_sync_groups at all
            with patch("grove.push.check_sync_groups") as mock_check:
                result = run(args)
                mock_check.assert_not_called()
        assert result == 0

    def test_invalid_filter_returns_error(self, tmp_sync_group_multi_instance: Path):
        """Invalid filter path should cause run() to return 1."""
        from grove.push import run

        args = argparse.Namespace(
            dry_run=False, skip_checks=False,
            paths=["nonexistent"], sync_group=None, cascade=None,
        )
        with patch("grove.push.find_repo_root", return_value=tmp_sync_group_multi_instance):
            result = run(args)
        assert result == 1
