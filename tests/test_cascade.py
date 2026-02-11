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
    _determine_tiers,
    _discover_cascade_chain,
    _get_state_path,
    abort_cascade,
    continue_cascade,
    run_cascade,
    show_cascade_status,
)
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
