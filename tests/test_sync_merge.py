"""Tests for grove.sync_merge."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.sync import discover_sync_submodules
from grove.sync_merge import (
    SyncMergeState,
    _get_state_path,
    abort_sync_merge,
    attempt_divergence_merge,
    show_sync_merge_status,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside *cwd*."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


class TestSyncMergeState:
    """State persistence tests."""

    def test_save_and_load(self, tmp_path: Path):
        """State should round-trip through save/load."""
        state_path = tmp_path / "sync-merge-state.json"
        state = SyncMergeState(
            group_name="common",
            started_at="2026-01-15T12:00:00+00:00",
            workspace_path="/tmp/workspace",
            base_commit="abc1234",
            diverged_commits=[
                {"sha": "aaa", "source_path": "/path/a", "status": "diverged"},
                {"sha": "bbb", "source_path": "/path/b", "status": "diverged"},
            ],
        )
        state.save(state_path)
        loaded = SyncMergeState.load(state_path)

        assert loaded.group_name == "common"
        assert loaded.base_commit == "abc1234"
        assert len(loaded.diverged_commits) == 2
        assert loaded.merged_sha is None

    def test_remove(self, tmp_path: Path):
        """remove() should delete the state file."""
        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        SyncMergeState.remove(state_path)
        assert not state_path.exists()

    def test_remove_missing_ok(self, tmp_path: Path):
        """remove() should not error when file doesn't exist."""
        state_path = tmp_path / "does-not-exist.json"
        SyncMergeState.remove(state_path)


class TestAttemptDivergenceMerge:
    """Tests for attempt_divergence_merge()."""

    def test_clean_merge_returns_result(
        self,
        tmp_sync_group_diverged: Path,
        capsys,
    ):
        """Two diverged instances with non-conflicting changes should merge cleanly."""
        root = tmp_sync_group_diverged
        submodules = discover_sync_submodules(root, "common_origin")

        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = attempt_divergence_merge(
                "common",
                submodules,
                root,
                standalone_repo=None,
                dry_run=False,
                force=False,
            )

        assert result is not None
        merged_sha, workspace, desc = result
        assert len(merged_sha) == 40  # full SHA
        assert "merged" in desc.lower()
        output = capsys.readouterr().out
        assert "merge successful" in output.lower()

    def test_dry_run_returns_placeholder(
        self,
        tmp_sync_group_diverged: Path,
        capsys,
    ):
        """Dry run should return a placeholder result without merging."""
        root = tmp_sync_group_diverged
        submodules = discover_sync_submodules(root, "common_origin")

        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = attempt_divergence_merge(
                "common",
                submodules,
                root,
                standalone_repo=None,
                dry_run=True,
                force=False,
            )

        assert result is not None
        _, _, desc = result
        assert "dry-run" in desc.lower()

    def test_standalone_repo_preferred(
        self,
        tmp_sync_group_diverged: Path,
        capsys,
    ):
        """When a standalone repo is configured, it should be used as workspace."""
        root = tmp_sync_group_diverged
        submodules = discover_sync_submodules(root, "common_origin")

        # The tmp_sync_group_diverged fixture has common_origin in parent dir
        common_origin = root.parent / "common_origin"
        assert common_origin.exists()

        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = attempt_divergence_merge(
                "common",
                submodules,
                root,
                standalone_repo=common_origin,
                dry_run=False,
                force=False,
            )

        assert result is not None
        _, workspace, _ = result
        assert workspace == common_origin

    def test_already_in_progress(
        self,
        tmp_sync_group_diverged: Path,
        capsys,
    ):
        """Should fail if a sync merge is already in progress."""
        root = tmp_sync_group_diverged
        submodules = discover_sync_submodules(root, "common_origin")

        # Create a fake state file
        state_path = _get_state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"dummy": true}')

        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = attempt_divergence_merge(
                "common",
                submodules,
                root,
                standalone_repo=None,
                dry_run=False,
                force=False,
            )

        assert result is None
        output = capsys.readouterr().out
        assert "already in progress" in output.lower()
        state_path.unlink()


class TestAbortSyncMerge:
    """Tests for abort_sync_merge()."""

    def test_abort_when_no_merge(self, tmp_sync_group_diverged: Path, capsys):
        """Aborting with no merge in progress should fail."""
        root = tmp_sync_group_diverged
        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = abort_sync_merge()
        assert result == 1
        output = capsys.readouterr().out
        assert "no sync merge" in output.lower()


class TestShowSyncMergeStatus:
    """Tests for show_sync_merge_status()."""

    def test_status_no_merge(self, tmp_sync_group_diverged: Path, capsys):
        """Status with no merge should report cleanly."""
        root = tmp_sync_group_diverged
        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = show_sync_merge_status()
        assert result == 0
        output = capsys.readouterr().out
        assert "no sync merge" in output.lower()

    def test_status_during_merge(self, tmp_sync_group_diverged: Path, capsys):
        """Status during a merge should show details."""
        root = tmp_sync_group_diverged
        state_path = _get_state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        state = SyncMergeState(
            group_name="common",
            started_at="2026-01-15T12:00:00+00:00",
            workspace_path=str(root / "frontend" / "libs" / "common"),
            base_commit="abc1234567890123456789012345678901234567",
            diverged_commits=[
                {
                    "sha": "aaa1234567890123456789012345678901234567",
                    "source_path": str(root / "frontend" / "libs" / "common"),
                    "status": "diverged",
                },
                {
                    "sha": "bbb1234567890123456789012345678901234567",
                    "source_path": str(root / "backend" / "libs" / "common"),
                    "status": "diverged",
                },
            ],
        )
        state.save(state_path)

        with patch("grove.sync_merge.find_repo_root", return_value=root):
            result = show_sync_merge_status()

        assert result == 0
        output = capsys.readouterr().out
        assert "common" in output
        assert "conflicts" in output.lower() or "merge in progress" in output.lower()

        state_path.unlink()
