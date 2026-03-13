"""Tests for WorkTrunk backend delegation helpers."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from grove.worktree_backend import (
    maybe_delegate_hook,
    maybe_delegate_list,
    maybe_delegate_remove,
    maybe_delegate_step,
    maybe_delegate_switch,
)


def _set_backend(repo_root: Path, mode: str) -> None:
    existing = (repo_root / ".grove.toml").read_text()
    (repo_root / ".grove.toml").write_text(
        existing + f'\n[worktree]\nbackend = "{mode}"\n'
    )


class TestWorktreeBackendDelegation:
    def test_switch_delegates_to_wt(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            branch="feature",
            branches=True,
            remotes=True,
            create=True,
            base="main",
            execute="pwd",
            yes=True,
            clobber=True,
            no_cd=True,
            no_verify=True,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_switch(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["wt", "switch"]
        assert "feature" in cmd
        assert "--create" in cmd
        assert "--base" in cmd

    def test_list_delegates_to_wt(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            format="json",
            branches=True,
            remotes=True,
            full=True,
            progressive=True,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_list(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["wt", "list"]
        assert "--format" in cmd
        assert "json" in cmd
        assert "--progressive" in cmd

    def test_remove_delegates_to_wt(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            targets=["feature"],
            path=None,
            force=True,
            no_delete_branch=True,
            force_delete=True,
            foreground=True,
            no_verify=True,
            yes=True,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_remove(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["wt", "remove"]
        assert "feature" in cmd
        assert "--force" in cmd
        assert "--no-delete-branch" in cmd

    def test_wt_backend_without_wt_binary_returns_1(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            branch="feature",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
            no_verify=False,
        )
        with patch("grove.worktree_backend.shutil.which", return_value=None):
            result = maybe_delegate_switch(tmp_submodule_tree, args)

        assert result == 1

    def test_step_diff_delegates_to_wt(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            step_command="diff",
            target="main",
            extra_args=["--", "README.md"],
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_step(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["wt", "step", "diff"]
        assert cmd[3] == "main"
        assert cmd[4:] == ["--", "README.md"]

    def test_step_commit_forwards_common_flags(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            step_command="commit",
            yes=True,
            no_verify=True,
            show_prompt=True,
            stage="tracked",
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_step(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["wt", "step", "commit"]
        assert "--yes" in cmd
        assert "--no-verify" in cmd
        assert "--show-prompt" in cmd
        assert "--stage" in cmd

    def test_step_prune_dry_run_prints_wt_command_without_exec(
        self, tmp_submodule_tree: Path, capsys
    ):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            step_command="prune",
            dry_run=True,
            yes=False,
            foreground=False,
            min_age=None,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend.subprocess.run") as mock_run,
        ):
            result = maybe_delegate_step(tmp_submodule_tree, args)

        assert result == 0
        output = capsys.readouterr().out
        assert "will run: wt step prune --dry-run" in output
        mock_run.assert_not_called()

    def test_auto_backend_delegates_when_wt_is_available(
        self, tmp_submodule_tree: Path
    ):
        _set_backend(tmp_submodule_tree, "auto")
        args = argparse.Namespace(
            branch="feature",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
            no_verify=False,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_switch(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["wt", "switch"]

    def test_auto_backend_falls_back_to_native_when_wt_missing(
        self, tmp_submodule_tree: Path
    ):
        _set_backend(tmp_submodule_tree, "auto")
        args = argparse.Namespace(
            branch="feature",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
            no_verify=False,
        )
        with patch("grove.worktree_backend.shutil.which", return_value=None):
            result = maybe_delegate_switch(tmp_submodule_tree, args)
        assert result is None

    def test_native_backend_never_delegates_even_when_wt_is_available(
        self,
        tmp_submodule_tree: Path,
    ):
        _set_backend(tmp_submodule_tree, "native")
        args = argparse.Namespace(
            branch="feature",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
            no_verify=False,
        )
        with patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"):
            result = maybe_delegate_switch(tmp_submodule_tree, args)
        assert result is None

    def test_delegation_sets_worktrunk_config_path_with_synthesized_config(
        self,
        tmp_submodule_tree: Path,
    ):
        _set_backend(tmp_submodule_tree, "wt")
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            'worktree-path = "../{{ branch | sanitize }}"\n'
            + existing
            + "\n[list]\nfull = true\n"
            + '[pre-remove]\ncheck = "echo ok"\n'
            + '[hooks.post-create]\nsetup = "echo setup"\n'
        )

        args = argparse.Namespace(
            format="json",
            branches=False,
            remotes=False,
            full=False,
            progressive=False,
        )
        captured: dict[str, str] = {}

        def _fake_run(command, check=False, env=None):
            assert env is not None
            config_path = env.get("WORKTRUNK_CONFIG_PATH")
            assert config_path is not None
            captured["path"] = config_path
            captured["content"] = Path(config_path).read_text()
            return subprocess.CompletedProcess(args=command, returncode=0)

        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend.subprocess.run", side_effect=_fake_run),
        ):
            result = maybe_delegate_list(tmp_submodule_tree, args)

        assert result == 0
        assert 'worktree-path = "../{{ branch | sanitize }}"' in captured["content"]
        assert "[list]" in captured["content"]
        assert "[pre-remove]" in captured["content"]
        assert "[hooks.post-create]" in captured["content"]
        assert not Path(captured["path"]).exists()

    def test_delegation_synthesis_honors_explicit_config_override(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
    ):
        _set_backend(tmp_submodule_tree, "wt")
        override = tmp_submodule_tree.parent / "override.toml"
        override.write_text(
            'worktree-path = "../override/{{ branch }}"\n[list]\nfull = true\n'
        )
        monkeypatch.setenv("GROVE_CONFIG_PATH", str(override))

        args = argparse.Namespace(
            format="json",
            branches=False,
            remotes=False,
            full=False,
            progressive=False,
        )
        captured: dict[str, str] = {}

        def _fake_run(command, check=False, env=None):
            assert env is not None
            config_path = env.get("WORKTRUNK_CONFIG_PATH")
            assert config_path is not None
            captured["content"] = Path(config_path).read_text()
            return subprocess.CompletedProcess(args=command, returncode=0)

        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend.subprocess.run", side_effect=_fake_run),
        ):
            result = maybe_delegate_list(tmp_submodule_tree, args)

        assert result == 0
        assert 'worktree-path = "../override/{{ branch }}"' in captured["content"]

    def test_hook_show_delegates_to_wt(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            hook_type="show",
            name="pre-remove",
            expanded=True,
            var=None,
            yes=False,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_hook(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd == ["wt", "hook", "show", "pre-remove", "--expanded"]

    def test_hook_run_delegates_to_wt_with_yes_and_vars(self, tmp_submodule_tree: Path):
        _set_backend(tmp_submodule_tree, "wt")
        args = argparse.Namespace(
            hook_type="pre-remove",
            name="record",
            expanded=False,
            var=["branch=feature", "target=main"],
            yes=True,
        )
        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch(
                "grove.worktree_backend.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            result = maybe_delegate_hook(tmp_submodule_tree, args)

        assert result == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["wt", "hook", "pre-remove", "record"]
        assert "--yes" in cmd
        assert cmd.count("--var") == 2


class TestWorktreeBackendIntegration:
    def test_delegates_to_real_wt_binary_when_available(
        self,
        tmp_git_repo: Path,
        monkeypatch,
    ):
        """Smoke test real wt delegation without subprocess mocking."""
        try:
            version = subprocess.run(
                ["wt", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            pytest.skip("wt binary not available in test environment")
        if version.returncode != 0:
            pytest.skip("wt binary not available in test environment")

        baseline = subprocess.run(
            ["wt", "list", "--format", "json"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
            check=False,
        )
        if baseline.returncode != 0:
            pytest.skip(
                f"wt list not operational in test environment: {baseline.stderr}"
            )

        config_path = tmp_git_repo / ".config" / "grove.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[worktree]\nbackend = "wt"\n')

        hidden_which = shutil.which

        def _which(cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None):
            if cmd == "wt":
                return "wt"
            return hidden_which(cmd, mode=mode, path=path)

        monkeypatch.setattr(shutil, "which", _which)

        args = argparse.Namespace(
            format="json",
            branches=False,
            remotes=False,
            full=False,
            progressive=False,
        )
        result = maybe_delegate_list(tmp_git_repo, args)
        assert result == 0
