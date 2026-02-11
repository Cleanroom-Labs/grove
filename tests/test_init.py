"""Tests for grove.init."""

from types import SimpleNamespace
from pathlib import Path

from grove.config import CONFIG_FILENAME
from grove.init import TEMPLATE, run


class TestInitRun:
    def test_creates_config_file(self, tmp_path: Path):
        """Should create .grove.toml in the target directory."""
        args = SimpleNamespace(path=str(tmp_path), force=False)
        result = run(args)

        assert result == 0
        config = tmp_path / CONFIG_FILENAME
        assert config.exists()
        assert config.read_text() == TEMPLATE

    def test_refuses_overwrite_without_force(self, tmp_path: Path):
        """Should fail if config already exists and --force not given."""
        (tmp_path / CONFIG_FILENAME).write_text("existing\n")
        args = SimpleNamespace(path=str(tmp_path), force=False)
        result = run(args)

        assert result == 1
        # Original content should be preserved
        assert (tmp_path / CONFIG_FILENAME).read_text() == "existing\n"

    def test_overwrites_with_force(self, tmp_path: Path):
        """Should overwrite existing config when --force is given."""
        (tmp_path / CONFIG_FILENAME).write_text("old content\n")
        args = SimpleNamespace(path=str(tmp_path), force=True)
        result = run(args)

        assert result == 0
        assert (tmp_path / CONFIG_FILENAME).read_text() == TEMPLATE

    def test_fails_on_nonexistent_directory(self, tmp_path: Path):
        """Should fail if target directory does not exist."""
        nonexistent = tmp_path / "does-not-exist"
        args = SimpleNamespace(path=str(nonexistent), force=False)
        result = run(args)

        assert result == 1
