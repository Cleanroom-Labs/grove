"""Tests for grove.cascade."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside *cwd*."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=True,
    )

from grove.cascade import (
    CascadeState,
    RepoCascadeEntry,
    _build_linear_entries,
    _build_unified_cascade_plan,
    _check_sync_group_consistency,
    _determine_tiers,
    _discover_cascade_chain,
    _expand_linear_for_intermediate_sync_groups,
    _find_sync_group_for_path,
    _get_state_path,
    abort_cascade,
    continue_cascade,
    run_cascade,
    show_cascade_status,
)
from grove.config import load_config
from grove.repo_utils import discover_repos_from_gitmodules


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

class TestCascadeState:
    def _make_state(self) -> CascadeState:
        return CascadeState(
            submodule_path="libs/common",
            started_at="2026-01-15T12:00:00+00:00",
            system_mode="default",
            quick=False,
            repos=[
                RepoCascadeEntry(rel_path="libs/common", role="leaf", status="committed"),
                RepoCascadeEntry(rel_path="services/api", role="intermediate", status="paused",
                                 failed_tier="integration-tests"),
                RepoCascadeEntry(rel_path=".", role="root", status="pending"),
            ],
        )

    def test_save_and_load(self, tmp_path: Path):
        """State should round-trip through save/load."""
        state_path = tmp_path / "cascade-state.json"
        state = self._make_state()
        state.save(state_path)
        loaded = CascadeState.load(state_path)

        assert loaded.submodule_path == "libs/common"
        assert loaded.system_mode == "default"
        assert loaded.quick is False
        assert len(loaded.repos) == 3
        assert loaded.repos[0].role == "leaf"
        assert loaded.repos[1].status == "paused"
        assert loaded.repos[1].failed_tier == "integration-tests"

    def test_remove(self, tmp_path: Path):
        """remove() should delete the state file."""
        state_path = tmp_path / "cascade-state.json"
        state_path.write_text("{}")
        CascadeState.remove(state_path)
        assert not state_path.exists()

    def test_remove_missing_ok(self, tmp_path: Path):
        """remove() should not error when file doesn't exist."""
        state_path = tmp_path / "does-not-exist.json"
        CascadeState.remove(state_path)  # should not raise

    def test_save_creates_valid_json(self, tmp_path: Path):
        """Saved state should be valid JSON."""
        state_path = tmp_path / "cascade-state.json"
        state = self._make_state()
        state.save(state_path)
        data = json.loads(state_path.read_text())
        assert data["submodule_path"] == "libs/common"
        assert len(data["repos"]) == 3

    def test_diagnosis_round_trips(self, tmp_path: Path):
        """Diagnosis list should survive save/load."""
        state_path = tmp_path / "cascade-state.json"
        state = self._make_state()
        state.repos[1].diagnosis = [
            {"rel_path": "libs/common", "tier": "local-tests", "passed": True},
        ]
        state.save(state_path)
        loaded = CascadeState.load(state_path)
        assert loaded.repos[1].diagnosis == [
            {"rel_path": "libs/common", "tier": "local-tests", "passed": True},
        ]


# ---------------------------------------------------------------------------
# _determine_tiers
# ---------------------------------------------------------------------------

class TestDetermineTiers:
    def test_leaf_default(self):
        """Leaf in default mode: local + contract only."""
        tiers = _determine_tiers("leaf", "default", quick=False)
        assert tiers == ["local-tests", "contract-tests"]

    def test_intermediate_default(self):
        """Intermediate in default mode: local + contract + integration."""
        tiers = _determine_tiers("intermediate", "default", quick=False)
        assert tiers == ["local-tests", "contract-tests", "integration-tests"]

    def test_root_default(self):
        """Root in default mode: all four tiers."""
        tiers = _determine_tiers("root", "default", quick=False)
        assert tiers == ["local-tests", "contract-tests", "integration-tests", "system-tests"]

    def test_leaf_system_all(self):
        """Leaf with --system: adds system-tests."""
        tiers = _determine_tiers("leaf", "all", quick=False)
        assert "system-tests" in tiers

    def test_root_system_none(self):
        """Root with --no-system: no system-tests."""
        tiers = _determine_tiers("root", "none", quick=False)
        assert "system-tests" not in tiers

    def test_quick_mode(self):
        """Quick mode: only local + contract regardless of role."""
        for role in ("leaf", "intermediate", "root"):
            tiers = _determine_tiers(role, "default", quick=True)
            assert tiers == ["local-tests", "contract-tests"]

    def test_intermediate_system_all(self):
        """Intermediate with --system: includes system-tests."""
        tiers = _determine_tiers("intermediate", "all", quick=False)
        assert tiers == [
            "local-tests", "contract-tests", "integration-tests", "system-tests",
        ]


