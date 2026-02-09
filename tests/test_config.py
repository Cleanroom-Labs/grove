"""Tests for grove.config."""

import pytest
from pathlib import Path

from grove.config import (
    CONFIG_FILENAME,
    DEFAULT_COMMIT_MESSAGE,
    MergeConfig,
    GroveConfig,
    SyncGroup,
    load_config,
)


class TestLoadConfig:
    def test_valid_config(self, tmp_path: Path):
        """A well-formed config should load correctly."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-shared-lib"\n'
            'standalone-repo = "/tmp/my-shared-lib"\n'
        )
        config = load_config(tmp_path)
        assert "common" in config.sync_groups
        group = config.sync_groups["common"]
        assert group.name == "common"
        assert group.url_match == "my-shared-lib"
        assert group.standalone_repo == Path("/tmp/my-shared-lib")
        assert group.commit_message == DEFAULT_COMMIT_MESSAGE

    def test_custom_commit_message(self, tmp_path: Path):
        """A custom commit-message should override the default."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.icons]\n'
            'url-match = "my-icons"\n'
            'standalone-repo = "/tmp/my-icons"\n'
            'commit-message = "chore: update {group} to {sha}"\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["icons"]
        assert group.commit_message == "chore: update {group} to {sha}"

    def test_tilde_expansion(self, tmp_path: Path):
        """'~' in standalone-repo should be expanded."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-lib"\n'
            'standalone-repo = "~/Projects/my-lib"\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["common"]
        assert "~" not in str(group.standalone_repo)
        assert group.standalone_repo == Path.home() / "Projects" / "my-lib"

    def test_multiple_groups(self, tmp_path: Path):
        """Multiple sync groups should all be loaded."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-common"\n'
            'standalone-repo = "/tmp/common"\n'
            '\n'
            '[sync-groups.icons]\n'
            'url-match = "my-icons"\n'
            'standalone-repo = "/tmp/icons"\n'
        )
        config = load_config(tmp_path)
        assert len(config.sync_groups) == 2
        assert "common" in config.sync_groups
        assert "icons" in config.sync_groups

    def test_empty_sync_groups(self, tmp_path: Path):
        """A config with no sync-groups section should return empty dict."""
        (tmp_path / CONFIG_FILENAME).write_text("# empty config\n")
        config = load_config(tmp_path)
        assert config.sync_groups == {}

    def test_missing_config_returns_empty(self, tmp_path: Path):
        """A missing config file should return empty config (no sync groups)."""
        config = load_config(tmp_path)
        assert config.sync_groups == {}

    def test_invalid_toml_raises(self, tmp_path: Path):
        """Invalid TOML should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text("not valid toml [[[")
        with pytest.raises(ValueError, match="Invalid TOML"):
            load_config(tmp_path)

    def test_missing_url_match_raises(self, tmp_path: Path):
        """A sync group without url-match should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.bad]\n'
            'standalone-repo = "/tmp/repo"\n'
        )
        with pytest.raises(ValueError, match="url-match"):
            load_config(tmp_path)

    def test_missing_standalone_repo_loads_as_none(self, tmp_path: Path):
        """A sync group without standalone-repo should load with None."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "something"\n'
        )
        config = load_config(tmp_path)
        assert config.sync_groups["common"].standalone_repo is None

    def test_valid_config_without_standalone_repo(self, tmp_path: Path):
        """A config with only url-match should load successfully."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.icons]\n'
            'url-match = "my-icons"\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["icons"]
        assert group.url_match == "my-icons"
        assert group.standalone_repo is None
        assert group.commit_message == DEFAULT_COMMIT_MESSAGE

    def test_allow_drift_loaded(self, tmp_path: Path):
        """allow-drift paths should be loaded into the SyncGroup."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-lib"\n'
            'allow-drift = ["technical-docs/common"]\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["common"]
        assert group.allow_drift == ["technical-docs/common"]

    def test_allow_drift_default_empty(self, tmp_path: Path):
        """Omitted allow-drift should default to empty list."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-lib"\n'
        )
        config = load_config(tmp_path)
        assert config.sync_groups["common"].allow_drift == []

    def test_non_table_group_raises(self, tmp_path: Path):
        """A sync group that is not a table should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups]\n'
            'bad = "not a table"\n'
        )
        with pytest.raises(ValueError, match="expected a table"):
            load_config(tmp_path)


class TestMergeConfig:
    def test_default_merge_config(self, tmp_path: Path):
        """Missing [worktree-merge] section should return defaults."""
        (tmp_path / CONFIG_FILENAME).write_text("# empty\n")
        config = load_config(tmp_path)
        assert config.merge.test_command is None
        assert config.merge.test_overrides == {}

    def test_missing_config_has_default_merge(self, tmp_path: Path):
        """Missing config file should include default merge config."""
        config = load_config(tmp_path)
        assert isinstance(config.merge, MergeConfig)
        assert config.merge.test_command is None

    def test_test_command(self, tmp_path: Path):
        """test-command should be loaded."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert config.merge.test_command == "pytest"

    def test_test_overrides(self, tmp_path: Path):
        """test-overrides should be loaded as a dict."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = "pytest"\n'
            '\n'
            '[worktree-merge.test-overrides]\n'
            '"." = "npm test"\n'
            '"technical-docs" = "make html"\n'
            '"technical-docs/whisper" = ""\n'
        )
        config = load_config(tmp_path)
        assert config.merge.test_overrides["."] == "npm test"
        assert config.merge.test_overrides["technical-docs"] == "make html"
        assert config.merge.test_overrides["technical-docs/whisper"] == ""

    def test_test_overrides_without_default(self, tmp_path: Path):
        """Overrides can exist without a default test-command."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge.test-overrides]\n'
            '"." = "npm test"\n'
        )
        config = load_config(tmp_path)
        assert config.merge.test_command is None
        assert config.merge.test_overrides["."] == "npm test"

    def test_invalid_test_command_type_raises(self, tmp_path: Path):
        """Non-string test-command should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-command = 42\n'
        )
        with pytest.raises(ValueError, match="test-command"):
            load_config(tmp_path)

    def test_invalid_test_overrides_type_raises(self, tmp_path: Path):
        """Non-table test-overrides should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\n'
            'test-overrides = "bad"\n'
        )
        with pytest.raises(ValueError, match="test-overrides"):
            load_config(tmp_path)

    def test_invalid_override_value_raises(self, tmp_path: Path):
        """Non-string override value should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge.test-overrides]\n'
            '"." = 42\n'
        )
        with pytest.raises(ValueError, match="test-overrides"):
            load_config(tmp_path)

    def test_merge_config_alongside_sync_groups(self, tmp_path: Path):
        """Both sections should coexist."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\n'
            'url-match = "my-lib"\n'
            '\n'
            '[worktree-merge]\n'
            'test-command = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert "common" in config.sync_groups
        assert config.merge.test_command == "pytest"
