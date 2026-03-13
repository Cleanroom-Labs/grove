"""Tests for grove.cli argument parsing."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from grove.cli import main
from grove.cli_parsers import build_parser


class TestCliNoArgs:
    def test_no_args_returns_2(self, capsys):
        """Calling main() with no arguments should print usage and return 2."""
        result = main([])
        assert result == 2
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "grove" in captured.out.lower()


class TestCliInitSubcommand:
    def test_parse_init_defaults(self):
        """'init' should default to path='.' and legacy=False."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.init.run", mock_run):
            main(["init"])

        args = mock_run.call_args[0][0]
        assert args.command == "init"
        assert args.path == "."
        assert args.legacy is False

    def test_parse_init_legacy_flag(self):
        """'init --legacy' should set legacy=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.init.run", mock_run):
            main(["init", "--legacy"])

        args = mock_run.call_args[0][0]
        assert args.command == "init"
        assert args.legacy is True


class TestCliCheckSubcommand:
    def test_parse_check_verbose(self):
        """'check -v' should set command='check' and verbose=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run):
            main(["check", "-v"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "check"
        assert args.verbose is True

    def test_parse_check_no_flags(self):
        """'check' alone should set verbose=False."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.check.run", mock_run):
            main(["check"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "check"
        assert args.verbose is False


class TestCliPushSubcommand:
    def test_parse_push_dry_run_skip_checks(self):
        """'push --dry-run --skip-checks' should set both flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            main(["push", "--dry-run", "--skip-checks"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "push"
        assert args.dry_run is True
        assert args.skip_checks is True

    def test_parse_push_defaults(self):
        """'push' with no flags should have dry_run=False, skip_checks=False."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            main(["push"])

        args = mock_run.call_args[0][0]
        assert args.dry_run is False
        assert args.skip_checks is False

    def test_parse_push_short_flags(self):
        """'push -n -f' should set dry_run and skip_checks via short flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            main(["push", "-n", "-f"])

        args = mock_run.call_args[0][0]
        assert args.dry_run is True
        assert args.skip_checks is True

    def test_parse_push_verbose(self):
        """'push -v' should set verbose=True."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.push.run", mock_run):
            main(["push", "-v"])

        args = mock_run.call_args[0][0]
        assert args.verbose is True


class TestCliSyncSubcommand:
    def test_parse_sync_full(self):
        """'sync common --commit abc1234 --dry-run --no-push --skip-checks' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(
                [
                    "sync",
                    "common",
                    "--commit",
                    "abc1234",
                    "--dry-run",
                    "--no-push",
                    "--skip-checks",
                ]
            )

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "sync"
        assert args.group == "common"
        assert args.commit == "abc1234"
        assert args.dry_run is True
        assert args.no_push is True
        assert args.skip_checks is True

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
        assert args.skip_checks is False

    def test_parse_sync_group_only(self):
        """'sync common' should set group='common' and commit=None."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "common"])

        args = mock_run.call_args[0][0]
        assert args.group == "common"
        assert args.commit is None

    def test_parse_sync_group_and_commit(self):
        """'sync common --commit abc1234' should set both group and commit."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "common", "--commit", "abc1234"])

        args = mock_run.call_args[0][0]
        assert args.group == "common"
        assert args.commit == "abc1234"

    def test_parse_sync_short_flags(self):
        """'sync -n -f' should set dry_run and skip_checks via short flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.sync.run", mock_run):
            main(["sync", "-n", "-f"])

        args = mock_run.call_args[0][0]
        assert args.dry_run is True
        assert args.skip_checks is True


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


class TestCliShellSubcommand:
    def test_shell_no_subcommand_returns_2(self):
        """'shell' with no subcommand should print help and return 2."""
        result = main(["shell"])
        assert result == 2

    def test_parse_shell_init(self):
        """'shell init zsh' should dispatch to grove.shell.run."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.shell.run", mock_run):
            main(["shell", "init", "zsh"])

        args = mock_run.call_args[0][0]
        assert args.command == "shell"
        assert args.shell_command == "init"
        assert args.shell_name == "zsh"


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
        with (
            patch("grove.check.run", mock_run),
            patch.dict(os.environ, {"NO_COLOR": "1"}),
        ):
            main(["check"])

        assert Colors._enabled is False
        Colors._enabled = True


class TestCliWorktreeSubcommand:
    def test_worktree_no_subcommand_returns_2(self):
        """'worktree' with no subcommand should print help and return 2."""
        result = main(["worktree"])
        assert result == 2

    def test_parse_worktree_add(self):
        """'worktree add ../path my-branch' should parse correctly (path first, branch second)."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "add", "../path", "my-branch"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "add"
        assert args.path == "../path"
        assert args.branch == "my-branch"
        assert args.create_branch is False

    def test_parse_worktree_with_config_override(self, tmp_path):
        """'worktree --config <path> ...' should expose args.config."""
        config_path = tmp_path / "override.toml"
        config_path.write_text('[worktree]\nbackend = "native"\n')

        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "--config", str(config_path), "list"])

        args = mock_run.call_args[0][0]
        assert args.config == str(config_path)

    def test_worktree_config_override_missing_file_returns_1(self, tmp_path, capsys):
        """Missing --config file should fail before command dispatch."""
        missing = tmp_path / "missing.toml"
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            result = main(["worktree", "--config", str(missing), "list"])

        assert result == 1
        assert "config file not found" in capsys.readouterr().out
        mock_run.assert_not_called()

    def test_worktree_config_override_is_restored_after_dispatch(self, tmp_path):
        """--config should only override GROVE_CONFIG_PATH for one dispatch."""
        config_path = tmp_path / "override.toml"
        config_path.write_text('[worktree]\nbackend = "native"\n')

        def _fake_run(_args):
            assert os.environ.get("GROVE_CONFIG_PATH") == str(config_path.resolve())
            return 0

        with (
            patch("grove.worktree.run", side_effect=_fake_run),
            patch.dict(os.environ, {"GROVE_CONFIG_PATH": "/tmp/original"}, clear=False),
        ):
            result = main(["worktree", "--config", str(config_path), "list"])
            assert result == 0
            assert os.environ.get("GROVE_CONFIG_PATH") == "/tmp/original"

    def test_parse_worktree_add_create_branch(self):
        """'worktree add -b ../path new-branch' should set the create_branch flag."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "add", "-b", "../path", "new-branch"])

        args = mock_run.call_args[0][0]
        assert args.create_branch is True
        assert args.path == "../path"
        assert args.branch == "new-branch"

    def test_parse_worktree_add_exclude_sync_group(self):
        """'worktree add --exclude-sync-group' should set the opt-out flag."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "add", "--exclude-sync-group", "../path", "feature"])

        args = mock_run.call_args[0][0]
        assert args.exclude_sync_group is True

    def test_parse_worktree_init_submodules(self):
        """'worktree init-submodules' should parse all new init flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "init-submodules",
                    "../path",
                    "--reference",
                    ".",
                    "--branch",
                    "feature",
                    "--exclude-sync-group",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "init-submodules"
        assert args.path == "../path"
        assert args.reference == "."
        assert args.branch == "feature"
        assert args.exclude_sync_group is True

    def test_parse_worktree_switch(self):
        """'worktree switch' should parse branch and create flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "switch",
                    "feature",
                    "--branches",
                    "--remotes",
                    "-c",
                    "-b",
                    "main",
                    "-x",
                    "pwd",
                    "--clobber",
                    "--no-cd",
                    "--no-verify",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "switch"
        assert args.branch == "feature"
        assert args.branches is True
        assert args.remotes is True
        assert args.create is True
        assert args.base == "main"
        assert args.execute == "pwd"
        assert args.clobber is True
        assert args.no_cd is True
        assert args.no_verify is True

    def test_parse_hidden_directive_file_global_flag(self):
        """Hidden --directive-file should still parse for shell wrappers."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "--directive-file",
                    "/tmp/grove-switch-dir",
                    "worktree",
                    "switch",
                    "feature",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.directive_file == "/tmp/grove-switch-dir"

    def test_parse_worktree_list(self):
        """'worktree list' should parse native list flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "list",
                    "--format",
                    "json",
                    "--branches",
                    "--remotes",
                    "--progressive",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "list"
        assert args.format == "json"
        assert args.branches is True
        assert args.remotes is True
        assert args.full is None
        assert args.progressive is True

    def test_parse_worktree_list_with_config_after_subcommand(self, tmp_path):
        """'worktree list --config <path>' should parse override after subcommand."""
        config_path = tmp_path / "override.toml"
        config_path.write_text('[worktree]\nbackend = "native"\n')

        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "list", "--config", str(config_path)])

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "list"
        assert args.config == str(config_path)

    def test_parse_worktree_remove(self):
        """'worktree remove ../path' should parse compatibility path targets."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "remove", "../path"])

        args = mock_run.call_args[0][0]
        assert args.command == "worktree"
        assert args.worktree_command == "remove"
        assert args.targets == ["../path"]
        assert args.force is False
        assert args.no_delete_branch is False
        assert args.force_delete is False

    def test_parse_worktree_remove_force(self):
        """'worktree remove --force' should set the force flag."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "remove", "--force", "../path"])

        args = mock_run.call_args[0][0]
        assert args.force is True
        assert args.targets == ["../path"]

    def test_parse_worktree_remove_defaults_to_current(self):
        """'worktree remove' should allow zero explicit targets."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "remove"])

        args = mock_run.call_args[0][0]
        assert args.targets == []

    def test_parse_worktree_remove_branch_flags(self):
        """Branch-oriented remove flags should parse for future wt alignment."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "remove",
                    "feature",
                    "--no-delete-branch",
                    "-D",
                    "--foreground",
                    "--no-verify",
                    "--yes",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.targets == ["feature"]
        assert args.no_delete_branch is True
        assert args.force_delete is True
        assert args.foreground is True
        assert args.no_verify is True
        assert args.yes is True

    def test_parse_worktree_hook_run(self):
        """'worktree hook <type>' should parse hook execution args."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "hook",
                    "pre-remove",
                    "record",
                    "--var",
                    "branch=feature",
                    "--yes",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "hook"
        assert args.hook_type == "pre-remove"
        assert args.name == "record"
        assert args.var == ["branch=feature"]
        assert args.yes is True

    def test_parse_worktree_hook_show(self):
        """'worktree hook show' should parse show-specific flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "hook", "show", "pre-remove", "--expanded"])

        args = mock_run.call_args[0][0]
        assert args.hook_type == "show"
        assert args.name == "pre-remove"
        assert args.expanded is True

    def test_parse_worktree_step_diff(self):
        """'worktree step diff' should parse target plus extra diff args."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(["worktree", "step", "diff", "main", "--", "README.md"])

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "step"
        assert args.step_command == "diff"
        assert args.target == "main"
        assert args.extra_args == ["README.md"]

    def test_parse_worktree_step_diff_with_config_after_subcommand(self, tmp_path):
        """'worktree step diff --config <path>' should parse override at step level."""
        config_path = tmp_path / "override.toml"
        config_path.write_text('[worktree]\nbackend = "native"\n')

        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "step",
                    "diff",
                    "--config",
                    str(config_path),
                    "main",
                    "--",
                    "README.md",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "step"
        assert args.step_command == "diff"
        assert args.config == str(config_path)
        assert args.target == "main"

    def test_parse_worktree_step_commit(self):
        """'worktree step commit' should parse commit-stage flags."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree.run", mock_run):
            main(
                [
                    "worktree",
                    "step",
                    "commit",
                    "--stage",
                    "tracked",
                    "--show-prompt",
                    "--no-verify",
                    "--yes",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "step"
        assert args.step_command == "commit"
        assert args.stage == "tracked"
        assert args.show_prompt is True
        assert args.no_verify is True
        assert args.yes is True


class TestCliWorktreeMergeSubcommand:
    def test_parse_worktree_merge_branch(self):
        """'worktree merge my-feature' should parse correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "my-feature"])

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
        assert args.no_verify is False

    def test_parse_worktree_merge_with_config_after_subcommand(self, tmp_path):
        """'worktree merge --config <path>' should parse override in merge parser."""
        config_path = tmp_path / "override.toml"
        config_path.write_text('[worktree]\nbackend = "native"\n')

        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(
                [
                    "worktree",
                    "merge",
                    "--config",
                    str(config_path),
                    "my-feature",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.worktree_command == "merge"
        assert args.config == str(config_path)
        assert args.branch == "my-feature"

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
        """'worktree merge my-feature --dry-run --no-recurse --no-ff --no-test --no-verify' should set all."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(
                [
                    "worktree",
                    "merge",
                    "my-feature",
                    "--dry-run",
                    "--no-recurse",
                    "--no-ff",
                    "--no-test",
                    "--no-verify",
                ]
            )

        args = mock_run.call_args[0][0]
        assert args.branch == "my-feature"
        assert args.dry_run is True
        assert args.no_recurse is True
        assert args.no_ff is True
        assert args.no_test is True
        assert args.no_verify is True

    def test_parse_worktree_merge_short_flags(self):
        """'worktree merge my-feature -n -v' should set dry_run and verbose."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge", "my-feature", "-n", "-v"])

        args = mock_run.call_args[0][0]
        assert args.dry_run is True
        assert args.verbose is True

    def test_worktree_merge_no_args(self):
        """'worktree merge' with no branch should still dispatch."""
        mock_run = MagicMock(return_value=2)
        with patch("grove.worktree_merge.run", mock_run):
            main(["worktree", "merge"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.branch is None


class TestCliAliasExpansion:
    def test_alias_expands_single_token(self):
        """An alias like c = 'check' should expand 'c' to 'check'."""
        from grove.cli import _expand_aliases
        from grove.config import AliasConfig, GroveConfig

        config = GroveConfig(aliases=AliasConfig(mapping={"c": "check"}))
        with (
            patch("grove.repo_utils.find_repo_root", return_value="/fake"),
            patch("grove.config.load_config", return_value=config),
        ):
            result = _expand_aliases(["c", "-v"])
        assert result == ["check", "-v"]

    def test_alias_expands_multi_token(self):
        """An alias like wm = 'worktree merge' should expand to two tokens."""
        from grove.cli import _expand_aliases
        from grove.config import AliasConfig, GroveConfig

        config = GroveConfig(aliases=AliasConfig(mapping={"wm": "worktree merge"}))
        with (
            patch("grove.repo_utils.find_repo_root", return_value="/fake"),
            patch("grove.config.load_config", return_value=config),
        ):
            result = _expand_aliases(["wm", "--status"])
        assert result == ["worktree", "merge", "--status"]

    def test_no_alias_match_passes_through(self):
        """Non-matching commands should pass through unchanged."""
        from grove.cli import _expand_aliases
        from grove.config import AliasConfig, GroveConfig

        config = GroveConfig(aliases=AliasConfig(mapping={"c": "check"}))
        with (
            patch("grove.repo_utils.find_repo_root", return_value="/fake"),
            patch("grove.config.load_config", return_value=config),
        ):
            result = _expand_aliases(["push", "--dry-run"])
        assert result == ["push", "--dry-run"]

    def test_no_repo_root_passes_through(self):
        """When not in a git repo, aliases should be skipped gracefully."""
        from grove.cli import _expand_aliases

        with patch("grove.repo_utils.find_repo_root", side_effect=FileNotFoundError):
            result = _expand_aliases(["wm"])
        assert result == ["wm"]

    def test_empty_argv_passes_through(self):
        """Empty argv should pass through unchanged."""
        from grove.cli import _expand_aliases

        result = _expand_aliases([])
        assert result == []

    def test_alias_integration_with_main(self):
        """An alias should resolve through main() to the correct command handler."""
        from grove.config import AliasConfig, GroveConfig

        config = GroveConfig(aliases=AliasConfig(mapping={"c": "check"}))
        mock_run = MagicMock(return_value=0)
        with (
            patch("grove.repo_utils.find_repo_root", return_value="/fake"),
            patch("grove.config.load_config", return_value=config),
            patch("grove.check.run", mock_run),
        ):
            main(["c", "-v"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "check"
        assert args.verbose is True


class TestCliWorktreeAliases:
    def test_alias_sw_dispatches_as_switch(self):
        """'grove worktree sw' should parse as worktree_command='switch'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "sw", "feat", "--no-cd"])
        assert args.worktree_command == "switch"
        assert args.branch == "feat"
        assert args.no_cd is True

    def test_alias_ls_dispatches_as_list(self):
        """'grove worktree ls' should parse as worktree_command='list'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "ls"])
        assert args.worktree_command == "list"

    def test_alias_m_dispatches_as_merge(self):
        """'grove worktree m' should parse as worktree_command='merge'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "m", "--status"])
        assert args.worktree_command == "merge"
        assert args.status is True

    def test_alias_rm_dispatches_as_remove(self):
        """'grove worktree rm' should parse as worktree_command='remove'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "rm", "my-branch"])
        assert args.worktree_command == "remove"

    def test_alias_st_dispatches_as_step(self):
        """'grove worktree st diff' should parse as worktree_command='step'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "st", "diff"])
        assert args.worktree_command == "step"
        assert args.step_command == "diff"

    def test_alias_cb_dispatches_as_checkout_branches(self):
        """'grove worktree cb' should parse as worktree_command='checkout-branches'."""
        parser = build_parser()
        args = parser.parse_args(["worktree", "cb"])
        assert args.worktree_command == "checkout-branches"


class TestCliConfigSubcommand:
    def test_config_no_subcommand_returns_2(self):
        """'config' with no subcommand should print help and return 2."""
        result = main(["config"])
        assert result == 2

    def test_parse_config_import_wt(self):
        """'config import-wt --user --dry-run' should dispatch correctly."""
        mock_run = MagicMock(return_value=0)
        with patch("grove.config_import.run", mock_run):
            main(["config", "import-wt", "--user", "--dry-run"])

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args.command == "config"
        assert args.config_command == "import-wt"
        assert args.user is True
        assert args.project is False
        assert args.dry_run is True


class TestCliInvalidSubcommand:
    def test_unknown_subcommand_exits(self):
        """An unrecognised subcommand should cause argparse to exit with code 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code == 2


class TestCliE2E:
    def test_init_default_creates_project_config_via_real_cli(self, tmp_path: Path):
        """Subprocess CLI invocation should create .config/grove.toml by default."""
        workspace_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace_root / "src")

        result = subprocess.run(
            [sys.executable, "-m", "grove.cli", "init", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert (tmp_path / ".config" / "grove.toml").exists()

    def test_check_shows_legacy_config_deprecation_warning(self, tmp_git_repo: Path):
        """Real CLI should emit a deprecation warning when only .grove.toml exists."""
        (tmp_git_repo / ".grove.toml").write_text('[worktree]\nbackend = "native"\n')
        workspace_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace_root / "src")

        result = subprocess.run(
            [sys.executable, "-m", "grove.cli", "check"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert "using deprecated legacy config" in result.stderr
