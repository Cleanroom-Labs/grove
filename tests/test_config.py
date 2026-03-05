"""Tests for grove.config."""

import pytest
from pathlib import Path

from grove.config import (
    CascadeConfig,
    CONFIG_FILENAME,
    DEFAULT_COMMIT_MESSAGE,
    MergeConfig,
    get_sync_group_exclude_paths,
    load_config,
)
from grove.user_config import get_project_config_path, get_user_config_path


class TestLoadConfig:
    def test_valid_config(self, tmp_path: Path):
        """A well-formed config should load correctly."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[sync-groups.common]\n"
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
            "[sync-groups.icons]\n"
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
            "[sync-groups.common]\n"
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
            "[sync-groups.common]\n"
            'url-match = "my-common"\n'
            'standalone-repo = "/tmp/common"\n'
            "\n"
            "[sync-groups.icons]\n"
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
            '[sync-groups.bad]\nstandalone-repo = "/tmp/repo"\n'
        )
        with pytest.raises(ValueError, match="url-match"):
            load_config(tmp_path)

    def test_missing_standalone_repo_loads_as_none(self, tmp_path: Path):
        """A sync group without standalone-repo should load with None."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\nurl-match = "something"\n'
        )
        config = load_config(tmp_path)
        assert config.sync_groups["common"].standalone_repo is None

    def test_valid_config_without_standalone_repo(self, tmp_path: Path):
        """A config with only url-match should load successfully."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.icons]\nurl-match = "my-icons"\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["icons"]
        assert group.url_match == "my-icons"
        assert group.standalone_repo is None
        assert group.commit_message == DEFAULT_COMMIT_MESSAGE

    def test_allow_drift_loaded(self, tmp_path: Path):
        """allow-drift paths should be loaded into the SyncGroup."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[sync-groups.common]\n"
            'url-match = "my-lib"\n'
            'allow-drift = ["technical-docs/common"]\n'
        )
        config = load_config(tmp_path)
        group = config.sync_groups["common"]
        assert group.allow_drift == ["technical-docs/common"]

    def test_allow_drift_default_empty(self, tmp_path: Path):
        """Omitted allow-drift should default to empty list."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\nurl-match = "my-lib"\n'
        )
        config = load_config(tmp_path)
        assert config.sync_groups["common"].allow_drift == []

    def test_non_table_group_raises(self, tmp_path: Path):
        """A sync group that is not a table should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('[sync-groups]\nbad = "not a table"\n')
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
            '[worktree-merge]\ntest-command = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert config.merge.test_command == "pytest"

    def test_test_overrides(self, tmp_path: Path):
        """test-overrides should be loaded as a dict."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[worktree-merge]\n"
            'test-command = "pytest"\n'
            "\n"
            "[worktree-merge.test-overrides]\n"
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
            '[worktree-merge.test-overrides]\n"." = "npm test"\n'
        )
        config = load_config(tmp_path)
        assert config.merge.test_command is None
        assert config.merge.test_overrides["."] == "npm test"

    def test_invalid_test_command_type_raises(self, tmp_path: Path):
        """Non-string test-command should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text("[worktree-merge]\ntest-command = 42\n")
        with pytest.raises(ValueError, match="test-command"):
            load_config(tmp_path)

    def test_invalid_test_overrides_type_raises(self, tmp_path: Path):
        """Non-table test-overrides should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\ntest-overrides = "bad"\n'
        )
        with pytest.raises(ValueError, match="test-overrides"):
            load_config(tmp_path)

    def test_invalid_override_value_raises(self, tmp_path: Path):
        """Non-string override value should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge.test-overrides]\n"." = 42\n'
        )
        with pytest.raises(ValueError, match="test-overrides"):
            load_config(tmp_path)

    def test_merge_config_alongside_sync_groups(self, tmp_path: Path):
        """Both sections should coexist."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[sync-groups.common]\n"
            'url-match = "my-lib"\n'
            "\n"
            "[worktree-merge]\n"
            'test-command = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert "common" in config.sync_groups
        assert config.merge.test_command == "pytest"


class TestWorktreeConfig:
    def test_default_worktree_config(self, tmp_path: Path):
        """Missing [worktree] section should default to copy_venv=False."""
        (tmp_path / CONFIG_FILENAME).write_text("# empty\n")
        config = load_config(tmp_path)
        assert config.worktree.copy_venv is False

    def test_copy_venv_true(self, tmp_path: Path):
        """copy-venv = true should be loaded."""
        (tmp_path / CONFIG_FILENAME).write_text("[worktree]\ncopy-venv = true\n")
        config = load_config(tmp_path)
        assert config.worktree.copy_venv is True

    def test_copy_venv_false(self, tmp_path: Path):
        """Explicit copy-venv = false should remain False."""
        (tmp_path / CONFIG_FILENAME).write_text("[worktree]\ncopy-venv = false\n")
        config = load_config(tmp_path)
        assert config.worktree.copy_venv is False

    def test_non_boolean_copy_venv_raises(self, tmp_path: Path):
        """Non-boolean copy-venv should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('[worktree]\ncopy-venv = "yes"\n')
        with pytest.raises(ValueError, match="copy-venv"):
            load_config(tmp_path)

    def test_non_table_worktree_raises(self, tmp_path: Path):
        """Non-table [worktree] should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('worktree = "bad"\n')
        with pytest.raises(ValueError, match="expected a table"):
            load_config(tmp_path)

    def test_worktree_alongside_other_sections(self, tmp_path: Path):
        """[worktree] should coexist with sync-groups and cascade."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[sync-groups.common]\n"
            'url-match = "my-lib"\n'
            "\n"
            "[worktree]\n"
            "copy-venv = true\n"
            "\n"
            "[cascade]\n"
            'local-tests = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert "common" in config.sync_groups
        assert config.worktree.copy_venv is True
        assert config.cascade.local_tests == "pytest"


