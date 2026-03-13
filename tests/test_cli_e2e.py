"""End-to-end CLI tests for worktrunk integration flows."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.cli import main
from grove.user_config import get_user_config_path, load_toml_file


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


class TestCliE2ENativeLifecycle:
    def test_switch_create_initializes_submodules_on_named_branches(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_submodule_tree)
        branch = "e2e-switch"
        expected_path = (
            tmp_submodule_tree.parent / f"{tmp_submodule_tree.name}.{branch}"
        )

        result = main(["worktree", "switch", "-c", branch, "--no-cd", "--yes"])

        assert result == 0
        assert expected_path.exists()
        assert (expected_path / "technical-docs").exists()
        assert (expected_path / "technical-docs" / "common").exists()
        assert (
            _git(
                expected_path / "technical-docs", "branch", "--show-current"
            ).stdout.strip()
            == branch
        )
        assert (
            _git(
                expected_path / "technical-docs" / "common",
                "branch",
                "--show-current",
            ).stdout.strip()
            == branch
        )

    def test_list_json_outputs_valid_json(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
        capsys,
    ):
        branch = "e2e-list"
        wt_path = tmp_submodule_tree.parent / "e2e-list-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", branch, str(wt_path))
        monkeypatch.chdir(tmp_submodule_tree)

        result = main(["worktree", "list", "--format", "json"])

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, dict)
        assert "worktrees" in payload
        rows = payload["worktrees"]
        assert isinstance(rows, list)
        assert any(row.get("branch") == branch for row in rows)
        assert all("path" in row for row in rows)

    def test_step_diff_shows_merge_base_diff(
        self, tmp_git_repo: Path, monkeypatch, capfd
    ):
        monkeypatch.chdir(tmp_git_repo)
        _git(tmp_git_repo, "checkout", "-b", "e2e-diff")
        (tmp_git_repo / "diff-target.txt").write_text("changed\n")
        _git(tmp_git_repo, "add", "diff-target.txt")
        _git(tmp_git_repo, "commit", "-m", "add diff target")

        result = main(["worktree", "step", "diff", "main"])

        assert result == 0
        assert "diff-target.txt" in capfd.readouterr().out

    def test_hook_show_lists_configured_hooks(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
        capsys,
    ):
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing + '\n[post-create]\nbootstrap = "echo boot"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)

        result = main(["worktree", "hook", "show"])

        assert result == 0
        output = capsys.readouterr().out
        assert "post-create" in output
        assert "bootstrap: echo boot" in output

    def test_remove_branch_runs_hooks(self, tmp_submodule_tree: Path, monkeypatch):
        branch = "e2e-remove"
        wt_path = tmp_submodule_tree.parent / "e2e-remove-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", branch, str(wt_path))
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing
            + '\n[pre-remove]\nrecord = "echo pre-{{ branch }} >> .e2e-hook-log"\n'
            + '[post-remove]\nrecord = "echo post-{{ branch }} >> .e2e-hook-log"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)

        result = main(["worktree", "remove", branch, "--yes"])

        assert result == 0
        assert not wt_path.exists()
        lines = (tmp_submodule_tree / ".e2e-hook-log").read_text().splitlines()
        assert f"pre-{branch}" in lines
        assert f"post-{branch}" in lines


class TestCliE2EDelegation:
    def test_switch_delegates_with_synthesized_config(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
    ):
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            'worktree-path = "../wt/{{ branch | sanitize }}"\n'
            + existing
            + '\n[worktree]\nbackend = "auto"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)
        captured: dict[str, str] = {}

        def _fake_run(command, *, env=None):
            assert env is not None
            config_path = env.get("WORKTRUNK_CONFIG_PATH")
            assert config_path is not None
            captured["command"] = " ".join(command)
            captured["content"] = Path(config_path).read_text()
            return 0

        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend._run_wt_command", side_effect=_fake_run),
        ):
            result = main(["worktree", "switch", "feature-1"])

        assert result == 0
        assert captured["command"].startswith("wt switch feature-1")
        assert 'worktree-path = "../wt/{{ branch | sanitize }}"' in captured["content"]

    def test_step_for_each_delegates_when_wt_available(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
    ):
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing + '\n[worktree]\nbackend = "auto"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)

        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend._run_wt_command", return_value=0) as mock_run,
        ):
            result = main(["worktree", "step", "for-each", "--", "echo", "{branch}"])

        assert result == 0
        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["wt", "step", "for-each"]
        assert cmd[3:] == ["echo", "{branch}"]

    def test_step_for_each_without_wt_errors_in_native_mode(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
        capsys,
    ):
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing + '\n[worktree]\nbackend = "auto"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)

        result = main(["worktree", "step", "for-each", "--", "echo", "{branch}"])

        assert result == 1
        assert "requires the worktrunk backend" in capsys.readouterr().out

    def test_delegated_dry_run_prints_command_and_skips_execution(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
        capsys,
    ):
        existing = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing + '\n[worktree]\nbackend = "auto"\n'
        )
        monkeypatch.chdir(tmp_submodule_tree)

        with (
            patch("grove.worktree_backend.shutil.which", return_value="/usr/bin/wt"),
            patch("grove.worktree_backend._run_wt_command") as mock_run,
        ):
            result = main(["worktree", "step", "prune", "--dry-run"])

        assert result == 0
        assert "will run: wt step prune --dry-run" in capsys.readouterr().out
        mock_run.assert_not_called()


class TestCliE2EConfigImport:
    def test_import_wt_user_config(self, tmp_path: Path, monkeypatch):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        result = main(["config", "import-wt", "--user"])

        assert result == 0
        loaded = load_toml_file(get_user_config_path())
        assert loaded["commit"]["stage"] == "tracked"

    def test_import_wt_user_dry_run_previews_without_writing(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        result = main(["config", "import-wt", "--user", "--dry-run"])

        assert result == 0
        assert not get_user_config_path().exists()
        assert "Would write" in capsys.readouterr().out

    def test_import_wt_conflict_requires_force(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        target = get_user_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[commit]\nstage = "all"\n')

        result = main(["config", "import-wt", "--user"])

        assert result == 1
        output = capsys.readouterr().out
        assert "Import conflicts" in output
        assert "--force" in output

    def test_import_wt_force_overwrites_conflicts(self, tmp_path: Path, monkeypatch):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        target = get_user_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[commit]\nstage = "all"\n')

        result = main(["config", "import-wt", "--user", "--force"])

        assert result == 0
        loaded = load_toml_file(target)
        assert loaded["commit"]["stage"] == "tracked"


class TestCliE2ESafety:
    def test_remove_dirty_submodule_worktree_without_force_refuses_deletion(
        self,
        tmp_submodule_tree: Path,
        monkeypatch,
        capsys,
    ):
        branch = "e2e-dirty-remove"
        wt_path = tmp_submodule_tree.parent / "e2e-dirty-remove-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", branch, str(wt_path))
        _git(wt_path, "submodule", "update", "--init", "--recursive")
        dirty_file = wt_path / "technical-docs" / "common" / "dirty.txt"
        dirty_file.write_text("dirty\n")
        monkeypatch.chdir(tmp_submodule_tree)

        result = main(["worktree", "remove", branch])

        assert result == 1
        assert wt_path.exists()
        assert "uncommitted changes" in capsys.readouterr().out
