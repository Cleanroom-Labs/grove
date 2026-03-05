"""Tests for native `grove worktree list`."""

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.worktree_list import list_worktrees


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


class TestListWorktrees:
    def test_json_lists_current_and_secondary_worktrees(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """JSON output should include the current worktree and sibling worktrees."""
        wt_path = tmp_submodule_tree.parent / "list-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "list-branch", str(wt_path))

        args = argparse.Namespace(
            format="json",
            branches=False,
            remotes=False,
            full=False,
        )

        with patch(
            "grove.worktree_list.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = list_worktrees(args)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        rows = payload["worktrees"]

        root_row = next(row for row in rows if row["path"] == str(tmp_submodule_tree))
        secondary_row = next(row for row in rows if row["path"] == str(wt_path))

        assert root_row["is_current"] is True
        assert root_row["is_main"] is True
        assert secondary_row["branch"] == "list-branch"
        assert secondary_row["kind"] == "worktree"

    def test_branches_flag_includes_unchecked_out_local_branches(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """`--branches` should include local branches without worktrees."""
        _git(tmp_submodule_tree, "branch", "available-branch")

        args = argparse.Namespace(
            format="json",
            branches=True,
            remotes=False,
            full=False,
        )

        with patch(
            "grove.worktree_list.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = list_worktrees(args)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        branch_row = next(
            row
            for row in payload["worktrees"]
            if row["kind"] == "branch" and row["branch"] == "available-branch"
        )
        assert branch_row["path"] is None
        assert branch_row["is_current"] is False

    def test_config_defaults_apply_when_flags_are_omitted(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """List config should apply when CLI flags are not provided."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config + "\n[list]\nbranches = true\nfull = true\n"
        )
        _git(tmp_submodule_tree, "branch", "config-branch")

        args = argparse.Namespace(
            format=None,
            branches=None,
            remotes=None,
            full=None,
        )

        with patch(
            "grove.worktree_list.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = list_worktrees(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "config-branch" in output
        assert "HEAD" in output
        assert "Upstream" in output

    def test_remotes_flag_includes_remote_branches(
        self,
        tmp_git_repo: Path,
        tmp_path: Path,
        capsys,
    ):
        """`--remotes` should include remote-tracking branches."""
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", str(origin))

        current_branch = _git(tmp_git_repo, "branch", "--show-current").stdout.strip()
        _git(tmp_git_repo, "remote", "add", "origin", str(origin))
        _git(tmp_git_repo, "push", "-u", "origin", current_branch)

        _git(tmp_git_repo, "checkout", "-b", "topic")
        (tmp_git_repo / "topic.txt").write_text("topic\n")
        _git(tmp_git_repo, "add", "topic.txt")
        _git(tmp_git_repo, "commit", "-m", "Add topic branch")
        _git(tmp_git_repo, "push", "-u", "origin", "topic")
        _git(tmp_git_repo, "checkout", current_branch)
        _git(tmp_git_repo, "branch", "-D", "topic")

        args = argparse.Namespace(
            format="json",
            branches=False,
            remotes=True,
            full=False,
        )

        with patch("grove.worktree_list.find_repo_root", return_value=tmp_git_repo):
            result = list_worktrees(args)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        remote_row = next(
            row
            for row in payload["worktrees"]
            if row["kind"] == "remote" and row["branch"] == "origin/topic"
        )
        assert remote_row["path"] is None
