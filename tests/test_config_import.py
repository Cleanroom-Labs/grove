"""Tests for grove.config_import."""

from pathlib import Path
from types import SimpleNamespace

from grove.config_import import _report_conflicts, _translate_wt_to_grove, run
from grove.user_config import (
    get_project_config_path,
    get_user_config_path,
    load_toml_file,
)


class TestConfigImport:
    def test_imports_user_config_dry_run(self, capsys, monkeypatch, tmp_path: Path):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        args = SimpleNamespace(user=True, project=False, dry_run=True, force=False)
        result = run(args)

        assert result == 0
        assert not get_user_config_path().exists()
        captured = capsys.readouterr()
        assert "Would write" in captured.out
        assert 'stage = "tracked"' in captured.out

    def test_imports_user_config_into_existing_grove_config(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        target = get_user_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[worktree]\ncopy-venv = true\n")

        args = SimpleNamespace(user=True, project=False, dry_run=False, force=False)
        result = run(args)

        assert result == 0
        loaded = load_toml_file(target)
        assert loaded["worktree"]["copy-venv"] is True
        assert loaded["commit"]["stage"] == "tracked"

    def test_imports_project_config(self, monkeypatch, tmp_git_repo: Path):
        source = tmp_git_repo / ".config" / "wt.toml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("[list]\nfull = true\n")
        monkeypatch.chdir(tmp_git_repo)

        args = SimpleNamespace(user=False, project=True, dry_run=False, force=False)
        result = run(args)

        assert result == 0
        loaded = load_toml_file(get_project_config_path(tmp_git_repo))
        assert loaded["list"]["full"] is True

    def test_imports_commit_generation_command(
        self, capsys, monkeypatch, tmp_path: Path
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit.generation]\ncommand = "wt msg --prompt -"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        args = SimpleNamespace(user=True, project=False, dry_run=False, force=False)
        result = run(args)

        assert result == 0
        loaded = load_toml_file(get_user_config_path())
        assert loaded["commit"]["generation"]["command"] == "wt msg --prompt -"
        assert "Imported" in capsys.readouterr().out

    def test_report_conflicts_detects_changed_scalar_fields(self):
        existing = {
            "commit": {"stage": "all"},
            "worktree": {"backend": "native"},
        }
        incoming = {
            "commit": {"stage": "tracked"},
            "worktree": {"backend": "native"},
        }

        conflicts = _report_conflicts(existing, incoming)

        assert conflicts == ["commit.stage: existing='all', incoming='tracked'"]

    def test_translate_wt_to_grove_preserves_known_sections(self):
        raw = {
            "worktree": {"backend": "auto"},
            "commit": {"stage": "tracked"},
            "hooks": {"pre-commit": {"check": "pytest"}},
        }

        translated = _translate_wt_to_grove(raw)

        assert translated == raw
        assert translated is not raw

    def test_import_conflicts_require_force(
        self,
        capsys,
        monkeypatch,
        tmp_path: Path,
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        target = get_user_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[commit]\nstage = "all"\n')

        args = SimpleNamespace(user=True, project=False, dry_run=False, force=False)
        result = run(args)

        assert result == 1
        assert load_toml_file(target)["commit"]["stage"] == "all"
        output = capsys.readouterr().out
        assert "Import conflicts" in output
        assert "commit.stage" in output
        assert "--force" in output

    def test_import_force_replaces_target_config(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        source = tmp_path / "wt-user.toml"
        source.write_text('[commit]\nstage = "tracked"\n')
        monkeypatch.setenv("WORKTRUNK_CONFIG_PATH", str(source))

        target = get_user_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[worktree]\ncopy-venv = true\n[commit]\nstage = "all"\n')

        args = SimpleNamespace(user=True, project=False, dry_run=False, force=True)
        result = run(args)

        assert result == 0
        loaded = load_toml_file(target)
        assert loaded["commit"]["stage"] == "tracked"
        assert "worktree" not in loaded
