"""Tests for grove.worktree_merge."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from grove.config import CONFIG_FILENAME
from grove.repo_utils import RepoInfo, discover_repos, run_git
from grove.worktree_merge import (
    MergeState,
    RepoMergeEntry,
    _count_divergent_commits,
    _get_journal_path,
    _get_state_path,
    _get_test_command,
    _has_branch,
    _is_ancestor,
    _log,
    _predict_conflicts,
    abort_merge,
    continue_merge,
    start_merge,
    status_merge,
)
from grove.config import MergeConfig


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=True,
    )


# ---------------------------------------------------------------------------
# Unit tests: git helpers
# ---------------------------------------------------------------------------

class TestHasBranch:
    def test_existing_branch(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(path=tmp_submodule_tree_with_branches,
                        repo_root=tmp_submodule_tree_with_branches)
        assert _has_branch(repo, "my-feature") is True

    def test_nonexistent_branch(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(path=tmp_submodule_tree_with_branches,
                        repo_root=tmp_submodule_tree_with_branches)
        assert _has_branch(repo, "nonexistent") is False


class TestIsAncestor:
    def test_already_merged(self, tmp_git_repo: Path):
        """If branch == HEAD, it should be an ancestor."""
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        # HEAD is an ancestor of itself
        assert _is_ancestor(repo, "HEAD") is True

    def test_not_merged(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(path=tmp_submodule_tree_with_branches,
                        repo_root=tmp_submodule_tree_with_branches)
        assert _is_ancestor(repo, "my-feature") is False


class TestCountDivergentCommits:
    def test_divergent(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(path=tmp_submodule_tree_with_branches,
                        repo_root=tmp_submodule_tree_with_branches)
        ahead, behind = _count_divergent_commits(repo, "my-feature")
        # main is 0 ahead and 1 behind the feature branch
        assert ahead == 0
        assert behind == 1


# ---------------------------------------------------------------------------
# Unit tests: test command resolution
# ---------------------------------------------------------------------------

class TestGetTestCommand:
    def test_root_override(self, tmp_git_repo: Path):
        config = MergeConfig(
            test_command="pytest",
            test_overrides={".": "npm test"},
        )
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert _get_test_command(config, repo) == "npm test"

    def test_override_for_submodule(self, tmp_submodule_tree: Path):
        config = MergeConfig(
            test_command="pytest",
            test_overrides={"technical-docs": "make html"},
        )
        child = tmp_submodule_tree / "technical-docs"
        repo = RepoInfo(path=child, repo_root=tmp_submodule_tree)
        assert _get_test_command(config, repo) == "make html"

    def test_empty_override_means_skip(self, tmp_submodule_tree: Path):
        config = MergeConfig(
            test_command="pytest",
            test_overrides={"technical-docs": ""},
        )
        child = tmp_submodule_tree / "technical-docs"
        repo = RepoInfo(path=child, repo_root=tmp_submodule_tree)
        assert _get_test_command(config, repo) is None

    def test_local_config_fallback(self, tmp_submodule_tree: Path):
        """Repo's own .grove.toml should be used if no override."""
        config = MergeConfig(test_command=None)
        child = tmp_submodule_tree / "technical-docs"
        # Write a local config
        (child / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "make html"\n'
        )
        repo = RepoInfo(path=child, repo_root=tmp_submodule_tree)
        assert _get_test_command(config, repo) == "make html"

    def test_root_default_fallback(self, tmp_git_repo: Path):
        config = MergeConfig(test_command="pytest")
        # A submodule without override or local config
        sub = tmp_git_repo / "sub"
        sub.mkdir()
        repo = RepoInfo(path=sub, repo_root=tmp_git_repo)
        assert _get_test_command(config, repo) == "pytest"

    def test_no_test_command(self, tmp_git_repo: Path):
        config = MergeConfig()
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        assert _get_test_command(config, repo) is None


# ---------------------------------------------------------------------------
# Unit tests: conflict prediction
# ---------------------------------------------------------------------------

