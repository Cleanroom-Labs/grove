"""Tests for `grove worktree step` native command handlers."""

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grove.worktree_step as worktree_step
from grove.worktree_step import run as run_step


def _cp(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestWorktreeStep:
    def test_commit_show_prompt_prints_and_exits(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(
            step_command="commit",
            show_prompt=True,
            stage=None,
            no_verify=False,
            yes=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step._build_commit_prompt", return_value="PROMPT"),
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            result = run_step(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == "PROMPT"
        assert mock_git.call_count == 0

    def test_commit_stages_all_and_runs_git_commit(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="commit",
            show_prompt=False,
            stage="all",
            no_verify=True,
            yes=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step._build_commit_prompt", return_value="PROMPT"),
            patch(
                "grove.worktree_step._commit_with_generated_message",
                return_value=_cp(returncode=0),
            ) as mock_commit,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0),  # git add -A
                _cp(returncode=1),  # staged changes exist
            ]
            result = run_step(args)

        assert result == 0
        assert mock_git.call_args_list[0][0] == (tmp_git_repo, "add", "-A")
        assert mock_git.call_args_list[1][0] == (
            tmp_git_repo,
            "diff",
            "--cached",
            "--quiet",
        )
        mock_commit.assert_called_once_with(tmp_git_repo, "PROMPT")

    def test_commit_uses_configured_stage_when_flag_omitted(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="commit",
            show_prompt=False,
            stage=None,
            no_verify=True,
            yes=False,
        )

        config = SimpleNamespace(commit=SimpleNamespace(stage="tracked"))
        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step.load_config", return_value=config),
            patch("grove.worktree_step._build_commit_prompt", return_value="PROMPT"),
            patch(
                "grove.worktree_step._commit_with_generated_message",
                return_value=_cp(returncode=0),
            ) as mock_commit,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0),  # git add -u
                _cp(returncode=1),  # staged changes exist
            ]
            result = run_step(args)

        assert result == 0
        assert mock_git.call_args_list[0][0] == (tmp_git_repo, "add", "-u")
        mock_commit.assert_called_once_with(tmp_git_repo, "PROMPT")

    def test_commit_runs_pre_commit_hook_unless_no_verify(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="commit",
            show_prompt=False,
            stage="none",
            no_verify=False,
            yes=True,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step.run_configured_hooks", return_value=0
            ) as mock_hooks,
            patch("grove.worktree_step._build_commit_prompt", return_value="PROMPT"),
            patch(
                "grove.worktree_step._commit_with_generated_message",
                return_value=_cp(returncode=0),
            ) as mock_commit,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="feature\n"),  # current branch
                _cp(returncode=0, stdout="origin/main\n"),  # default branch
                _cp(returncode=1),  # staged changes exist
            ]
            result = run_step(args)

        assert result == 0
        hook_kwargs = mock_hooks.call_args.kwargs
        assert mock_hooks.call_args.args[1] == "pre-commit"
        assert hook_kwargs["yes"] is True
        assert hook_kwargs["variables"]["branch"] == "feature"
        mock_commit.assert_called_once_with(tmp_git_repo, "PROMPT")

    def test_squash_show_prompt_prints_and_exits(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(
            step_command="squash",
            target="main",
            show_prompt=True,
            stage=None,
            no_verify=False,
            yes=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step._resolve_squash_base",
                return_value="abc123",
            ),
            patch(
                "grove.worktree_step._build_squash_prompt", return_value="SQUASH_PROMPT"
            ),
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            result = run_step(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == "SQUASH_PROMPT"
        assert mock_git.call_count == 0

    def test_squash_resets_to_merge_base_and_commits(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="squash",
            target="main",
            show_prompt=False,
            stage="none",
            no_verify=True,
            yes=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step._build_squash_prompt", return_value="SQUASH_PROMPT"
            ),
            patch(
                "grove.worktree_step._commit_with_generated_message",
                return_value=_cp(returncode=0),
            ) as mock_commit,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="abc123\n"),  # merge-base
                _cp(returncode=0, stdout="3\n"),  # rev-list --count
                _cp(returncode=0),  # reset --soft
                _cp(returncode=1),  # staged changes exist
            ]
            result = run_step(args)

        assert result == 0
        assert mock_git.call_args_list[2][0] == (
            tmp_git_repo,
            "reset",
            "--soft",
            "abc123",
        )
        mock_commit.assert_called_once_with(tmp_git_repo, "SQUASH_PROMPT")

    def test_squash_runs_pre_commit_hook(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="squash",
            target="main",
            show_prompt=False,
            stage="none",
            no_verify=False,
            yes=True,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step.run_configured_hooks", return_value=0
            ) as mock_hooks,
            patch(
                "grove.worktree_step._build_squash_prompt", return_value="SQUASH_PROMPT"
            ),
            patch(
                "grove.worktree_step._commit_with_generated_message",
                return_value=_cp(returncode=0),
            ) as mock_commit,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="abc123\n"),  # merge-base
                _cp(returncode=0, stdout="2\n"),  # rev-list --count
                _cp(returncode=0),  # reset --soft
                _cp(returncode=0, stdout="feature\n"),  # branch for hook vars
                _cp(
                    returncode=0, stdout="origin/main\n"
                ),  # default branch for hook vars
                _cp(returncode=1),  # staged changes exist
            ]
            result = run_step(args)

        assert result == 0
        hook_kwargs = mock_hooks.call_args.kwargs
        assert mock_hooks.call_args.args[1] == "pre-commit"
        assert hook_kwargs["variables"]["target"] == "main"
        assert hook_kwargs["variables"]["base"] == "abc123"
        mock_commit.assert_called_once_with(tmp_git_repo, "SQUASH_PROMPT")

    def test_squash_no_commits_returns_0(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(
            step_command="squash",
            target="main",
            show_prompt=False,
            stage="none",
            no_verify=True,
            yes=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="abc123\n"),  # merge-base
                _cp(returncode=0, stdout="0\n"),  # rev-list --count
            ]
            result = run_step(args)

        assert result == 0
        assert "Nothing to squash" in capsys.readouterr().out

    def test_prune_invalid_min_age_returns_1(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(
            step_command="prune",
            dry_run=True,
            yes=False,
            min_age="tomorrow",
            foreground=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step.run_git",
                return_value=_cp(returncode=0, stdout="main\n"),
            ),
        ):
            result = run_step(args)

        assert result == 1
        assert "Invalid --min-age value" in capsys.readouterr().out

    def test_prune_dry_run_lists_candidates(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(
            step_command="prune",
            dry_run=True,
            yes=False,
            min_age=None,
            foreground=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step._resolve_default_branch", return_value="main"),
            patch(
                "grove.worktree_step._collect_prune_targets",
                return_value=[
                    {
                        "branch": "feature-a",
                        "path": "/tmp/feature-a",
                        "age": "2d ago",
                    }
                ],
            ),
        ):
            result = run_step(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "Would prune 1 worktree(s)" in output
        assert "feature-a" in output

    def test_prune_executes_remove_worktree(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="prune",
            dry_run=False,
            yes=True,
            min_age="2d",
            foreground=True,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step._resolve_default_branch", return_value="main"),
            patch(
                "grove.worktree_step._collect_prune_targets",
                return_value=[
                    {"branch": "feature-a", "path": "/tmp/feature-a", "age": "2d ago"},
                    {"branch": "feature-b", "path": "/tmp/feature-b", "age": "5d ago"},
                ],
            ),
            patch("grove.worktree.remove_worktree", return_value=0) as mock_remove,
        ):
            result = run_step(args)

        assert result == 0
        remove_args = mock_remove.call_args.args[0]
        assert remove_args.targets == ["feature-a", "feature-b"]
        assert remove_args.yes is True
        assert remove_args.foreground is True

    def test_copy_ignored_dry_run(self, tmp_git_repo: Path, tmp_path: Path, capsys):
        worktree_path = tmp_path / "copy-wt"
        current_branch = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_git_repo),
                "worktree",
                "add",
                "-b",
                "copy-target",
                str(worktree_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / ".gitignore").write_text("*.local\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", ".gitignore"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "Add gitignore"],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / "env.local").write_text("secret\n")

        args = argparse.Namespace(
            step_command="copy-ignored",
            from_branch=current_branch,
            to_branch="copy-target",
            dry_run=True,
            force=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
        ):
            result = run_step(args)

        assert result == 0
        assert "Would copy: env.local" in capsys.readouterr().out
        assert not (worktree_path / "env.local").exists()

    def test_copy_ignored_copies_file(self, tmp_git_repo: Path, tmp_path: Path):
        worktree_path = tmp_path / "copy-wt-real"
        current_branch = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_git_repo),
                "worktree",
                "add",
                "-b",
                "copy-target-real",
                str(worktree_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / ".gitignore").write_text("*.local\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", ".gitignore"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "Add gitignore"],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / "env.local").write_text("secret\n")

        args = argparse.Namespace(
            step_command="copy-ignored",
            from_branch=current_branch,
            to_branch="copy-target-real",
            dry_run=False,
            force=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
        ):
            result = run_step(args)

        assert result == 0
        assert (worktree_path / "env.local").read_text() == "secret\n"

    def test_copy_ignored_respects_worktreeinclude(
        self, tmp_git_repo: Path, tmp_path: Path
    ):
        worktree_path = tmp_path / "copy-wt-filtered"
        current_branch = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_git_repo),
                "worktree",
                "add",
                "-b",
                "copy-target-filtered",
                str(worktree_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / ".gitignore").write_text("*.local\nbuild/\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", ".gitignore"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "Add ignore rules"],
            check=True,
            capture_output=True,
            text=True,
        )
        (tmp_git_repo / ".worktreeinclude").write_text("env.local\n")
        (tmp_git_repo / "env.local").write_text("copy-me\n")
        (tmp_git_repo / "build").mkdir()
        (tmp_git_repo / "build" / "cache.bin").write_text("skip-me\n")

        args = argparse.Namespace(
            step_command="copy-ignored",
            from_branch=current_branch,
            to_branch="copy-target-filtered",
            dry_run=False,
            force=False,
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
        ):
            result = run_step(args)

        assert result == 0
        assert (worktree_path / "env.local").read_text() == "copy-me\n"
        assert not (worktree_path / "build" / "cache.bin").exists()

    def test_push_uses_explicit_target(self, tmp_git_repo: Path):
        args = argparse.Namespace(step_command="push", target="develop")

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step.run_git", return_value=_cp(returncode=0)
            ) as mock_git,
        ):
            result = run_step(args)

        assert result == 0
        mock_git.assert_called_once_with(
            tmp_git_repo,
            "push",
            ".",
            "HEAD:develop",
            check=False,
            capture=False,
        )

    def test_push_inferrs_target_from_default_branch(self, tmp_git_repo: Path):
        args = argparse.Namespace(step_command="push", target=None)

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="origin/main\n"),
                _cp(returncode=0),
            ]
            result = run_step(args)

        assert result == 0
        assert mock_git.call_args_list[1][0] == (
            tmp_git_repo,
            "push",
            ".",
            "HEAD:main",
        )

    def test_diff_forwards_extra_args(self, tmp_git_repo: Path):
        args = argparse.Namespace(
            step_command="diff",
            target=None,
            extra_args=["--", "README.md"],
        )

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            mock_git.side_effect = [
                _cp(returncode=0, stdout="origin/main\n"),
                _cp(returncode=0),
            ]
            result = run_step(args)

        assert result == 0
        assert mock_git.call_args_list[1][0] == (
            tmp_git_repo,
            "diff",
            "main...HEAD",
            "--",
            "README.md",
        )

    def test_rebase_failure_returns_1(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(step_command="rebase", target="main")

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
            patch(
                "grove.worktree_step.run_git",
                return_value=_cp(returncode=1, stderr="rebase failed"),
            ),
        ):
            result = run_step(args)

        assert result == 1
        assert "failed to rebase onto" in capsys.readouterr().out

    def test_unsupported_native_commands_fail_fast(self, tmp_git_repo: Path, capsys):
        args = argparse.Namespace(step_command="for-each")

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch("grove.worktree_step.maybe_delegate_step", return_value=None),
        ):
            result = run_step(args)

        assert result == 1
        assert "requires the worktrunk backend" in capsys.readouterr().out

    def test_wt_only_commands_delegate_before_native_rejection(
        self, tmp_git_repo: Path
    ):
        args = argparse.Namespace(step_command="for-each")

        with (
            patch("grove.worktree_step.find_repo_root", return_value=tmp_git_repo),
            patch(
                "grove.worktree_step.maybe_delegate_step", return_value=0
            ) as delegate,
        ):
            result = run_step(args)

        assert result == 0
        delegate.assert_called_once_with(tmp_git_repo, args)

    def test_commit_with_generated_message_uses_generated_text(
        self, tmp_git_repo: Path
    ):
        with (
            patch(
                "grove.worktree_step._generate_message", return_value="feat: generated"
            ),
            patch(
                "grove.worktree_step._commit_with_message",
                return_value=_cp(returncode=0),
            ) as mock_commit_with_message,
            patch("grove.worktree_step.run_git") as mock_git,
        ):
            result = worktree_step._commit_with_generated_message(
                tmp_git_repo, "PROMPT"
            )

        assert result.returncode == 0
        mock_commit_with_message.assert_called_once_with(
            tmp_git_repo, "feat: generated"
        )
        mock_git.assert_not_called()

    def test_commit_with_generated_message_falls_back_to_editor(
        self, tmp_git_repo: Path
    ):
        with (
            patch("grove.worktree_step._generate_message", return_value=None),
            patch(
                "grove.worktree_step._commit_with_message"
            ) as mock_commit_with_message,
            patch(
                "grove.worktree_step.run_git", return_value=_cp(returncode=0)
            ) as mock_git,
        ):
            result = worktree_step._commit_with_generated_message(
                tmp_git_repo, "PROMPT"
            )

        assert result.returncode == 0
        mock_commit_with_message.assert_not_called()
        mock_git.assert_called_once_with(
            tmp_git_repo,
            "commit",
            check=False,
            capture=False,
        )
