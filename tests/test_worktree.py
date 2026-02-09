"""Tests for grove.worktree."""

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.repo_utils import parse_gitmodules
from grove.worktree import (
    _copy_local_config,
    _init_submodules,
    add_worktree,
    remove_worktree,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=True,
    )


# ---------------------------------------------------------------------------
# parse_gitmodules (shared from repo_utils, used by worktree)
# ---------------------------------------------------------------------------

class TestParseGitmodulesAll:
    def test_missing_file(self, tmp_path: Path):
        """Nonexistent .gitmodules should return empty list."""
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert result == []

    def test_empty_file(self, tmp_path: Path):
        """Empty .gitmodules should return empty list."""
        (tmp_path / ".gitmodules").write_text("")
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert result == []

    def test_single_submodule(self, tmp_path: Path):
        (tmp_path / ".gitmodules").write_text(
            '[submodule "technical-docs"]\n'
            "    path = technical-docs\n"
            "    url = /path/to/technical-docs\n"
        )
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert len(result) == 1
        name, path, url = result[0]
        assert name == "technical-docs"
        assert path == "technical-docs"
        assert url == "/path/to/technical-docs"

    def test_multiple_submodules(self, tmp_path: Path):
        """Should return ALL submodules when no url_match is given."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "technical-docs"]\n'
            "    path = technical-docs\n"
            "    url = /path/to/technical-docs\n"
            '[submodule "common"]\n'
            "    path = source/common\n"
            "    url = /path/to/common\n"
        )
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert len(result) == 2
        names = [r[0] for r in result]
        assert "technical-docs" in names
        assert "common" in names


# ---------------------------------------------------------------------------
# _init_submodules
# ---------------------------------------------------------------------------

class TestInitSubmodules:
    def test_no_gitmodules_returns_true(self, tmp_path: Path):
        """Directory without .gitmodules should succeed immediately."""
        worktree = tmp_path / "wt"
        worktree.mkdir()
        assert _init_submodules(worktree, tmp_path) is True


# ---------------------------------------------------------------------------
# add_worktree (integration)
# ---------------------------------------------------------------------------

class TestAddWorktree:
    def test_creates_worktree_directory(self, tmp_submodule_tree: Path):
        """Worktree directory should exist after add."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="test-branch", path=str(wt_path), checkout=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        assert wt_path.exists()
        assert (wt_path / ".git").exists()

    def test_initializes_nested_submodules(self, tmp_submodule_tree: Path):
        """Nested submodules should be checked out in the new worktree."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="test-branch", path=str(wt_path), checkout=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        # Level 2: technical-docs submodule
        assert (wt_path / "technical-docs" / ".git").exists()
        # Level 3: common submodule inside technical-docs
        assert (wt_path / "technical-docs" / "common" / ".git").exists()
        # Verify actual content from the grandchild repo
        assert (wt_path / "technical-docs" / "common" / "theme.txt").exists()

    def test_checkout_existing_branch(self, tmp_submodule_tree: Path):
        """--checkout should use an existing branch without creating a new one."""
        _git(tmp_submodule_tree, "branch", "existing-branch")

        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="existing-branch", path=str(wt_path), checkout=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        assert wt_path.exists()

    def test_path_already_exists_returns_1(self, tmp_submodule_tree: Path):
        """Should fail if the target path already exists."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        wt_path.mkdir()  # pre-create

        args = argparse.Namespace(branch="test-branch", path=str(wt_path), checkout=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 1


# ---------------------------------------------------------------------------
# remove_worktree (integration)
# ---------------------------------------------------------------------------

class TestRemoveWorktree:
    def test_removes_worktree(self, tmp_submodule_tree: Path):
        """Worktree directory should be gone after remove."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "rm-branch", str(wt_path))
        assert wt_path.exists()

        args = argparse.Namespace(path=str(wt_path), force=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = remove_worktree(args)

        assert result == 0
        assert not wt_path.exists()

    def test_force_removes_dirty_worktree(self, tmp_submodule_tree: Path):
        """--force should remove a worktree with uncommitted changes."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "dirty-branch", str(wt_path))
        (wt_path / "dirty.txt").write_text("uncommitted\n")

        args = argparse.Namespace(path=str(wt_path), force=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = remove_worktree(args)

        assert result == 0
        assert not wt_path.exists()


# ---------------------------------------------------------------------------
# _copy_local_config
# ---------------------------------------------------------------------------

class TestCopyLocalConfig:
    def test_copies_config_between_repos(self, tmp_git_repo: Path, tmp_path: Path):
        """_copy_local_config should copy non-structural keys between repos."""
        target = tmp_path / "target"
        target.mkdir()
        _git(target, "init")

        _git(tmp_git_repo, "config", "--local", "user.signingkey", "ABCD1234")
        _copy_local_config(tmp_git_repo, target)

        out = _git(target, "config", "--local", "user.signingkey")
        assert out.stdout.strip() == "ABCD1234"

    def test_copies_submodule_config(self, tmp_submodule_tree: Path):
        """Custom config in a submodule should be copied to the worktree's submodule."""
        sub = tmp_submodule_tree / "technical-docs"
        _git(sub, "config", "--local", "user.signingkey", "SUB_KEY_99")

        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(
            branch="subcfg-branch", path=str(wt_path), checkout=False, no_copy_config=False,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        wt_sub = wt_path / "technical-docs"
        out = _git(wt_sub, "config", "--local", "user.signingkey")
        assert out.stdout.strip() == "SUB_KEY_99"

    def test_no_copy_config_flag_skips(self, tmp_submodule_tree: Path):
        """--no-copy-config should prevent submodule config from being copied."""
        sub = tmp_submodule_tree / "technical-docs"
        _git(sub, "config", "--local", "user.signingkey", "SHOULD_NOT_COPY")

        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(
            branch="nocopy-branch", path=str(wt_path), checkout=False, no_copy_config=True,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        wt_sub = wt_path / "technical-docs"
        # Key should not exist in the worktree's submodule
        out = subprocess.run(
            ["git", "-C", str(wt_sub), "config", "--local", "--get", "user.signingkey"],
            capture_output=True, text=True,
        )
        assert out.returncode != 0

    def test_excludes_structural_keys(self, tmp_git_repo: Path, tmp_path: Path):
        """Structural keys (remote.*, core.*, etc.) should not be copied."""
        target = tmp_path / "target"
        target.mkdir()
        _git(target, "init")

        _git(tmp_git_repo, "config", "--local", "remote.origin.pushurl", "git@example.com:test.git")
        _git(tmp_git_repo, "config", "--local", "user.signingkey", "GOOD_KEY")

        _copy_local_config(tmp_git_repo, target)

        # Structural key should NOT be copied
        out = subprocess.run(
            ["git", "-C", str(target), "config", "--local", "--get", "remote.origin.pushurl"],
            capture_output=True, text=True,
        )
        assert out.returncode != 0

        # Non-structural key should be copied
        out = _git(target, "config", "--local", "user.signingkey")
        assert out.stdout.strip() == "GOOD_KEY"