class TestCascadeConfig:
    def test_default_cascade_config(self, tmp_path: Path):
        """Missing [cascade] section should return defaults."""
        (tmp_path / CONFIG_FILENAME).write_text("# empty\n")
        config = load_config(tmp_path)
        assert config.cascade.local_tests is None
        assert config.cascade.contract_tests is None
        assert config.cascade.integration_tests is None
        assert config.cascade.system_tests is None
        assert config.cascade.overrides == {}

    def test_all_tiers(self, tmp_path: Path):
        """All four tiers should be loadable."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[cascade]\n"
            'local-tests = "pytest tests/unit"\n'
            'contract-tests = "pytest tests/contracts"\n'
            'integration-tests = "pytest tests/integration"\n'
            'system-tests = "pytest tests/system"\n'
        )
        config = load_config(tmp_path)
        assert config.cascade.local_tests == "pytest tests/unit"
        assert config.cascade.contract_tests == "pytest tests/contracts"
        assert config.cascade.integration_tests == "pytest tests/integration"
        assert config.cascade.system_tests == "pytest tests/system"

    def test_partial_tiers(self, tmp_path: Path):
        """Only configured tiers should be set; others remain None."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[cascade]\nlocal-tests = "pytest"\nsystem-tests = "make e2e"\n'
        )
        config = load_config(tmp_path)
        assert config.cascade.local_tests == "pytest"
        assert config.cascade.contract_tests is None
        assert config.cascade.integration_tests is None
        assert config.cascade.system_tests == "make e2e"

    def test_local_tests_fallback_to_merge(self, tmp_path: Path):
        """local-tests should fall back to worktree-merge.test-command."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree-merge]\ntest-command = "pytest"\n'
        )
        config = load_config(tmp_path)
        assert config.cascade.local_tests == "pytest"

    def test_explicit_local_tests_overrides_merge_fallback(self, tmp_path: Path):
        """Explicit local-tests should take precedence over merge fallback."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[worktree-merge]\n"
            'test-command = "pytest"\n'
            "[cascade]\n"
            'local-tests = "make test-unit"\n'
        )
        config = load_config(tmp_path)
        assert config.cascade.local_tests == "make test-unit"

    def test_per_repo_overrides(self, tmp_path: Path):
        """Per-repo overrides should be parsed correctly."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[cascade]\n"
            'local-tests = "pytest"\n'
            "\n"
            '[cascade.overrides."services/api"]\n'
            'local-tests = "npm test -- --unit"\n'
            'integration-tests = "npm test -- --integration"\n'
        )
        config = load_config(tmp_path)
        overrides = config.cascade.overrides
        assert "services/api" in overrides
        assert overrides["services/api"]["local-tests"] == "npm test -- --unit"
        assert (
            overrides["services/api"]["integration-tests"]
            == "npm test -- --integration"
        )

    def test_dot_repo_override(self, tmp_path: Path):
        """Root repo override with '.' should work."""
        (tmp_path / CONFIG_FILENAME).write_text(
            "[cascade]\n"
            'local-tests = "pytest"\n'
            "\n"
            '[cascade.overrides."."]\n'
            'local-tests = "make test-unit"\n'
            'system-tests = "make test-e2e"\n'
        )
        config = load_config(tmp_path)
        assert config.cascade.overrides["."]["local-tests"] == "make test-unit"
        assert config.cascade.overrides["."]["system-tests"] == "make test-e2e"

    def test_unknown_tier_in_overrides_raises(self, tmp_path: Path):
        """Unknown tier name in overrides should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[cascade.overrides."services/api"]\nunit-tests = "pytest"\n'
        )
        with pytest.raises(ValueError, match="unknown tier"):
            load_config(tmp_path)

    def test_non_string_tier_raises(self, tmp_path: Path):
        """Non-string tier value should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text("[cascade]\nlocal-tests = 42\n")
        with pytest.raises(ValueError, match="expected a string"):
            load_config(tmp_path)

    def test_non_table_cascade_raises(self, tmp_path: Path):
        """Non-table [cascade] should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('cascade = "bad"\n')
        with pytest.raises(ValueError, match="expected a table"):
            load_config(tmp_path)

    def test_non_table_overrides_raises(self, tmp_path: Path):
        """Non-table cascade.overrides should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('[cascade]\noverrides = "bad"\n')
        with pytest.raises(ValueError, match="expected a table"):
            load_config(tmp_path)

    def test_non_string_override_value_raises(self, tmp_path: Path):
        """Non-string value in repo overrides should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[cascade.overrides."services/api"]\nlocal-tests = 42\n'
        )
        with pytest.raises(ValueError, match="expected a string"):
            load_config(tmp_path)


