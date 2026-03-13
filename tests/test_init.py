"""Tests for grove.init."""

from types import SimpleNamespace
from pathlib import Path

from grove.init import TEMPLATE, run
from grove.user_config import get_legacy_config_path, get_project_config_path


class TestInitRun:
    def test_creates_config_file(self, tmp_path: Path):
        """Should create .config/grove.toml in the target directory by default."""
        args = SimpleNamespace(path=str(tmp_path), force=False, legacy=False)
        result = run(args)

        assert result == 0
        config = get_project_config_path(tmp_path)
        assert config.exists()
        assert config.read_text() == TEMPLATE

    def test_refuses_overwrite_without_force(self, tmp_path: Path):
        """Should fail if config already exists and --force not given."""
        config = get_project_config_path(tmp_path)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("existing\n")
        args = SimpleNamespace(path=str(tmp_path), force=False, legacy=False)
        result = run(args)

        assert result == 1
        # Original content should be preserved
        assert config.read_text() == "existing\n"

    def test_overwrites_with_force(self, tmp_path: Path):
        """Should overwrite existing config when --force is given."""
        config = get_project_config_path(tmp_path)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("old content\n")
        args = SimpleNamespace(path=str(tmp_path), force=True, legacy=False)
        result = run(args)

        assert result == 0
        assert config.read_text() == TEMPLATE

    def test_legacy_flag_writes_legacy_path(self, tmp_path: Path):
        """--legacy should write .grove.toml instead of .config/grove.toml."""
        args = SimpleNamespace(path=str(tmp_path), force=False, legacy=True)

        result = run(args)

        assert result == 0
        assert get_legacy_config_path(tmp_path).read_text() == TEMPLATE
        assert not get_project_config_path(tmp_path).exists()

    def test_fails_on_nonexistent_directory(self, tmp_path: Path):
        """Should fail if target directory does not exist."""
        nonexistent = tmp_path / "does-not-exist"
        args = SimpleNamespace(path=str(nonexistent), force=False, legacy=False)
        result = run(args)

        assert result == 1
