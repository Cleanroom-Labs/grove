"""Tests for grove.worktree_merge."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from grove.config import CONFIG_FILENAME
from grove.repo_utils import RepoInfo
from grove.worktree_merge import (
    MergeState,
    RepoMergeEntry,
    _get_journal_path,
    _get_state_path,
    _get_test_command,
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
        capture_output=True,
        text=True,
        check=True,
    )


# ---------------------------------------------------------------------------
# Unit tests: git helpers
# ---------------------------------------------------------------------------


class TestHasBranch:
    def test_existing_branch(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        assert repo.has_local_branch("my-feature") is True

    def test_nonexistent_branch(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        assert repo.has_local_branch("nonexistent") is False


class TestIsAncestor:
    def test_already_merged(self, tmp_git_repo: Path):
        """If branch == HEAD, it should be an ancestor."""
        repo = RepoInfo(path=tmp_git_repo, repo_root=tmp_git_repo)
        # HEAD is an ancestor of itself
        assert repo.is_ancestor("HEAD") is True

    def test_not_merged(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        assert repo.is_ancestor("my-feature") is False


class TestCountDivergentCommits:
    def test_divergent(self, tmp_submodule_tree_with_branches: Path):
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
        ahead, behind = repo.count_divergent_commits("my-feature")
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
            '[worktree-merge]\ntest-command = "make html"\n'
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
        repo = RepoInfo(
            path=tmp_submodule_tree_with_branches,
            repo_root=tmp_submodule_tree_with_branches,
        )
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


class TestMergeStateCorruption:
    """Edge cases: corrupt or unexpected state files."""

    def test_load_truncated_json(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state_path.write_text('{"branch": "feat", "repos": [')
        with pytest.raises(json.JSONDecodeError):
            MergeState.load(state_path)

    def test_load_missing_repos_key(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat",
                    "no_ff": False,
                    "no_test": False,
                    "started_at": "2026-01-01T00:00:00",
                }
            )
        )
        with pytest.raises(KeyError):
            MergeState.load(state_path)

    def test_load_empty_file(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state_path.write_text("")
        with pytest.raises(json.JSONDecodeError):
            MergeState.load(state_path)

    def test_extra_fields_in_repos_raises(self, tmp_path: Path):
        """Extra keys in repo entries cause TypeError (forward-compat gap)."""
        state_path = tmp_path / "merge.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat",
                    "no_ff": False,
                    "no_test": False,
                    "started_at": "2026-01-01T00:00:00",
                    "repos": [
                        {
                            "rel_path": ".",
                            "status": "pending",
                            "pre_merge_head": None,
                            "post_merge_head": None,
                            "reason": None,
                            "extra_key": "ignored",
                        }
                    ],
                }
            )
        )
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            MergeState.load(state_path)

    def test_extra_top_level_fields_ignored(self, tmp_path: Path):
        """Extra top-level keys in the JSON should not break loading."""
        state_path = tmp_path / "merge.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat",
                    "no_ff": False,
                    "no_test": False,
                    "started_at": "2026-01-01T00:00:00",
                    "repos": [
                        {
                            "rel_path": ".",
                            "status": "pending",
                            "pre_merge_head": None,
                            "post_merge_head": None,
                            "reason": None,
                        }
                    ],
                    "future_field": True,
                }
            )
        )
        state = MergeState.load(state_path)
        assert state.branch == "feat"
        assert len(state.repos) == 1


class TestMergeState:
    def test_save_and_load(self, tmp_path: Path):
        state_path = tmp_path / "merge.json"
        state = MergeState(
            branch="my-feature",
            no_ff=True,
            no_test=False,
            started_at="2026-01-01T00:00:00",
            repos=[
                RepoMergeEntry(
                    rel_path="sub",
                    status="merged",
                    pre_merge_head="aaa",
                    post_merge_head="bbb",
                ),
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
            '[worktree-merge]\ntest-command = "false"\n'
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
        (root / CONFIG_FILENAME).write_text('[worktree-merge]\ntest-command = "true"\n')

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
            '[worktree-merge]\ntest-command = "false"\n'
        )
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add failing test config")

        # Record pre-merge commits (after committing config)
        pre_commits = {}
        for repo_path in [
            root,
            root / "technical-docs",
            root / "technical-docs" / "common",
        ]:
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
            '[worktree-merge]\ntest-command = "false"\n'
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
        (root / CONFIG_FILENAME).write_text('[worktree-merge]\ntest-command = "true"\n')
        _git(root, "add", CONFIG_FILENAME)
        _git(root, "commit", "-m", "add passing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")
        assert result == 0

    def test_test_fail_pauses(self, tmp_submodule_tree_with_branches: Path):
        root = tmp_submodule_tree_with_branches
        (root / CONFIG_FILENAME).write_text(
            '[worktree-merge]\ntest-command = "false"\n'
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
            '[worktree-merge]\ntest-command = "false"\n'
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
        git_dir = Path(_git(root, "rev-parse", "--git-common-dir").stdout.strip())
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


# ---------------------------------------------------------------------------
# Integration tests: continue with conflict resolution
# ---------------------------------------------------------------------------


class TestContinueConflictResolution:
    def test_continue_after_conflict_resolution(self, tmp_git_repo: Path):
        """Create a conflict, resolve it, then --continue should succeed."""
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

        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            result = start_merge("conflict-branch", no_test=True)

        assert result == 1
        state = MergeState.load(_get_state_path(tmp_git_repo))
        paused = [e for e in state.repos if e.status == "paused"]
        assert len(paused) == 1
        assert paused[0].reason == "conflict"

        # Resolve the conflict
        (tmp_git_repo / "README.md").write_text("resolved content\n")
        _git(tmp_git_repo, "add", "README.md")

        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            result = continue_merge()

        assert result == 0
        assert not _get_state_path(tmp_git_repo).exists()

    def test_continue_with_unresolved_conflicts(self, tmp_git_repo: Path, capsys):
        """Continuing with unresolved conflicts should return 1."""
        _git(tmp_git_repo, "checkout", "-b", "conflict-branch")
        (tmp_git_repo / "README.md").write_text("conflict branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "conflict branch commit")
        _git(tmp_git_repo, "checkout", "-")

        (tmp_git_repo / "README.md").write_text("main branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "main conflicting commit")

        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            start_merge("conflict-branch", no_test=True)

        # Don't resolve — just try to continue
        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            result = continue_merge()

        assert result == 1
        output = capsys.readouterr().out
        assert "Unresolved" in output or "conflict" in output.lower()

        # Clean up
        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            abort_merge()


class TestAbortConflict:
    def test_abort_during_conflict(self, tmp_git_repo: Path):
        """Aborting during a conflict should restore the repo."""
        _git(tmp_git_repo, "checkout", "-b", "conflict-branch")
        (tmp_git_repo / "README.md").write_text("conflict branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "conflict branch commit")
        _git(tmp_git_repo, "checkout", "-")

        (tmp_git_repo / "README.md").write_text("main branch content\n")
        _git(tmp_git_repo, "add", "README.md")
        _git(tmp_git_repo, "commit", "-m", "main conflicting commit")

        main_sha = _git(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            start_merge("conflict-branch", no_test=True)

        with patch("grove.worktree_merge.find_repo_root", return_value=tmp_git_repo):
            result = abort_merge()

        assert result == 0
        assert not _get_state_path(tmp_git_repo).exists()

        # HEAD should be back to the pre-merge main commit
        current_sha = _git(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
        assert current_sha == main_sha

    def test_abort_after_partial_merge(self, tmp_submodule_tree_with_branches: Path):
        """When first repo merges but second pauses, abort should roll both back."""
        root = tmp_submodule_tree_with_branches

        # Make the root repo conflict to pause there, while submodules merge clean
        _git(root, "checkout", "my-feature")
        (root / "README.md").write_text("feature-branch README\n")
        _git(root, "add", "README.md")
        _git(root, "commit", "-m", "feature conflict on root README")
        _git(root, "checkout", "-")

        (root / "README.md").write_text("main-branch README\n")
        _git(root, "add", "README.md")
        _git(root, "commit", "-m", "main conflict on root README")

        # Record pre-merge SHAs for child
        child = root / "technical-docs"
        child_pre = _git(child, "rev-parse", "HEAD").stdout.strip()

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)
        assert result == 1  # paused on conflict

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            abort_merge()

        # Child should be restored
        child_post = _git(child, "rev-parse", "HEAD").stdout.strip()
        assert child_post == child_pre


# ---------------------------------------------------------------------------
# Integration tests: sync-aware merge
# ---------------------------------------------------------------------------


class TestSyncGroupMerge:
    def test_sync_group_merge_and_propagation(
        self, tmp_submodule_tree_with_sync_branches: Path
    ):
        """After merge, sync-group submodule is merged and parent auto-resolves."""
        root = tmp_submodule_tree_with_sync_branches
        common = root / "technical-docs" / "common"

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)

        assert result == 0
        assert not _get_state_path(root).exists()

        # Common should have the feature file (merged)
        assert (common / "feature.txt").exists()

        # Technical-docs and parent should have their feature files
        assert (root / "technical-docs" / "feature.txt").exists()
        assert (root / "feature.txt").exists()

    def test_sync_group_dry_run(
        self, tmp_submodule_tree_with_sync_branches: Path, capsys
    ):
        """Dry run should report sync-group predictions without executing."""
        root = tmp_submodule_tree_with_sync_branches

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", dry_run=True)

        assert result == 0
        assert not _get_state_path(root).exists()

        output = capsys.readouterr().out
        assert "sync group" in output.lower() or "Sync-group" in output

        # No actual merges should have happened
        common = root / "technical-docs" / "common"
        assert not (common / "feature.txt").exists()

    def test_sync_group_no_feature_branch(self, tmp_submodule_tree: Path, capsys):
        """When sync-group has no feature branch, skip gracefully."""
        root = tmp_submodule_tree

        # Put submodules on named branches but don't create feature branches
        for sub in [root / "technical-docs" / "common", root / "technical-docs"]:
            result = subprocess.run(
                ["git", "-C", str(sub), "checkout", "main"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                _git(sub, "checkout", "-b", "main")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("nonexistent-branch", no_test=True)

        # Should succeed (nothing to merge)
        assert result == 0

    def test_sync_group_abort_undoes_sync(
        self, tmp_submodule_tree_with_sync_branches: Path
    ):
        """Abort should restore all synced instances to pre-merge state."""
        root = tmp_submodule_tree_with_sync_branches
        common = root / "technical-docs" / "common"
        child = root / "technical-docs"

        # Record pre-merge SHAs
        pre_common_sha = _git(common, "rev-parse", "HEAD").stdout.strip()
        pre_child_sha = _git(child, "rev-parse", "HEAD").stdout.strip()
        _git(root, "rev-parse", "HEAD").stdout.strip()

        # Make root conflict so merge pauses at root
        _git(root, "checkout", "my-feature")
        (root / "README.md").write_text("feature-branch README\n")
        _git(root, "add", "README.md")
        _git(root, "commit", "-m", "feature conflict on root README")
        _git(root, "checkout", "-")

        (root / "README.md").write_text("main-branch README\n")
        _git(root, "add", "README.md")
        _git(root, "commit", "-m", "main conflict on root README")

        # Re-record root SHA after the conflict setup commit
        _git(root, "rev-parse", "HEAD").stdout.strip()

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)
        # Should pause on root conflict (sync + child merge succeeded)
        assert result == 1

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            abort_result = abort_merge()
        assert abort_result == 0

        # All repos should be restored to their pre-merge state
        assert _git(common, "rev-parse", "HEAD").stdout.strip() == pre_common_sha
        assert _git(child, "rev-parse", "HEAD").stdout.strip() == pre_child_sha

    def test_sync_group_auto_resolve_in_parent(
        self, tmp_submodule_tree_with_sync_branches: Path
    ):
        """Parent merge should auto-resolve sync-group pointer conflicts."""
        root = tmp_submodule_tree_with_sync_branches
        common = root / "technical-docs" / "common"

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature", no_test=True)

        assert result == 0

        # Verify common is on a consistent commit (not conflicted)
        # The common submodule should be at the merged SHA
        common_sha = _git(common, "rev-parse", "HEAD").stdout.strip()
        assert common_sha  # Should have a valid SHA
        assert (common / "feature.txt").exists()  # Feature was merged

    def test_state_includes_pre_sync_heads(
        self, tmp_submodule_tree_with_sync_branches: Path
    ):
        """MergeState should record pre_sync_heads when sync groups are involved."""
        root = tmp_submodule_tree_with_sync_branches

        # Use a failing test command to pause the merge after sync
        (root / ".grove.toml").read_text()  # Verify it exists
        (root / ".grove.toml").write_text(
            "[sync-groups.common]\n"
            'url-match = "grandchild_origin"\n'
            "\n"
            "[worktree-merge]\n"
            'test-command = "false"\n'
        )
        _git(root, "add", ".grove.toml")
        _git(root, "commit", "-m", "add failing test config")

        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            result = start_merge("my-feature")

        # Should be paused due to test failure (in sync-group or normal merge)
        assert result == 1
        state = MergeState.load(_get_state_path(root))

        # pre_sync_heads should be populated
        assert isinstance(state.pre_sync_heads, dict)

        # Clean up
        with patch("grove.worktree_merge.find_repo_root", return_value=root):
            abort_merge()


class TestMergeStateBackwardCompat:
    def test_load_without_pre_sync_heads(self, tmp_path: Path):
        """Old state files without pre_sync_heads should load with empty dict."""
        state_path = tmp_path / "merge.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat",
                    "no_ff": False,
                    "no_test": False,
                    "started_at": "2026-01-01T00:00:00",
                    "repos": [
                        {
                            "rel_path": ".",
                            "status": "pending",
                            "pre_merge_head": None,
                            "post_merge_head": None,
                            "reason": None,
                        }
                    ],
                }
            )
        )
        state = MergeState.load(state_path)
        assert state.pre_sync_heads == {}

    def test_load_with_pre_sync_heads(self, tmp_path: Path):
        """State files with pre_sync_heads should load correctly."""
        state_path = tmp_path / "merge.json"
        state_path.write_text(
            json.dumps(
                {
                    "branch": "feat",
                    "no_ff": False,
                    "no_test": False,
                    "started_at": "2026-01-01T00:00:00",
                    "repos": [
                        {
                            "rel_path": "common",
                            "status": "merged",
                            "pre_merge_head": "aaa",
                            "post_merge_head": "bbb",
                            "reason": None,
                            "sync_group": "common",
                        }
                    ],
                    "pre_sync_heads": {"technical-docs": "ccc"},
                }
            )
        )
        state = MergeState.load(state_path)
        assert state.pre_sync_heads == {"technical-docs": "ccc"}
        assert state.repos[0].sync_group == "common"

    def test_save_includes_pre_sync_heads(self, tmp_path: Path):
        """Saved state should include pre_sync_heads."""
        state_path = tmp_path / "merge.json"
        state = MergeState(
            branch="feat",
            no_ff=False,
            no_test=False,
            started_at="2026-01-01T00:00:00",
            repos=[RepoMergeEntry(rel_path=".", sync_group="common")],
            pre_sync_heads={"technical-docs": "abc123"},
        )
        state.save(state_path)

        data = json.loads(state_path.read_text())
        assert data["pre_sync_heads"] == {"technical-docs": "abc123"}
        assert data["repos"][0]["sync_group"] == "common"
