"""Tests for grove.hooks."""

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.hooks import run


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


class TestHooks:
    def test_show_hooks_with_expansion(self, tmp_submodule_tree: Path, capsys):
        """`hook show --expanded` should render template variables."""
        from grove.repo_utils import Colors

        Colors.disable()

        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config + '\n[pre-remove]\nrecord = "echo {{ branch }}"\n'
        )
        args = argparse.Namespace(
            hook_type="show",
            name="pre-remove",
            expanded=True,
            var=["branch=feature"],
            yes=False,
        )

        with patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "pre-remove:" in output
        assert "record: echo feature" in output

    def test_run_named_hook(self, tmp_submodule_tree: Path):
        """`worktree hook <type> <name>` should run one hook command."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-remove]\nrecord = "echo {{ branch }} > .hook-run-log"\n'
        )
        args = argparse.Namespace(
            hook_type="pre-remove",
            name="record",
            expanded=False,
            var=["branch=feature"],
            yes=True,
        )

        with patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)

        assert result == 0
        assert (tmp_submodule_tree / ".hook-run-log").read_text().strip() == "feature"

    def test_hook_template_sanitize_filter(self, tmp_submodule_tree: Path):
        """`| sanitize` should replace path separators in hook templates."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-remove]\nrecord = "echo {{ branch | sanitize }} > .hook-run-log"\n'
        )
        args = argparse.Namespace(
            hook_type="pre-remove",
            name="record",
            expanded=False,
            var=["branch=feature/subtask"],
            yes=True,
        )

        with patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)

        assert result == 0
        assert (
            tmp_submodule_tree / ".hook-run-log"
        ).read_text().strip() == "feature-subtask"

    def test_show_hooks_expands_baseline_variables(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Expanded hooks should include built-in repo/worktree/commit variables."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-remove]\nrecord = "echo {{ repo }} {{ worktree_name }} {{ primary_worktree_path }} {{ commit }} {{ short_commit }}"\n'
        )
        args = argparse.Namespace(
            hook_type="show",
            name="pre-remove",
            expanded=True,
            var=None,
            yes=False,
        )

        commit = _git(tmp_submodule_tree, "rev-parse", "HEAD").stdout.strip()
        short_commit = commit[:12]

        with patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)

        assert result == 0
        output = capsys.readouterr().out
        assert (
            f"record: echo {tmp_submodule_tree.name} {tmp_submodule_tree.name}"
            in output
        )
        assert str(tmp_submodule_tree) in output
        assert commit in output
        assert short_commit in output

    def test_invalid_var_returns_1(self, tmp_submodule_tree: Path):
        """Malformed --var values should fail fast."""
        args = argparse.Namespace(
            hook_type="show",
            name=None,
            expanded=False,
            var=["missing_equals"],
            yes=False,
        )
        with patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree):
            result = run(args)
        assert result == 1

    def test_hook_prompt_decline_returns_1(self, tmp_submodule_tree: Path):
        """Interactive decline should skip hook execution and return 1."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-remove]\nrecord = "echo touched > .hook-run-log"\n'
        )
        args = argparse.Namespace(
            hook_type="pre-remove",
            name="record",
            expanded=False,
            var=None,
            yes=False,
        )

        with (
            patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.hooks._should_prompt_for_hooks", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            result = run(args)

        assert result == 1
        assert not (tmp_submodule_tree / ".hook-run-log").exists()

    def test_hook_prompt_approve_runs_command(self, tmp_submodule_tree: Path):
        """Interactive approval should allow hook execution."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-remove]\nrecord = "echo touched > .hook-run-log"\n'
        )
        args = argparse.Namespace(
            hook_type="pre-remove",
            name="record",
            expanded=False,
            var=None,
            yes=False,
        )

        with (
            patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.hooks._should_prompt_for_hooks", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            result = run(args)

        assert result == 0
        assert (tmp_submodule_tree / ".hook-run-log").read_text().strip() == "touched"

    def test_hooks_run_delegates_when_backend_requests_wt(
        self, tmp_submodule_tree: Path
    ):
        """hooks.run should short-circuit to backend delegation when requested."""
        args = argparse.Namespace(
            hook_type="show",
            name=None,
            expanded=False,
            var=None,
            yes=False,
        )
        with (
            patch("grove.hooks.find_repo_root", return_value=tmp_submodule_tree),
            patch("grove.hooks.maybe_delegate_hook", return_value=0) as mock_delegate,
        ):
            result = run(args)

        assert result == 0
        mock_delegate.assert_called_once_with(tmp_submodule_tree, args)
