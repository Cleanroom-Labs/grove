"""Tests for grove.cli argument parsing."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from grove.cli import main


class TestCliNoArgs:
    def test_no_args_returns_2(self, capsys):
        """Calling main() with no arguments should print usage and return 2."""
        result = main([])
        assert result == 2
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "grove" in captured.out.lower()


class TestCliCheckSubcommand:
    def test_parse_check_verbose(self):
        """'check -v' should set command='check' and verbose=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run):
            result = main(["check", "-v"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "check"
        assert args.verbose is True

    def test_parse_check_no_flags(self):
        """'check' alone should set verbose=False."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run):
            result = main(["check"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "check"
        assert args.verbose is False


class TestCliPushSubcommand:
    def test_parse_push_dry_run_force(self):
        """'push --dry-run --force' should set both flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            result = main(["push", "--dry-run", "--force"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "push"
        assert args.dry_run is True
        assert args.force is True

    def test_parse_push_defaults(self):
        """'push' with no flags should have dry_run=False, force=False."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            main(["push"])

        args = mock_run.call_args[0][0]
        assert args.dry_run is False
        assert args.force is False


class TestCliSyncSubcommand:
    def test_parse_sync_full(self):
        """'sync common abc1234 --dry-run --no-push --force' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "common", "abc1234", "--dry-run", "--no-push", "--force"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "sync"
        assert args.group == "common"
        assert args.commit == "abc1234"
        assert args.dry_run is True
        assert args.no_push is True
        assert args.force is True

    def test_parse_sync_defaults(self):
        """'sync' with no arguments should have sensible defaults."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync"])

        args = mock_run.call_args[0][0]
        assert args.group is None
        assert args.commit is None
        assert args.dry_run is False
        assert args.no_push is False
        assert args.force is False

    def test_parse_sync_group_only(self):
        """'sync common' should set group='common' and commit=None."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "common"])

        args = mock_run.call_args[0][0]
        assert args.group == "common"
        assert args.commit is None

    def test_parse_sync_group_and_commit(self):
        """'sync common abc1234' should set both group and commit."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "common", "abc1234"])

        args = mock_run.call_args[0][0]
        assert args.group == "common"
        assert args.commit == "abc1234"


class TestCliVisualizeSubcommand:
    def test_parse_visualize_with_path(self):
        """'visualize /tmp/foo' should set path='/tmp/foo'."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.visualizer.__main__.run", mock_run):
            main(["visualize", "/tmp/foo"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "visualize"
        assert args.path == "/tmp/foo"

    def test_parse_visualize_default_path(self):
        """'visualize' with no path should default to '.'."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.visualizer.__main__.run", mock_run):
            main(["visualize"])

        args = mock_run.call_args[0][0]
        assert args.path == "."


class TestCliNoColor:
    def test_no_color_flag(self):
        """'--no-color check' should disable colors and still dispatch."""
        from grove.repo_utils import Colors

        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run):
            main(["--no-color", "check"])

        assert Colors._enabled is False
        # Restore for other tests
        Colors._enabled = True

    def test_no_color_env_var(self):
        """NO_COLOR env var should disable colors."""
        import os
        from grove.repo_utils import Colors

        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run), \
             patch.dict(os.environ, {"NO_COLOR": "1"}):
            main(["check"])

        assert Colors._enabled is False
        Colors._enabled = True


class TestCliWorktreeSubcommand:
    def test_worktree_no_subcommand_returns_2(self):
        """'worktree' with no subcommand should print help and return 2."""
        result = main(["worktree"])
        assert result == 2

    def test_parse_worktree_add(self):
        """'worktree add my-branch ../path' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            result = main(["worktree", "add", "my-branch", "../path"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "add"
        assert args.branch == "my-branch"
        assert args.path == "../path"
        assert args.checkout is False

    def test_parse_worktree_add_checkout(self):
        """'worktree add --checkout' should set the checkout flag."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "add", "--checkout", "my-branch", "../path"])

        args = mock_run.call_args[0][0]
        assert args.checkout is True

    def test_parse_worktree_remove(self):
        """'worktree remove ../path' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "remove", "../path"])

        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "remove"
        assert args.path == "../path"
        assert args.force is False

    def test_parse_worktree_remove_force(self):
        """'worktree remove --force' should set the force flag."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "remove", "--force", "../path"])

        args = mock_run.call_args[0][0]
        assert args.force is True


class TestCliWorktreeMergeSubcommand:
    def test_parse_worktree_merge_branch(self):
        """'worktree merge my-feature' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            result = main(["worktree", "merge", "my-feature"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "merge"
        assert args.branch == "my-feature"
        assert args.continue_merge is False
        assert args.abort is False
        assert args.status is False
        assert args.dry_run is False
        assert args.no_recurse is False
        assert args.no_ff is False
        assert args.no_test is False

    def test_parse_worktree_merge_continue(self):
        """'worktree merge --continue' should set continue_merge=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "--continue"])

        args = mock_run.call_args[0][0]
        assert args.continue_merge is True
        assert args.branch is None

    def test_parse_worktree_merge_abort(self):
        """'worktree merge --abort' should set abort=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "--abort"])

        args = mock_run.call_args[0][0]
        assert args.abort is True

    def test_parse_worktree_merge_status(self):
        """'worktree merge --status' should set status=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "--status"])

        args = mock_run.call_args[0][0]
        assert args.status is True

    def test_parse_worktree_merge_all_flags(self):
        """'worktree merge my-feature --dry-run --no-recurse --no-ff --no-test' should set all."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "my-feature",
                  "--dry-run", "--no-recurse", "--no-ff", "--no-test"])

        args = mock_run.call_args[0][0]
        assert args.branch == "my-feature"
        assert args.dry_run is True
        assert args.no_recurse is True
        assert args.no_ff is True
        assert args.no_test is True

    def test_worktree_merge_no_args(self):
        """'worktree merge' with no branch should still dispatch."""
        mock_run = MagicMock(return_value=2)
        with patch("grove.worktree_merge.run", mock_run):
            result = main(["worktree", "merge"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.branch is None


class TestCliInvalidSubcommand:
    def test_unknown_subcommand_exits(self):
        """An unrecognised subcommand should cause argparse to exit with code 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code == 2