class TestCascadeConfigGetCommand:
    def test_returns_default_tier(self):
        """get_command should return the default tier command."""
        config = CascadeConfig(local_tests="pytest")
        assert config.get_command("local-tests", "libs/common") == "pytest"

    def test_override_takes_precedence(self):
        """Per-repo override should take precedence over default."""
        config = CascadeConfig(
            local_tests="pytest",
            overrides={"libs/common": {"local-tests": "make test"}},
        )
        assert config.get_command("local-tests", "libs/common") == "make test"

    def test_unconfigured_tier_returns_none(self):
        """Unconfigured tier should return None."""
        config = CascadeConfig()
        assert config.get_command("contract-tests", "libs/common") is None

    def test_override_for_different_repo_not_applied(self):
        """Override for one repo should not affect another."""
        config = CascadeConfig(
            local_tests="pytest",
            overrides={"services/api": {"local-tests": "npm test"}},
        )
        assert config.get_command("local-tests", "libs/common") == "pytest"

    def test_all_tiers_resolvable(self):
        """All four tiers should be resolvable via get_command."""
        config = CascadeConfig(
            local_tests="lt",
            contract_tests="ct",
            integration_tests="it",
            system_tests="st",
        )
        for tier, expected in [
            ("local-tests", "lt"),
            ("contract-tests", "ct"),
            ("integration-tests", "it"),
            ("system-tests", "st"),
        ]:
            assert config.get_command(tier, ".") == expected


class TestGetSyncGroupExcludePaths:
    def test_returns_sync_submodule_paths(self, tmp_submodule_tree: Path):
        """Should return paths of submodules matching sync-group url-match."""
        config = load_config(tmp_submodule_tree)
        paths = get_sync_group_exclude_paths(tmp_submodule_tree, config)
        # The tree has a "common" sync group matching grandchild_origin
        common_path = tmp_submodule_tree / "technical-docs" / "common"
        assert common_path in paths

    def test_empty_when_no_sync_groups(self, tmp_git_repo: Path):
        """Should return empty set when no sync groups are configured."""
        config = load_config(tmp_git_repo)
        paths = get_sync_group_exclude_paths(tmp_git_repo, config)
        assert paths == set()


class TestAliasConfig:
    def test_default_empty_aliases(self, tmp_path: Path):
        """Missing [aliases] section should default to empty mapping."""
        (tmp_path / CONFIG_FILENAME).write_text("# empty\n")
        config = load_config(tmp_path)
        assert config.aliases.mapping == {}

    def test_aliases_parsed(self, tmp_path: Path):
        """[aliases] section should be parsed into mapping dict."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[aliases]\nwm = "worktree merge"\nc = "check"\n'
        )
        config = load_config(tmp_path)
        assert config.aliases.mapping == {"wm": "worktree merge", "c": "check"}

    def test_non_string_value_raises(self, tmp_path: Path):
        """Non-string alias value should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text("[aliases]\nbad = 42\n")
        with pytest.raises(ValueError, match="aliases.bad"):
            load_config(tmp_path)

    def test_non_table_aliases_raises(self, tmp_path: Path):
        """Non-table [aliases] should raise ValueError."""
        (tmp_path / CONFIG_FILENAME).write_text('aliases = "bad"\n')
        with pytest.raises(ValueError, match="expected a table"):
            load_config(tmp_path)