class TestPredictConflicts:
    def test_clean_merge(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(path=tmp_submodule_tree_with_branches,
                        repo_root=tmp_submodule_tree_with_branches)
        clean, conflicts = _predict_conflicts(repo, "my-feature")
        assert clean is True
        assert conflicts == []

    def test_conflicting_merge(self, tmp_git_repo: Path):
        """Create a conflict scenario and verify prediction."""
        # Create a branch with conflicting changes
        _git(tmp_git_repo, "checkout", "-b", "conflict-branch")
        (tmp_git_repo / "README.md").write_text("conflict branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "conflict branch commit")
        _git(tmp_git_repo, "checkout", "-")

        # Create conflicting change on main
        (tmp_git_repo / "README.md").write_text("main branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "main conflicting commit")

        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        clean, conflicts = _predict_conflicts(repo, "conflict-branch")
        assert clean is False
        assert "README.md" in conflicts


# ---------------------------------------------------------------------------
# Unit tests: state management
# ---------------------------------------------------------------------------

class TestMergeState:
    def test_save_and_load(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state = MergeState(
            branch="my-feature",
            no_ff=True,
            no_test=False,
            started_at="2026-01-01T00:00:00",
            repos=[
                RepoMergeEntry(rel_path="sub", status="merged",
                               pre_merge_head="aaa", post_merge_head="bbb"),
                RepoMergeEntry(rel_path=".", status="pending"),
            ],
        )
        state.save(state_path)
        assert state_path.exists()

        loaded = MergeState.load(state_path)
        assert loaded.branch == "my-feature"
        assert loaded.no_ff is True
        assert len(loaded.repos) == 2
        assert loaded.repos[0].status == "merged"
        assert loaded.repos[0].pre_merge_head == "aaa"

    def test_remove(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state_path.write_text("{}")
        MergeState.remove(state_path)
        assert not state_path.exists()

    def test_remove_nonexistent(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        MergeState.remove(state_path)  # should not raise


# ---------------------------------------------------------------------------
# Unit tests: journal
# ---------------------------------------------------------------------------

class TestMergeJournal:
    def test_log_appends(self, tmp_path: Path):
        journal = tmp_path / "merge.log"
        _log(journal, "first")
        _log(journal, "second")
        content = journal.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]
        # Each line should have a timestamp
        assert lines[0].startswith("[")

    def test_journal_path_monthly_rotation(self, tmp_submodule_tree: Path):
        """Journal path should include the current year-month."""
        path = _get_journal_path(tmp_submodule_tree)
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        expected_suffix = f"merge-journal-{now.strftime('%Y-%m')}.log"
        assert path.name == expected_suffix


# ---------------------------------------------------------------------------
# Integration tests: start_merge
# ---------------------------------------------------------------------------

class TestStartMerge:
    def test_full_merge(self, tmp_submodule_tree_with_branches: Path):
        """Full bottom-up merge of a feature branch."""
        root = tmp_submodule_tree_with_branches
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)

        assert result == 0
        # State file should be cleaned up
        assert not _get_state_path(root).exists()

        # Feature file should exist in merged repos
        assert (root / "feature.txt").exists()
        assert (root / "technical-docs" / "feature.txt").exists()
        # technical-docs/common is a sync-group submodule, so it's excluded
        # from discovery (same as push.py behavior)

    def test_dry_run(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", dry_run=True)

        assert result == 0
        # State file should NOT be created
        assert not _get_state_path(root).exists()
        # Feature files should NOT exist (no actual merge)
        assert not (root / "feature.txt").exists()

    def test_no_recurse(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_recurse=True, no_test=True)

        assert result == 0
        # Only root should be merged
        assert (root / "feature.txt").exists()
        # Submodules should NOT be merged
        assert not (root / "technical-docs" / "feature.txt").exists()

    def test_already_up_to_date(self, tmp_submodule_tree: Path):
        """If branch doesn't exist, should report nothing to merge."""
        root = tmp_submodule_tree
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("nonexistent-branch", no_test=True)

        assert result == 0

    def test_uncommitted_changes_block(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        # Create uncommitted changes
        (root / "dirty.txt").write_text("dirty\n")
        _git(root, "add", "dirty.txt")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)

        assert result == 1

    def test_guard_blocks_double_start(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        # Create a fake state file
        state_path = _get_state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{}")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")

        assert result == 1
        # Clean up
        state_path.unlink()

    def test_state_file_lifecycle(self, tmp_submodule_tree_with_branches: Path):
        """State file should exist during merge and be cleaned up after."""
        root = tmp_submodule_tree_with_branches
        state_path = _get_state_path(root)

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)

        assert result == 0
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Integration tests: continue_merge
# ---------------------------------------------------------------------------

class TestContinueMerge:
    def test_continue_after_test_failure(self, tmp_submodule_tree_with_branches: Path):
        """After a test failure, --continue should re-run tests."""
        root = tmp_submodule_tree_with_branches

        # Write a config with a test that will fail and commit it
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")

        # Should be paused due to test failure
        assert result == 1
        state = MergeState.load(_get_state_path(root))
        paused = [e for e in state.repos if e.status == "paused"]
        assert len(paused) == 1
        assert paused[0].reason == "test-failed"

        # Fix: change test command to succeed (this is OK as uncommitted
        # since we're in --continue, not starting a new merge)
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "true"\n'
        )

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = continue_merge()

        assert result == 0
        assert not _get_state_path(root).exists()

    def test_continue_no_merge_in_progress(self, tmp_submodule_tree: Path):
        root = tmp_submodule_tree
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = continue_merge()
        assert result == 1


# ---------------------------------------------------------------------------
# Integration tests: abort_merge
# ---------------------------------------------------------------------------

class TestAbortMerge:
    def test_abort_restores_state(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches

        # Write and commit a failing test config
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        # Record pre-merge commits (after committing config)
        pre_commits = {}
        for repo_path in [root, root / "technical-docs", root / "technical-docs" / "common"]:
            result = _git(repo_path, "rev-parse", "HEAD")
            pre_commits[str(repo_path)] = result.stdout.strip()

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            start_merge("my-feature")

        # Abort
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = abort_merge()

        assert result == 0
        assert not _get_state_path(root).exists()

        # Verify repos are restored
        for repo_path, pre_sha in pre_commits.items():
            result = _git(Path(repo_path), "rev-parse", "HEAD")
            assert result.stdout.strip() == pre_sha

    def test_abort_no_merge_in_progress(self, tmp_submodule_tree: Path):
        root = tmp_submodule_tree
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = abort_merge()
        assert result == 1


# ---------------------------------------------------------------------------
# Integration tests: status_merge
# ---------------------------------------------------------------------------

class TestStatusMerge:
    def test_status_no_merge(self, tmp_submodule_tree: Path, capsys):
        root = tmp_submodule_tree
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = status_merge()
        assert result == 0
        assert "No merge in progress" in capsys.readouterr().out

    def test_status_during_merge(self, tmp_submodule_tree_with_branches: Path, capsys):
        root = tmp_submodule_tree_with_branches

        # Write and commit a failing test config
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            start_merge("my-feature")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = status_merge()

        assert result == 0
        output = capsys.readouterr().out
        assert "my-feature" in output
        assert "PAUSED" in output

        # Clean up
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            abort_merge()


# ---------------------------------------------------------------------------
# Integration tests: test command execution
# ---------------------------------------------------------------------------

class TestTestCommand:
    def test_test_pass_continues(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "true"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add passing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")
        assert result == 0

    def test_test_fail_pauses(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")
        assert result == 1
        assert _get_state_path(root).exists()

        # Clean up
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            abort_merge()

    def test_no_test_skips(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        # Even with a failing test command, --no-test should succeed
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)
        assert result == 0


# ---------------------------------------------------------------------------
# Integration tests: merge journal
# ---------------------------------------------------------------------------

class TestMergeJournalIntegration:
    def test_journal_entries_logged(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            start_merge("my-feature", no_test=True)

        # Find journal file
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        git_dir = Path(
            _git(root, "rev-parse", "--git-common-dir").stdout.strip()
        )
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()
        journal = git_dir / "grove" / f"merge-journal-{now.strftime('%Y-%m')}.log"
        assert journal.exists()

        content = journal.read_text()
        assert "MERGE START" in content
        assert "MERGE COMPLETE" in content


# ---------------------------------------------------------------------------
# Integration tests: structural consistency
# ---------------------------------------------------------------------------

class TestStructuralCheck:
    def test_no_warning_when_same_structure(
        self, tmp_submodule_tree_with_branches: Path, capsys
    ):
        """No structural warnings when topology matches."""
        root = tmp_submodule_tree_with_branches
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            start_merge("my-feature", no_test=True)

        output = capsys.readouterr().out
        assert "Warning: submodule structure differs" not in output
