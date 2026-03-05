"""Tests for native `grove worktree switch`."""

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.repo_utils import get_state_path
from grove.worktree_switch import switch_worktree


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


class TestWorktreeSwitch:
    def test_switch_existing_branch_prints_worktree_path(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Switching to an existing worktree should print its path."""
        wt_path = tmp_submodule_tree.parent / "switch-existing"
        _git(
            tmp_submodule_tree, "worktree", "add", "-b", "switch-existing", str(wt_path)
        )

        args = argparse.Namespace(
            branch="switch-existing",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == str(wt_path)

    def test_switch_shortcut_default_branch_targets_main_worktree(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """^ should resolve to the default branch/main worktree."""
        wt_path = tmp_submodule_tree.parent / "switch-default"
        _git(
            tmp_submodule_tree, "worktree", "add", "-b", "switch-default", str(wt_path)
        )

        args = argparse.Namespace(
            branch="^",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch("grove.worktree_switch.find_repo_root", return_value=wt_path):
            result = switch_worktree(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == str(tmp_submodule_tree)

    def test_switch_shortcut_current_worktree(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """@ should resolve to the current worktree."""
        args = argparse.Namespace(
            branch="@",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == str(tmp_submodule_tree)

    def test_switch_shortcut_previous_worktree(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """- should toggle to the previously visited worktree."""
        wt_path = tmp_submodule_tree.parent / "switch-previous"
        _git(
            tmp_submodule_tree, "worktree", "add", "-b", "switch-previous", str(wt_path)
        )

        switch_args = argparse.Namespace(
            branch="switch-previous",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )
        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            assert switch_worktree(switch_args) == 0

        toggle_args = argparse.Namespace(
            branch="-",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )
        with patch("grove.worktree_switch.find_repo_root", return_value=wt_path):
            result = switch_worktree(toggle_args)

        lines = [line for line in capsys.readouterr().out.strip().splitlines() if line]
        assert result == 0
        assert lines[-1] == str(tmp_submodule_tree)

    def test_switch_writes_directive_file_for_shell_wrapper(
        self,
        tmp_submodule_tree: Path,
        tmp_path: Path,
        capsys,
    ):
        """Switch should write the target path into --directive-file by default."""
        wt_path = tmp_submodule_tree.parent / "switch-directive"
        _git(
            tmp_submodule_tree,
            "worktree",
            "add",
            "-b",
            "switch-directive",
            str(wt_path),
        )
        directive_file = tmp_path / "directive.txt"

        args = argparse.Namespace(
            branch="switch-directive",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
            directive_file=str(directive_file),
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert directive_file.read_text() == str(wt_path)
        assert capsys.readouterr().out.strip() == str(wt_path)

    def test_switch_no_cd_skips_directive_file(
        self,
        tmp_submodule_tree: Path,
        tmp_path: Path,
    ):
        """--no-cd should suppress directive-file writes."""
        wt_path = tmp_submodule_tree.parent / "switch-no-cd"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "switch-no-cd", str(wt_path))
        directive_file = tmp_path / "directive.txt"

        args = argparse.Namespace(
            branch="switch-no-cd",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=True,
            directive_file=str(directive_file),
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert not directive_file.exists()

    def test_switch_create_creates_new_worktree(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """-c should create a worktree when none exists yet."""
        args = argparse.Namespace(
            branch="switch-create",
            branches=False,
            remotes=False,
            create=True,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        target_path = (
            tmp_submodule_tree.parent / f"{tmp_submodule_tree.name}.switch-create"
        )
        assert target_path.exists()
        assert capsys.readouterr().out.strip().splitlines()[-1] == str(target_path)
        current_branch = _git(target_path, "branch", "--show-current").stdout.strip()
        assert current_branch == "switch-create"

    def test_switch_create_honors_worktree_path_template(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Configured worktree-path templates should resolve relative to the repo root."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            'worktree-path = "../custom/{{ branch | sanitize }}"\n\n' + existing_config
        )

        args = argparse.Namespace(
            branch="feature/custom",
            branches=False,
            remotes=False,
            create=True,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        target_path = tmp_submodule_tree.parent / "custom" / "feature-custom"
        assert target_path.exists()
        assert capsys.readouterr().out.strip().splitlines()[-1] == str(target_path)

    def test_switch_create_clobber_replaces_existing_path(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """--clobber should remove a stale target path before creating a worktree."""
        target_path = (
            tmp_submodule_tree.parent / f"{tmp_submodule_tree.name}.switch-clobber"
        )
        target_path.mkdir(parents=True, exist_ok=True)
        (target_path / "stale.txt").write_text("stale\n")

        args = argparse.Namespace(
            branch="switch-clobber",
            branches=False,
            remotes=False,
            create=True,
            base=None,
            execute=None,
            yes=False,
            clobber=True,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert target_path.exists()
        assert not (target_path / "stale.txt").exists()
        assert capsys.readouterr().out.strip().splitlines()[-1] == str(target_path)

    def test_switch_create_clobber_refuses_active_worktree_path(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """--clobber should never delete a path used by an active worktree."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            'worktree-path = "{{ repo_path }}"\n\n' + existing_config
        )

        args = argparse.Namespace(
            branch="switch-clobber-active",
            branches=False,
            remotes=False,
            create=True,
            base=None,
            execute=None,
            yes=False,
            clobber=True,
            no_cd=True,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 1
        assert "cannot clobber active worktree path" in capsys.readouterr().out

    def test_switch_runs_pre_and_post_switch_hooks(
        self,
        tmp_submodule_tree: Path,
    ):
        """Existing-worktree switches should run pre/post-switch hooks."""
        wt_path = tmp_submodule_tree.parent / "switch-hooks"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "switch-hooks", str(wt_path))
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-switch]\nrecord = "echo pre-{{ branch }} >> .switch-hook-log"\n'
            + '[post-switch]\nrecord = "echo post-{{ branch }} >> .switch-hook-log"\n'
        )

        args = argparse.Namespace(
            branch="switch-hooks",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=True,
            clobber=False,
            no_cd=True,
            no_verify=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        lines = (tmp_submodule_tree / ".switch-hook-log").read_text().splitlines()
        assert "pre-switch-hooks" in lines
        assert "post-switch-hooks" in lines

    def test_switch_no_verify_skips_hooks(
        self,
        tmp_submodule_tree: Path,
    ):
        """--no-verify should suppress switch hooks."""
        wt_path = tmp_submodule_tree.parent / "switch-no-hooks"
        _git(
            tmp_submodule_tree, "worktree", "add", "-b", "switch-no-hooks", str(wt_path)
        )
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[pre-switch]\nrecord = "echo touched >> .switch-hook-log"\n'
        )

        args = argparse.Namespace(
            branch="switch-no-hooks",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=True,
            clobber=False,
            no_cd=True,
            no_verify=True,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        assert not (tmp_submodule_tree / ".switch-hook-log").exists()

    def test_switch_create_runs_post_create_hook(
        self,
        tmp_submodule_tree: Path,
    ):
        """Created worktrees should run post-create hooks."""
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config
            + '\n[post-create]\nrecord = "echo create-{{ branch }} >> .switch-hook-log"\n'
        )

        args = argparse.Namespace(
            branch="switch-create-hook",
            branches=False,
            remotes=False,
            create=True,
            base=None,
            execute=None,
            yes=True,
            clobber=False,
            no_cd=True,
            no_verify=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        lines = (tmp_submodule_tree / ".switch-hook-log").read_text().splitlines()
        assert "create-switch-create-hook" in lines

    def test_switch_without_create_rejects_missing_worktree(
        self,
        tmp_submodule_tree: Path,
    ):
        """Switching to a branch without a worktree should suggest -c."""
        _git(tmp_submodule_tree, "branch", "missing-worktree")

        args = argparse.Namespace(
            branch="missing-worktree",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 1

    def test_switch_interactive_selection_uses_list_index(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """No branch arg should prompt for a numbered selection."""
        wt_path = tmp_submodule_tree.parent / "switch-interactive"
        _git(
            tmp_submodule_tree,
            "worktree",
            "add",
            "-b",
            "switch-interactive",
            str(wt_path),
        )

        args = argparse.Namespace(
            branch=None,
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with (
            patch(
                "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
            ),
            patch("builtins.input", return_value="2"),
        ):
            result = switch_worktree(args)

        lines = [line for line in capsys.readouterr().out.strip().splitlines() if line]
        assert result == 0
        assert lines[-1] == str(wt_path)

    def test_switch_state_is_shared_in_main_worktree_git_dir(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Previous-worktree state should be written under the main worktree git dir."""
        wt_path = tmp_submodule_tree.parent / "switch-state"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "switch-state", str(wt_path))

        args = argparse.Namespace(
            branch="switch-state",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 0
        capsys.readouterr()
        state_path = get_state_path(tmp_submodule_tree, "worktree-switch-state.json")
        payload = json.loads(state_path.read_text())
        assert payload["current_branch"] == "switch-state"
        assert payload["current_path"] == str(wt_path)

    def test_switch_pr_shortcut_requires_wt_backend(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Native mode should fail fast for pr:N shortcut syntax."""
        args = argparse.Namespace(
            branch="pr:123",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 1
        assert "requires the worktrunk backend" in capsys.readouterr().out

    def test_switch_mr_shortcut_requires_wt_backend(
        self,
        tmp_submodule_tree: Path,
        capsys,
    ):
        """Native mode should fail fast for mr:N shortcut syntax."""
        args = argparse.Namespace(
            branch="mr:77",
            branches=False,
            remotes=False,
            create=False,
            base=None,
            execute=None,
            yes=False,
            clobber=False,
            no_cd=False,
        )

        with patch(
            "grove.worktree_switch.find_repo_root", return_value=tmp_submodule_tree
        ):
            result = switch_worktree(args)

        assert result == 1
        assert "requires the worktrunk backend" in capsys.readouterr().out