class TestConfigPrecedence:
    def test_user_and_project_configs_are_merged(self, tmp_path: Path):
        """Project config should override user config while keeping unspecified keys."""
        user_path = get_user_config_path()
        user_path.parent.mkdir(parents=True, exist_ok=True)
        user_path.write_text(
            "[worktree]\n"
            'backend = "wt"\n'
            "copy-venv = true\n"
            "\n"
            "[commit]\n"
            'stage = "tracked"\n'
        )

        project_path = get_project_config_path(tmp_path)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text("[worktree]\ncopy-venv = false\n")

        config = load_config(tmp_path)
        assert config.worktree.copy_venv is False
        assert config.worktree.backend == "wt"
        assert config.commit.stage == "tracked"

    def test_legacy_config_fallback_warns(self, tmp_path: Path, capsys):
        """When only .grove.toml exists, it should be loaded with a warning."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.common]\nurl-match = "my-lib"\n'
        )

        config = load_config(tmp_path)
        captured = capsys.readouterr()

        assert "common" in config.sync_groups
        assert "deprecated legacy config" in captured.err

    def test_project_config_wins_over_legacy(self, tmp_path: Path, capsys):
        """When project config exists, legacy config should be ignored."""
        (tmp_path / CONFIG_FILENAME).write_text(
            '[sync-groups.legacy]\nurl-match = "legacy-lib"\n'
        )

        project_path = get_project_config_path(tmp_path)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text('[sync-groups.project]\nurl-match = "project-lib"\n')

        config = load_config(tmp_path)
        captured = capsys.readouterr()

        assert "project" in config.sync_groups
        assert "legacy" not in config.sync_groups
        assert "ignoring deprecated legacy config" in captured.err

    def test_explicit_override_is_highest_precedence(self, tmp_path: Path, monkeypatch):
        """GROVE_CONFIG_PATH should have highest precedence."""
        user_path = get_user_config_path()
        user_path.parent.mkdir(parents=True, exist_ok=True)
        user_path.write_text("[worktree]\ncopy-venv = false\n")

        project_path = get_project_config_path(tmp_path)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text("[worktree]\ncopy-venv = false\n")

        explicit = tmp_path / "override.toml"
        explicit.write_text("[worktree]\ncopy-venv = true\n")
        monkeypatch.setenv("GROVE_CONFIG_PATH", str(explicit))

        config = load_config(tmp_path)
        assert config.worktree.copy_venv is True


class TestExtendedSections:
    def test_parse_commit_generation(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            "[commit]\n"
            'stage = "all"\n'
            "\n"
            "[commit.generation]\n"
            'command = "echo from-llm"\n'
        )
        config = load_config(tmp_path)
        assert config.commit.stage == "all"
        assert config.commit.generation.command == "echo from-llm"

    def test_parse_worktree_llm_providers(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            "[worktree.llm]\n"
            "providers = [\n"
            '  { provider = "anthropic", model = "claude-haiku-4-20250514" },\n'
            '  { provider = "ollama", model = "qwen3:4b" },\n'
            "]\n"
        )
        config = load_config(tmp_path)
        assert len(config.worktree.llm.providers) == 2
        assert config.worktree.llm.providers[0].provider == "anthropic"
        assert config.worktree.llm.providers[1].provider == "ollama"

    def test_invalid_llm_provider_raises(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            '[worktree.llm]\nproviders = [{ provider = "bad", model = "x" }]\n'
        )
        with pytest.raises(ValueError, match="must be one of"):
            load_config(tmp_path)

    def test_parse_hook_string_shorthand(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text('post-create = "npm install"\n')
        config = load_config(tmp_path)
        assert config.hooks["post-create"].commands["default"] == "npm install"

    def test_parse_nested_hooks_table(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text('[hooks.pre-merge]\nci = "pytest -q"\n')
        config = load_config(tmp_path)
        assert config.hooks["pre-merge"].commands["ci"] == "pytest -q"

    def test_unknown_hook_type_raises(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            '[hooks.unknown-hook]\nrun = "echo nope"\n'
        )
        with pytest.raises(ValueError, match="unknown hook type"):
            load_config(tmp_path)
