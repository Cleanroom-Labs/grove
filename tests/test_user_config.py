"""Tests for grove.user_config."""

from pathlib import Path

from grove.user_config import (
    dump_toml,
    get_legacy_config_path,
    get_project_config_path,
    get_user_config_path,
    iter_grove_config_paths,
    load_toml_file,
    merge_dicts,
)


class TestPathResolution:
    def test_get_user_config_path_uses_config_home(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("GROVE_CONFIG_HOME", str(tmp_path / "cfg"))
        assert get_user_config_path() == tmp_path / "cfg" / "config.toml"

    def test_iter_paths_uses_legacy_only_as_fallback(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("GROVE_CONFIG_HOME", str(tmp_path / "cfg"))
        legacy = get_legacy_config_path(tmp_path)
        legacy.write_text("# legacy\n")

        paths = iter_grove_config_paths(tmp_path)
        assert get_project_config_path(tmp_path) in paths
        assert legacy in paths

    def test_iter_paths_omits_legacy_when_project_exists(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.setenv("GROVE_CONFIG_HOME", str(tmp_path / "cfg"))
        legacy = get_legacy_config_path(tmp_path)
        legacy.write_text("# legacy\n")
        project = get_project_config_path(tmp_path)
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text("# project\n")

        paths = iter_grove_config_paths(tmp_path)
        assert project in paths
        assert legacy not in paths

    def test_iter_paths_appends_explicit_override(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("GROVE_CONFIG_HOME", str(tmp_path / "cfg"))
        override = tmp_path / "override.toml"
        monkeypatch.setenv("GROVE_CONFIG_PATH", str(override))
        paths = iter_grove_config_paths(tmp_path)
        assert paths[-1] == override


class TestTomlHelpers:
    def test_load_toml_file(self, tmp_path: Path):
        config_path = tmp_path / "config.toml"
        config_path.write_text("[worktree]\ncopy-venv = true\n")
        assert load_toml_file(config_path) == {"worktree": {"copy-venv": True}}

    def test_merge_dicts_deep_merge_and_false_override(self):
        base = {"worktree": {"copy-venv": True, "backend": "wt"}}
        override = {"worktree": {"copy-venv": False}}
        assert merge_dicts(base, override) == {
            "worktree": {"copy-venv": False, "backend": "wt"}
        }

    def test_dump_toml_renders_nested_tables_and_quoted_keys(self):
        rendered = dump_toml(
            {
                "worktree-path": "../{{ branch }}",
                "pre-merge": {"team/service": "cargo test"},
            }
        )
        assert 'worktree-path = "../{{ branch }}"' in rendered
        assert "[pre-merge]" in rendered
        assert '"team/service" = "cargo test"' in rendered