# ---------------------------------------------------------------------------
# _discover_cascade_chain
# ---------------------------------------------------------------------------

class TestDiscoverCascadeChain:
    def test_chain_from_grandchild(self, tmp_submodule_tree: Path):
        """Chain from grandchild should be [grandchild, child, parent]."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)
        grandchild = root / "technical-docs" / "common"

        chain = _discover_cascade_chain(grandchild, repos)

        assert len(chain) == 3
        assert chain[0].path == grandchild.resolve()
        assert chain[1].path == (root / "technical-docs").resolve()
        assert chain[2].path == root.resolve()

    def test_chain_from_child(self, tmp_submodule_tree: Path):
        """Chain from child should be [child, parent]."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)
        child = root / "technical-docs"

        chain = _discover_cascade_chain(child, repos)

        assert len(chain) == 2
        assert chain[0].path == child.resolve()
        assert chain[1].path == root.resolve()

    def test_unknown_path_raises(self, tmp_submodule_tree: Path):
        """Nonexistent submodule path should raise ValueError."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)

        with pytest.raises(ValueError, match="not a recognized repository"):
            _discover_cascade_chain(root / "nonexistent", repos)


# ---------------------------------------------------------------------------
# Sync-group awareness (Feature 2)
# ---------------------------------------------------------------------------

class TestFindSyncGroupForPath:
    """Tests for _find_sync_group_for_path()."""

    def test_sync_group_submodule_detected(self, tmp_sync_group_multi_instance: Path):
        """A sync-group submodule should be identified."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        target = root / "frontend" / "libs" / "common"

        result = _find_sync_group_for_path(target, root, config)

        assert result is not None
        name, group = result
        assert name == "common"
        assert "common_origin" in group.url_match

    def test_non_sync_group_submodule_returns_none(self, tmp_sync_group_multi_instance: Path):
        """A regular submodule (not in a sync group) should return None."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)
        # 'frontend' is a submodule but not in any sync group
        target = root / "frontend"

        result = _find_sync_group_for_path(target, root, config)

        assert result is None

    def test_all_instances_detected(self, tmp_sync_group_multi_instance: Path):
        """Each instance of the sync group should be detected."""
        root = tmp_sync_group_multi_instance
        config = load_config(root)

        for parent in ["frontend", "backend", "shared"]:
            target = root / parent / "libs" / "common"
            result = _find_sync_group_for_path(target, root, config)
            assert result is not None, f"{parent}/libs/common should be in sync group"
            assert result[0] == "common"


class TestCheckSyncGroupConsistency:
    """Tests for _check_sync_group_consistency()."""

    def test_consistent_group_passes(self, tmp_sync_group_multi_instance: Path):
        """When all instances are at the same commit, the check should pass."""
        root = tmp_sync_group_multi_instance
        result = _check_sync_group_consistency(
            "common", root, "common_origin", force=False,
        )
        assert result is True

    def test_diverged_group_fails(self, tmp_sync_group_diverged: Path, capsys):
        """When instances have different commits, the check should fail."""
        root = tmp_sync_group_diverged
        result = _check_sync_group_consistency(
            "common", root, "common_origin", force=False,
        )
        assert result is False
        output = capsys.readouterr().out
        assert "not in sync" in output.lower()
        assert "grove sync" in output.lower()

    def test_diverged_group_force_proceeds(self, tmp_sync_group_diverged: Path, capsys):
        """With --force, diverged instances should still proceed."""
        root = tmp_sync_group_diverged
        result = _check_sync_group_consistency(
            "common", root, "common_origin", force=True,
        )
        assert result is True
        output = capsys.readouterr().out
        assert "warning" in output.lower()


class TestCascadeSyncGroupCheck:
    """Integration tests: sync-group check during cascade start."""

    def test_non_sync_group_leaf_no_check(self, tmp_submodule_tree: Path, capsys):
        """Cascading a non-sync-group submodule should skip the check entirely."""
        root = tmp_submodule_tree
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\nlocal-tests = "true"\n'
        )
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs", dry_run=True)
        assert result == 0
        output = capsys.readouterr().out
        # Should NOT mention sync-group detection
        assert "sync-group detected" not in output.lower()

    def test_consistent_sync_group_proceeds(
        self, tmp_sync_group_multi_instance: Path, capsys,
    ):
        """Cascading a consistent sync-group submodule should proceed."""
        root = tmp_sync_group_multi_instance
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("frontend/libs/common", dry_run=True)
        assert result == 0
        output = capsys.readouterr().out
        assert "sync-group detected" in output.lower()

    def test_inconsistent_sync_group_fails(
        self, tmp_sync_group_diverged: Path, capsys,
    ):
        """Cascading a diverged sync-group submodule should fail."""
        root = tmp_sync_group_diverged
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("frontend/libs/common")
        assert result == 1
        output = capsys.readouterr().out
        assert "not in sync" in output.lower()

    def test_inconsistent_sync_group_force_proceeds(
        self, tmp_sync_group_diverged: Path, capsys,
    ):
        """--force should bypass the sync-group consistency check."""
        root = tmp_sync_group_diverged
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("frontend/libs/common", dry_run=True, force=True)
        assert result == 0
        output = capsys.readouterr().out
        assert "warning" in output.lower()


# ---------------------------------------------------------------------------
# Integration tests: run_cascade
# ---------------------------------------------------------------------------

class TestRunCascade:
    def test_dry_run(self, tmp_submodule_tree: Path, capsys):
        """Dry run should preview the cascade without making changes."""
        root = tmp_submodule_tree
        # Add cascade config with a test command
        config_path = root / ".grove.toml"
        config_path.write_text(
            config_path.read_text() +
            '\n[cascade]\n'
            'local-tests = "true"\n'
        )

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common", dry_run=True)

        assert result == 0
        output = capsys.readouterr().out
        assert "dry-run" in output.lower()

    def test_happy_path_with_passing_tests(self, tmp_submodule_tree: Path, capsys):
        """Cascade with passing tests should commit pointer updates."""
        root = tmp_submodule_tree
        grandchild = root / "technical-docs" / "common"

        # Configure cascade with always-passing test
        config_path = root / ".grove.toml"
        config_path.write_text(
            config_path.read_text() +
            '\n[cascade]\n'
            'local-tests = "true"\n'
        )

        # Make a change in the grandchild to cascade
        (grandchild / "new-file.txt").write_text("new content\n")

        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")

        assert result == 0
        output = capsys.readouterr().out
        assert "complete" in output.lower()

        # State file should be cleaned up on success
        state_path = _get_state_path(root)
        assert not state_path.exists()

    def test_already_in_progress(self, tmp_submodule_tree: Path, capsys):
        """Starting a cascade while one is active should fail."""
        root = tmp_submodule_tree
        state_path = _get_state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"dummy": true}')

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")

        assert result == 1
        output = capsys.readouterr().out
        assert "already in progress" in output.lower()

    def test_root_path_rejected(self, tmp_submodule_tree: Path, capsys):
        """Cascading from root should fail (need at least leaf + parent)."""
        root = tmp_submodule_tree
        config_path = root / ".grove.toml"
        config_path.write_text(
            config_path.read_text() +
            '\n[cascade]\n'
            'local-tests = "true"\n'
        )

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade(".")

        # Should fail because "." resolves to root — no parent chain
        assert result == 1

    def test_no_test_config_warns(self, tmp_submodule_tree: Path, capsys):
        """Cascade with no test config should warn but succeed."""
        root = tmp_submodule_tree
        # Rewrite config without any test commands or cascade section
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
        )

        grandchild = root / "technical-docs" / "common"
        (grandchild / "new-file.txt").write_text("new content\n")

        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")

        assert result == 0
        output = capsys.readouterr().out
        assert "no cascade test tiers configured" in output.lower()


# ---------------------------------------------------------------------------
# Integration tests: pause / continue / abort
# ---------------------------------------------------------------------------

class TestCascadePauseResume:
    def test_failing_test_pauses(self, tmp_submodule_tree: Path, capsys):
        """A failing test should pause the cascade and save state."""
        root = tmp_submodule_tree
        grandchild = root / "technical-docs" / "common"

        # Configure with a failing test
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "false"\n'
        )

        # Make a change in the leaf
        (grandchild / "new-file.txt").write_text("content\n")

        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")

        assert result == 1
        output = capsys.readouterr().out
        assert "paused" in output.lower()

        # State file should exist
        state_path = _get_state_path(root)
        assert state_path.exists()

    def test_continue_after_fix(self, tmp_submodule_tree: Path, capsys):
        """Continuing after fixing the issue should complete the cascade."""
        root = tmp_submodule_tree
        grandchild = root / "technical-docs" / "common"

        # Step 1: Start with a failing test
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "false"\n'
        )

        (grandchild / "new-file.txt").write_text("content\n")

        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")
        assert result == 1

        # Step 2: Fix the test (change to passing)
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "true"\n'
        )

        # Step 3: Continue
        capsys.readouterr()  # clear previous output
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = continue_cascade()

        assert result == 0
        output = capsys.readouterr().out
        assert "complete" in output.lower()

        # State should be cleaned up
        state_path = _get_state_path(root)
        assert not state_path.exists()


class TestCascadeAbort:
    def test_abort_restores_state(self, tmp_submodule_tree: Path, capsys):
        """Aborting should restore repos to their pre-cascade state."""
        root = tmp_submodule_tree
        grandchild = root / "technical-docs" / "common"
        child = root / "technical-docs"

        # Record original child HEAD

        original_child_head = _git(child, "rev-parse", "HEAD").stdout.strip()

        # Configure with passing tests so cascade commits
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "true"\n'
            'integration-tests = "false"\n'  # fail at intermediate
        )

        # Make a change in the leaf
        (grandchild / "new-file.txt").write_text("content\n")
        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs/common")
        # Should pause at child's integration-tests
        assert result == 1

        # Abort
        capsys.readouterr()
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = abort_cascade()

        assert result == 0
        output = capsys.readouterr().out
        assert "aborted" in output.lower()

        # State should be cleaned up
        state_path = _get_state_path(root)
        assert not state_path.exists()

    def test_abort_when_no_cascade(self, tmp_submodule_tree: Path, capsys):
        """Aborting with no cascade in progress should fail."""
        root = tmp_submodule_tree
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = abort_cascade()

        assert result == 1
        output = capsys.readouterr().out
        assert "no cascade in progress" in output.lower()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestCascadeStatus:
    def test_status_no_cascade(self, tmp_submodule_tree: Path, capsys):
        """Status with no cascade should report cleanly."""
        root = tmp_submodule_tree
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = show_cascade_status()

        assert result == 0
        output = capsys.readouterr().out
        assert "no cascade in progress" in output.lower()

    def test_status_during_cascade(self, tmp_submodule_tree: Path, capsys):
        """Status during a paused cascade should show details."""
        root = tmp_submodule_tree
        grandchild = root / "technical-docs" / "common"

        # Start a cascade that will pause
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "false"\n'
        )

        (grandchild / "new-file.txt").write_text("content\n")

        _git(grandchild, "add", "new-file.txt")
        _git(grandchild, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            run_cascade("technical-docs/common")

        # Check status
        capsys.readouterr()
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = show_cascade_status()

        assert result == 0
        output = capsys.readouterr().out
        assert "paused" in output.lower() or "local-tests" in output.lower()


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------

class TestRunDispatcher:
    def test_no_path_no_flags(self, capsys):
        """run() with no path and no flags should return usage error."""
        from grove.cascade import run as cascade_run
        from argparse import Namespace
        args = Namespace(
            continue_cascade=False, abort=False, status=False,
            path=None, dry_run=False, system=False, no_system=False, quick=False,
        )
        result = cascade_run(args)
        assert result == 2

    def test_system_flag_sets_mode(self, tmp_submodule_tree: Path):
        """--system flag should set system_mode='all'."""
        from grove.cascade import run as cascade_run
        from argparse import Namespace
        args = Namespace(
            continue_cascade=False, abort=False, status=False,
            path="technical-docs/common", dry_run=True,
            system=True, no_system=False, quick=False,
        )
        root = tmp_submodule_tree
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "true"\n'
        )
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = cascade_run(args)
        assert result == 0

    def test_no_system_flag_sets_mode(self, tmp_submodule_tree: Path):
        """--no-system flag should set system_mode='none'."""
        from grove.cascade import run as cascade_run
        from argparse import Namespace
        args = Namespace(
            continue_cascade=False, abort=False, status=False,
            path="technical-docs/common", dry_run=True,
            system=False, no_system=True, quick=False,
        )
        root = tmp_submodule_tree
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "true"\n'
        )
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = cascade_run(args)
        assert result == 0


# ---------------------------------------------------------------------------
# DAG cascade plan building (Feature 3)
# ---------------------------------------------------------------------------

class TestBuildLinearEntries:
    """Tests for _build_linear_entries()."""

    def test_correct_roles(self, tmp_submodule_tree: Path):
        """Linear entries should have leaf/intermediate/root roles."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)
        grandchild = root / "technical-docs" / "common"
        chain = _discover_cascade_chain(grandchild, repos)

        entries = _build_linear_entries(chain, root)

        assert len(entries) == 3
        assert entries[0].role == "leaf"
        assert entries[1].role == "intermediate"
        assert entries[2].role == "root"

    def test_child_rel_paths_are_parent_relative(self, tmp_submodule_tree: Path):
        """child_rel_paths should be relative to the parent repo, not root."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)
        grandchild = root / "technical-docs" / "common"
        chain = _discover_cascade_chain(grandchild, repos)

        entries = _build_linear_entries(chain, root)

        # Leaf has no children
        assert entries[0].child_rel_paths is None
        # Intermediate (technical-docs) → child is "common" (relative to technical-docs)
        assert entries[1].child_rel_paths == ["common"]
        # Root → child is "technical-docs" (relative to root)
        assert entries[2].child_rel_paths == ["technical-docs"]


class TestBuildUnifiedCascadePlan:
    """Tests for _build_unified_cascade_plan()."""

    def test_dag_includes_all_instances_as_leaves(
        self, tmp_sync_group_multi_instance: Path,
    ):
        """DAG plan should include all sync-group instances as leaves."""
        root = tmp_sync_group_multi_instance
        repos = discover_repos_from_gitmodules(root)
        chain, entries, _isg = _build_unified_cascade_plan(
            "common", "common_origin", root, repos,
        )

        leaf_entries = [e for e in entries if e.role == "leaf"]
        leaf_rels = {e.rel_path for e in leaf_entries}

        assert len(leaf_entries) == 3
        assert "frontend/libs/common" in leaf_rels
        assert "backend/libs/common" in leaf_rels
        assert "shared/libs/common" in leaf_rels

    def test_dag_deduplicates_root(self, tmp_sync_group_multi_instance: Path):
        """DAG plan should have exactly one root entry."""
        root = tmp_sync_group_multi_instance
        repos = discover_repos_from_gitmodules(root)
        chain, entries, _isg = _build_unified_cascade_plan(
            "common", "common_origin", root, repos,
        )

        root_entries = [e for e in entries if e.role == "root"]
        assert len(root_entries) == 1
        assert root_entries[0].rel_path == "."

    def test_dag_depth_ordering(self, tmp_sync_group_multi_instance: Path):
        """Entries should be ordered deepest first (leaves, then intermediates, then root)."""
        root = tmp_sync_group_multi_instance
        repos = discover_repos_from_gitmodules(root)
        chain, entries, _isg = _build_unified_cascade_plan(
            "common", "common_origin", root, repos,
        )

        # All leaves should come before all intermediates, which come before root
        roles = [e.role for e in entries]
        leaf_indices = [i for i, r in enumerate(roles) if r == "leaf"]
        intermediate_indices = [i for i, r in enumerate(roles) if r == "intermediate"]
        root_indices = [i for i, r in enumerate(roles) if r == "root"]

        assert max(leaf_indices) < min(intermediate_indices)
        assert max(intermediate_indices) < min(root_indices)

    def test_dag_correct_child_rel_paths(self, tmp_sync_group_multi_instance: Path):
        """child_rel_paths should be relative to each repo, not root."""
        root = tmp_sync_group_multi_instance
        repos = discover_repos_from_gitmodules(root)
        chain, entries, _isg = _build_unified_cascade_plan(
            "common", "common_origin", root, repos,
        )

        entry_map = {e.rel_path: e for e in entries}

        # Leaves have no children
        assert entry_map["frontend/libs/common"].child_rel_paths is None

        # Intermediates: frontend → libs/common (relative to frontend)
        fe = entry_map["frontend"]
        assert fe.child_rel_paths is not None
        assert "libs/common" in fe.child_rel_paths

        # Root → frontend, backend, shared (relative to root)
        root_entry = entry_map["."]
        assert root_entry.child_rel_paths is not None
        assert "frontend" in root_entry.child_rel_paths
        assert "backend" in root_entry.child_rel_paths
        assert "shared" in root_entry.child_rel_paths

    def test_dag_total_count(self, tmp_sync_group_multi_instance: Path):
        """DAG plan should have 7 repos: 3 leaves + 3 intermediates + 1 root."""
        root = tmp_sync_group_multi_instance
        repos = discover_repos_from_gitmodules(root)
        chain, entries, _isg = _build_unified_cascade_plan(
            "common", "common_origin", root, repos,
        )

        assert len(entries) == 7
        assert len([e for e in entries if e.role == "leaf"]) == 3
        assert len([e for e in entries if e.role == "intermediate"]) == 3
        assert len([e for e in entries if e.role == "root"]) == 1


class TestCascadeDAGExecution:
    """Integration tests for DAG cascade execution."""

    def test_dag_cascade_dry_run(self, tmp_sync_group_multi_instance: Path, capsys):
        """DAG cascade dry run should preview all repos in the plan."""
        root = tmp_sync_group_multi_instance
        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("frontend/libs/common", dry_run=True)

        assert result == 0
        output = capsys.readouterr().out
        assert "dag" in output.lower()
        # Should mention all three instances
        assert "frontend/libs/common" in output
        assert "backend/libs/common" in output
        assert "shared/libs/common" in output

    def test_dag_state_round_trips(self, tmp_sync_group_multi_instance: Path):
        """DAG cascade state should save and load with new fields."""
        root = tmp_sync_group_multi_instance
        state = CascadeState(
            submodule_path="frontend/libs/common",
            started_at="2026-01-15T12:00:00+00:00",
            system_mode="default",
            quick=False,
            repos=[
                RepoCascadeEntry(
                    rel_path="frontend/libs/common", role="leaf",
                    child_rel_paths=None,
                ),
                RepoCascadeEntry(
                    rel_path="frontend", role="intermediate",
                    child_rel_paths=["libs/common"],
                ),
                RepoCascadeEntry(
                    rel_path=".", role="root",
                    child_rel_paths=["frontend", "backend", "shared"],
                ),
            ],
            sync_group_name="common",
            is_dag=True,
        )

        state_path = root / "test-state.json"
        state.save(state_path)
        loaded = CascadeState.load(state_path)

        assert loaded.sync_group_name == "common"
        assert loaded.is_dag is True
        assert loaded.repos[0].child_rel_paths is None
        assert loaded.repos[1].child_rel_paths == ["libs/common"]
        assert loaded.repos[2].child_rel_paths == ["frontend", "backend", "shared"]
        state_path.unlink()

    def test_linear_cascade_regression(self, tmp_submodule_tree: Path, capsys):
        """Non-sync-group cascade should still work with linear chain."""
        root = tmp_submodule_tree
        child = root / "technical-docs"

        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "grandchild_origin"\n'
            '\n'
            '[cascade]\nlocal-tests = "true"\n'
        )

        # Make a change in technical-docs (NOT a sync-group submodule)
        (child / "new-file.txt").write_text("new content\n")
        _git(child, "add", "new-file.txt")
        _git(child, "commit", "-m", "leaf change")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("technical-docs")

        assert result == 0
        output = capsys.readouterr().out
        assert "complete" in output.lower()
        # Should NOT be a DAG cascade
        assert "dag" not in output.lower()


# ---------------------------------------------------------------------------
# Intermediate sync-group expansion
# ---------------------------------------------------------------------------

class TestIntermediateSyncGroupExpansion:
    """Tests for intermediate sync-group detection and plan expansion."""

    def test_expansion_detects_intermediate_sync_group(
        self, tmp_intermediate_sync_group: Path,
    ):
        """Plan for leaf cascade should detect intermediate sync-group peers."""
        root = tmp_intermediate_sync_group
        repos = discover_repos_from_gitmodules(root)
        config = load_config(root)

        # Start with a linear chain from workspace-a/libs/common
        target = (root / "workspace-a" / "libs" / "common").resolve()
        chain = _discover_cascade_chain(target, repos)
        entries = _build_linear_entries(chain, root)

        sg_names = _expand_linear_for_intermediate_sync_groups(
            chain, entries, root, config, repos,
        )

        assert "services" in sg_names
        # Plan should now include workspace-b (the peer)
        rel_paths = {e.rel_path for e in entries}
        assert "workspace-b" in rel_paths
        assert "workspace-a" in rel_paths

    def test_expansion_designates_primary_and_sync_targets(
        self, tmp_intermediate_sync_group: Path,
    ):
        """Exactly one instance should be primary, others sync targets."""
        root = tmp_intermediate_sync_group
        repos = discover_repos_from_gitmodules(root)
        config = load_config(root)

        target = (root / "workspace-a" / "libs" / "common").resolve()
        chain = _discover_cascade_chain(target, repos)
        entries = _build_linear_entries(chain, root)

        _expand_linear_for_intermediate_sync_groups(
            chain, entries, root, config, repos,
        )

        # workspace-a should be primary (has sync_peers)
        entry_map = {e.rel_path: e for e in entries}
        ws_a = entry_map["workspace-a"]
        assert ws_a.sync_peers is not None
        assert "workspace-b" in ws_a.sync_peers
        assert ws_a.sync_primary_rel is None

        # workspace-b should be a sync target
        ws_b = entry_map["workspace-b"]
        assert ws_b.sync_primary_rel == "workspace-a"

    def test_expansion_adds_parent_chains_for_peers(
        self, tmp_intermediate_sync_group: Path,
    ):
        """Root should appear in the plan and have both workspaces as children."""
        root = tmp_intermediate_sync_group
        repos = discover_repos_from_gitmodules(root)
        config = load_config(root)

        target = (root / "workspace-a" / "libs" / "common").resolve()
        chain = _discover_cascade_chain(target, repos)
        entries = _build_linear_entries(chain, root)

        _expand_linear_for_intermediate_sync_groups(
            chain, entries, root, config, repos,
        )

        entry_map = {e.rel_path: e for e in entries}
        root_entry = entry_map["."]
        assert root_entry.child_rel_paths is not None
        assert "workspace-a" in root_entry.child_rel_paths
        assert "workspace-b" in root_entry.child_rel_paths

    def test_no_expansion_without_intermediate_sync_groups(
        self, tmp_submodule_tree: Path,
    ):
        """Non-sync-group intermediates should not cause expansion."""
        root = tmp_submodule_tree
        repos = discover_repos_from_gitmodules(root)

        # Write a config with a sync group that doesn't match intermediates
        (root / ".grove.toml").write_text(
            '[sync-groups.common]\n'
            'url-match = "nonexistent_origin"\n'
            '\n'
            '[cascade]\nlocal-tests = "true"\n'
        )
        config = load_config(root)

        target = (root / "technical-docs").resolve()
        chain = _discover_cascade_chain(target, repos)
        entries = _build_linear_entries(chain, root)

        original_count = len(entries)
        sg_names = _expand_linear_for_intermediate_sync_groups(
            chain, entries, root, config, repos,
        )

        assert sg_names == []
        assert len(entries) == original_count


class TestIntermediateSyncGroupExecution:
    """Integration tests for cascade with intermediate sync groups."""

    def test_cascade_syncs_intermediate_peers(
        self, tmp_intermediate_sync_group: Path, capsys,
    ):
        """Cascade from leaf should sync workspace-b after committing workspace-a."""
        root = tmp_intermediate_sync_group

        # Make a change in workspace-a/libs/common (the leaf)
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common")

        assert result == 0
        output = capsys.readouterr().out
        assert "complete" in output.lower()

        # workspace-a and workspace-b should now point to the same commit
        ws_a_sha = _git(root / "workspace-a", "rev-parse", "HEAD").stdout.strip()
        ws_b_sha = _git(root / "workspace-b", "rev-parse", "HEAD").stdout.strip()
        assert ws_a_sha == ws_b_sha

    def test_dry_run_shows_sync_targets(
        self, tmp_intermediate_sync_group: Path, capsys,
    ):
        """Dry run should show sync-target entries."""
        root = tmp_intermediate_sync_group

        # Make a change in workspace-a/libs/common
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common", dry_run=True)

        assert result == 0
        output = capsys.readouterr().out
        assert "sync target" in output.lower()
        assert "workspace-b" in output

    def test_linear_to_dag_promotion(
        self, tmp_intermediate_sync_group: Path, capsys,
    ):
        """Cascade starting from non-sync-group leaf should become DAG."""
        root = tmp_intermediate_sync_group

        # Make a change in workspace-a/libs/common
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common", dry_run=True)

        assert result == 0
        output = capsys.readouterr().out
        # Should be promoted to DAG because of intermediate sync group
        assert "dag" in output.lower()

    def test_abort_restores_synced_entries(
        self, tmp_intermediate_sync_group: Path, capsys,
    ):
        """Aborting after sync should restore workspace-b to original commit."""
        root = tmp_intermediate_sync_group

        # Record original workspace-b HEAD
        original_ws_b_sha = _git(
            root / "workspace-b", "rev-parse", "HEAD",
        ).stdout.strip()

        # Make a change in workspace-a/libs/common
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        # Run cascade, then abort (cascade succeeds, so we need to
        # test abort on a paused state — use a failing test command)
        (root / ".grove.toml").write_text(
            '[sync-groups.services]\n'
            f'url-match = "service_origin"\n'
            '\n'
            '[cascade]\n'
            'local-tests = "true"\n'
            'integration-tests = "false"\n'  # will fail at root level
        )
        _git(root, "add", ".grove.toml")
        _git(root, "commit", "-m", "Config with failing integration tests")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common")

        # Should have paused at root's integration test failure
        assert result == 1

        # Now abort
        with patch("grove.cascade.find_repo_root", return_value=root):
            abort_result = abort_cascade()
        assert abort_result == 0

        # workspace-b should be restored to its original commit
        restored_ws_b_sha = _git(
            root / "workspace-b", "rev-parse", "HEAD",
        ).stdout.strip()
        assert restored_ws_b_sha == original_ws_b_sha


class TestIntermediateDivergenceResolution:
    """Tests for diverged intermediate sync-group resolution."""

    def test_pre_cascade_auto_resolves_clean_divergence(
        self, tmp_intermediate_sync_group_diverged: Path, capsys,
    ):
        """Cleanly diverged intermediate sync group should be auto-resolved."""
        root = tmp_intermediate_sync_group_diverged

        # Make a change in workspace-a/libs/common (leaf)
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common")

        assert result == 0
        output = capsys.readouterr().out
        assert "auto-resolved" in output.lower()

    def test_force_bypasses_divergence_checks(
        self, tmp_intermediate_sync_group_diverged: Path, capsys,
    ):
        """--force should skip pre-cascade divergence resolution."""
        root = tmp_intermediate_sync_group_diverged

        # Make a change in workspace-a/libs/common
        leaf = root / "workspace-a" / "libs" / "common"
        _git(leaf, "checkout", "-b", "work")
        (leaf / "new.txt").write_text("new feature\n")
        _git(leaf, "add", "new.txt")
        _git(leaf, "commit", "-m", "Add new feature")

        with patch("grove.cascade.find_repo_root", return_value=root):
            result = run_cascade("workspace-a/libs/common", force=True)

        # Should complete (or fail) without auto-resolving divergence
        output = capsys.readouterr().out
        assert "auto-resolved" not in output.lower()
