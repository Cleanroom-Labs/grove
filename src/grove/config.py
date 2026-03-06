"""
grove/config.py
Load and validate Grove configuration.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from grove.user_config import (
    get_legacy_config_path,
    get_project_config_path,
    iter_grove_config_paths,
    load_toml_file,
    merge_dicts,
)

CONFIG_FILENAME = ".grove.toml"
DEFAULT_COMMIT_MESSAGE = "chore: sync {group} submodule to {sha}"
VALID_BACKENDS = ("auto", "native", "wt")
VALID_LLM_PROVIDERS = ("anthropic", "ollama", "openai", "litellm")
VALID_STAGE_VALUES = ("all", "tracked", "none")
HOOK_TYPES = (
    "pre-switch",
    "post-create",
    "post-start",
    "post-switch",
    "pre-commit",
    "pre-merge",
    "post-merge",
    "pre-remove",
    "post-remove",
)
_warned_legacy_paths: set[Path] = set()


@dataclass
class SyncGroup:
    """A group of submodules that should all be at the same commit."""

    name: str
    url_match: str
    standalone_repo: Path | None = None
    commit_message: str = DEFAULT_COMMIT_MESSAGE
    allow_drift: list[str] = field(default_factory=list)


@dataclass
class MergeConfig:
    """Configuration for ``grove worktree merge``."""

    test_command: str | None = None
    test_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMProviderEntry:
    """A single provider in the LLM fallback chain."""

    provider: str
    model: str


@dataclass
class LLMConfig:
    """LLM configuration for commit/squash message generation."""

    providers: list[LLMProviderEntry] = field(default_factory=list)


@dataclass
class WorktreeConfig:
    """Configuration for worktree lifecycle commands."""

    copy_venv: bool = False
    backend: str = "auto"
    worktree_path: str | None = None
    llm: LLMConfig = field(default_factory=LLMConfig)


@dataclass
class ListConfig:
    """Configuration for worktree listing."""

    full: bool = False
    branches: bool = False
    remotes: bool = False
    url: str | None = None


@dataclass
class CommitGenerationConfig:
    """Configuration for generated commit/squash messages."""

    command: str | None = None


@dataclass
class CommitConfig:
    """Configuration for worktree step commit/squash flows."""

    stage: str = "all"
    generation: CommitGenerationConfig = field(default_factory=CommitGenerationConfig)


@dataclass
class LifecycleMergeConfig:
    """Configuration for worktree lifecycle merge defaults."""

    squash: bool = True
    commit: bool = True
    rebase: bool = True
    remove: bool = True
    verify: bool = True


@dataclass
class CIConfig:
    """Configuration for CI integrations used by worktree lifecycle commands."""

    platform: str | None = None


@dataclass
class HookSection:
    """Named commands for a single hook type."""

    commands: dict[str, str] = field(default_factory=dict)


# The four cascade test tiers, in execution order.
CASCADE_TIERS = ("local-tests", "contract-tests", "integration-tests", "system-tests")


@dataclass
class CascadeConfig:
    """Configuration for ``grove cascade``."""

    local_tests: str | None = None
    contract_tests: str | None = None
    integration_tests: str | None = None
    system_tests: str | None = None
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def get_command(self, tier: str, repo_rel_path: str) -> str | None:
        """Resolve the test command for a tier and repo, checking overrides first."""
        repo_overrides = self.overrides.get(repo_rel_path, {})
        if tier in repo_overrides:
            return repo_overrides[tier]
        attr = tier.replace("-", "_")
        return getattr(self, attr, None)


@dataclass
class AliasConfig:
    """Command aliases mapping short names to full command strings."""

    mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class GroveConfig:
    """Top-level configuration loaded from Grove config files."""

    sync_groups: dict[str, SyncGroup] = field(default_factory=dict)
    merge: MergeConfig = field(default_factory=MergeConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    list: ListConfig = field(default_factory=ListConfig)
    commit: CommitConfig = field(default_factory=CommitConfig)
    lifecycle_merge: LifecycleMergeConfig = field(default_factory=LifecycleMergeConfig)
    ci: CIConfig = field(default_factory=CIConfig)
    hooks: dict[str, HookSection] = field(default_factory=dict)
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
    aliases: AliasConfig = field(default_factory=AliasConfig)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_llm_config(section_raw: dict, *, context: str = "worktree") -> LLMConfig:
    """Parse ``llm`` from a TOML dict into an ``LLMConfig``."""
    llm_raw = section_raw.get("llm", {})
    if not isinstance(llm_raw, dict):
        raise ValueError(
            f"{context}.llm: expected a table, got {type(llm_raw).__name__}"
        )

    providers_raw = llm_raw.get("providers", [])
    if not isinstance(providers_raw, list):
        raise ValueError(
            f"{context}.llm.providers: expected an array, got {type(providers_raw).__name__}"
        )

    providers: list[LLMProviderEntry] = []
    for index, entry in enumerate(providers_raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{context}.llm.providers[{index}]: expected a table, "
                f"got {type(entry).__name__}"
            )
        provider = entry.get("provider")
        if not isinstance(provider, str):
            raise ValueError(
                f"{context}.llm.providers[{index}].provider: expected a string"
            )
        if provider not in VALID_LLM_PROVIDERS:
            raise ValueError(
                f"{context}.llm.providers[{index}].provider: must be one of "
                f"{', '.join(VALID_LLM_PROVIDERS)}, got {provider!r}"
            )
        model = entry.get("model")
        if not isinstance(model, str):
            raise ValueError(
                f"{context}.llm.providers[{index}].model: expected a string"
            )
        providers.append(LLMProviderEntry(provider=provider, model=model))

    return LLMConfig(providers=providers)


def _parse_cascade_section(raw: dict, merge: MergeConfig) -> CascadeConfig:
    """Parse the ``[cascade]`` section from raw TOML data."""
    cascade_raw = raw.get("cascade", {})
    if not isinstance(cascade_raw, dict):
        raise ValueError(f"cascade: expected a table, got {type(cascade_raw).__name__}")

    tier_values: dict[str, str | None] = {}
    for tier in CASCADE_TIERS:
        value = cascade_raw.get(tier)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"cascade.{tier}: expected a string, got {type(value).__name__}"
            )
        tier_values[tier] = value

    # Fallback: local-tests inherits from worktree-merge.test-command
    if tier_values["local-tests"] is None and merge.test_command is not None:
        tier_values["local-tests"] = merge.test_command

    overrides: dict[str, dict[str, str]] = {}
    overrides_raw = cascade_raw.get("overrides", {})
    if not isinstance(overrides_raw, dict):
        raise ValueError("cascade.overrides: expected a table")

    for repo_path, repo_data in overrides_raw.items():
        if not isinstance(repo_data, dict):
            raise ValueError(
                f"cascade.overrides.{repo_path}: expected a table, "
                f"got {type(repo_data).__name__}"
            )
        repo_overrides: dict[str, str] = {}
        for key, value in repo_data.items():
            if key not in CASCADE_TIERS:
                raise ValueError(
                    f"cascade.overrides.{repo_path}.{key}: unknown tier "
                    f"(expected one of {', '.join(CASCADE_TIERS)})"
                )
            if not isinstance(value, str):
                raise ValueError(
                    f"cascade.overrides.{repo_path}.{key}: expected a string, "
                    f"got {type(value).__name__}"
                )
            repo_overrides[key] = value
        overrides[repo_path] = repo_overrides

    return CascadeConfig(
        local_tests=tier_values["local-tests"],
        contract_tests=tier_values["contract-tests"],
        integration_tests=tier_values["integration-tests"],
        system_tests=tier_values["system-tests"],
        overrides=overrides,
    )


def _warn_legacy_config_usage(repo_root: Path) -> None:
    """Warn once when the deprecated legacy project config is present."""
    legacy_path = get_legacy_config_path(repo_root)
    if not legacy_path.exists() or legacy_path in _warned_legacy_paths:
        return

    project_path = get_project_config_path(repo_root)
    if project_path.exists():
        print(
            "Warning: ignoring deprecated legacy config "
            f"{legacy_path}; using {project_path}.",
            file=sys.stderr,
        )
    else:
        print(
            "Warning: using deprecated legacy config "
            f"{legacy_path}; migrate to {project_path}.",
            file=sys.stderr,
        )

    _warned_legacy_paths.add(legacy_path)


def _load_raw_config(repo_root: Path) -> dict:
    """Load and merge user, project, and legacy Grove config files."""
    raw: dict = {}
    _warn_legacy_config_usage(repo_root)

    for path in iter_grove_config_paths(repo_root):
        if not path.exists():
            continue
        try:
            loaded = load_toml_file(path)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML in {path}: {e}") from e
        raw = merge_dicts(raw, loaded)

    return raw


def _parse_sync_groups(raw: dict) -> dict[str, SyncGroup]:
    sync_groups: dict[str, SyncGroup] = {}
    for name, group_data in raw.get("sync-groups", {}).items():
        if not isinstance(group_data, dict):
            raise ValueError(
                f"sync-groups.{name}: expected a table, got {type(group_data).__name__}"
            )

        url_match = group_data.get("url-match")
        if not url_match:
            raise ValueError(f"sync-groups.{name}: 'url-match' is required")

        standalone_repo_str = group_data.get("standalone-repo")
        standalone_repo = (
            Path(standalone_repo_str).expanduser() if standalone_repo_str else None
        )

        commit_message = group_data.get("commit-message", DEFAULT_COMMIT_MESSAGE)

        allow_drift_raw = group_data.get("allow-drift", [])
        if not isinstance(allow_drift_raw, list) or not all(
            isinstance(path, str) for path in allow_drift_raw
        ):
            raise ValueError(
                f"sync-groups.{name}: 'allow-drift' must be a list of strings"
            )

        sync_groups[name] = SyncGroup(
            name=name,
            url_match=url_match,
            standalone_repo=standalone_repo,
            commit_message=commit_message,
            allow_drift=allow_drift_raw,
        )

    return sync_groups


def _parse_merge_config(raw: dict) -> MergeConfig:
    merge_raw = raw.get("worktree-merge", {})
    if not isinstance(merge_raw, dict):
        raise ValueError(
            f"worktree-merge: expected a table, got {type(merge_raw).__name__}"
        )

    test_command = merge_raw.get("test-command")
    if test_command is not None and not isinstance(test_command, str):
        raise ValueError("worktree-merge.test-command: expected a string")

    test_overrides_raw = merge_raw.get("test-overrides", {})
    if not isinstance(test_overrides_raw, dict):
        raise ValueError("worktree-merge.test-overrides: expected a table")
    for key, value in test_overrides_raw.items():
        if not isinstance(value, str):
            raise ValueError(
                f"worktree-merge.test-overrides.{key}: expected a string, "
                f"got {type(value).__name__}"
            )

    return MergeConfig(
        test_command=test_command,
        test_overrides=dict(test_overrides_raw),
    )


def _parse_worktree_section(raw: dict) -> WorktreeConfig:
    worktree_raw = raw.get("worktree", {})
    if not isinstance(worktree_raw, dict):
        raise ValueError(
            f"worktree: expected a table, got {type(worktree_raw).__name__}"
        )

    copy_venv = worktree_raw.get("copy-venv", False)
    if not isinstance(copy_venv, bool):
        raise ValueError(
            f"worktree.copy-venv: expected a boolean, got {type(copy_venv).__name__}"
        )

    backend = worktree_raw.get("backend", "auto")
    if not isinstance(backend, str):
        raise ValueError(
            f"worktree.backend: expected a string, got {type(backend).__name__}"
        )
    if backend not in VALID_BACKENDS:
        raise ValueError(
            f"worktree.backend: expected one of {', '.join(VALID_BACKENDS)}, got {backend!r}"
        )

    nested_worktree_path = worktree_raw.get("worktree-path")
    worktree_path = (
        nested_worktree_path
        if nested_worktree_path is not None
        else raw.get("worktree-path")
    )
    if worktree_path is not None and not isinstance(worktree_path, str):
        key_name = (
            "worktree.worktree-path"
            if nested_worktree_path is not None
            else "worktree-path"
        )
        raise ValueError(
            f"{key_name}: expected a string, got {type(worktree_path).__name__}"
        )

    llm = parse_llm_config(worktree_raw)

    return WorktreeConfig(
        copy_venv=copy_venv,
        backend=backend,
        worktree_path=worktree_path,
        llm=llm,
    )


def _parse_list_section(raw: dict) -> ListConfig:
    list_raw = raw.get("list", {})
    if not isinstance(list_raw, dict):
        raise ValueError(f"list: expected a table, got {type(list_raw).__name__}")

    config = ListConfig()
    for key in ("full", "branches", "remotes"):
        value = list_raw.get(key, getattr(config, key))
        if not isinstance(value, bool):
            raise ValueError(f"list.{key}: expected a boolean")
        setattr(config, key, value)

    url = list_raw.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"list.url: expected a string, got {type(url).__name__}")
    config.url = url
    return config


def _parse_commit_section(raw: dict) -> CommitConfig:
    commit_raw = raw.get("commit", {})
    if not isinstance(commit_raw, dict):
        raise ValueError(f"commit: expected a table, got {type(commit_raw).__name__}")

    stage = commit_raw.get("stage", "all")
    if not isinstance(stage, str):
        raise ValueError(f"commit.stage: expected a string, got {type(stage).__name__}")
    if stage not in VALID_STAGE_VALUES:
        raise ValueError(
            f"commit.stage: expected one of {', '.join(VALID_STAGE_VALUES)}, got {stage!r}"
        )

    generation_raw = commit_raw.get("generation", {})
    if not isinstance(generation_raw, dict):
        raise ValueError(
            f"commit.generation: expected a table, got {type(generation_raw).__name__}"
        )
    command = generation_raw.get("command")
    if command is not None and not isinstance(command, str):
        raise ValueError(
            f"commit.generation.command: expected a string, got {type(command).__name__}"
        )

    return CommitConfig(
        stage=stage,
        generation=CommitGenerationConfig(command=command),
    )


def _parse_lifecycle_merge_section(raw: dict) -> LifecycleMergeConfig:
    merge_raw = raw.get("merge", {})
    if not isinstance(merge_raw, dict):
        raise ValueError(f"merge: expected a table, got {type(merge_raw).__name__}")

    config = LifecycleMergeConfig()
    for key in ("squash", "commit", "rebase", "remove", "verify"):
        value = merge_raw.get(key, getattr(config, key))
        if not isinstance(value, bool):
            raise ValueError(f"merge.{key}: expected a boolean")
        setattr(config, key, value)
    return config


def _parse_ci_section(raw: dict) -> CIConfig:
    ci_raw = raw.get("ci", {})
    if not isinstance(ci_raw, dict):
        raise ValueError(f"ci: expected a table, got {type(ci_raw).__name__}")

    platform = ci_raw.get("platform")
    if platform is not None and not isinstance(platform, str):
        raise ValueError(
            f"ci.platform: expected a string, got {type(platform).__name__}"
        )
    return CIConfig(platform=platform)


def _parse_hooks(raw: dict) -> dict[str, HookSection]:
    hooks: dict[str, HookSection] = {}

    def parse_hook_entry(hook_type: str, hook_raw: object) -> None:
        if isinstance(hook_raw, str):
            hooks[hook_type] = HookSection(commands={"default": hook_raw})
            return
        if not isinstance(hook_raw, dict):
            raise ValueError(
                f"{hook_type}: expected a string or table, got {type(hook_raw).__name__}"
            )
        commands: dict[str, str] = {}
        for key, value in hook_raw.items():
            if not isinstance(value, str):
                raise ValueError(
                    f"{hook_type}.{key}: expected a string, got {type(value).__name__}"
                )
            commands[key] = value
        hooks[hook_type] = HookSection(commands=commands)

    nested_hooks_raw = raw.get("hooks", {})
    if nested_hooks_raw is not None:
        if not isinstance(nested_hooks_raw, dict):
            raise ValueError(
                f"hooks: expected a table, got {type(nested_hooks_raw).__name__}"
            )
        for hook_type, hook_raw in nested_hooks_raw.items():
            if hook_type not in HOOK_TYPES:
                raise ValueError(
                    f"hooks.{hook_type}: unknown hook type "
                    f"(expected one of {', '.join(HOOK_TYPES)})"
                )
            parse_hook_entry(hook_type, hook_raw)

    for hook_type in HOOK_TYPES:
        if hook_type in raw:
            parse_hook_entry(hook_type, raw[hook_type])

    return hooks


def _parse_aliases_config(raw: dict) -> AliasConfig:
    aliases_raw = raw.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        raise ValueError(f"aliases: expected a table, got {type(aliases_raw).__name__}")
    for key, value in aliases_raw.items():
        if not isinstance(value, str):
            raise ValueError(
                f"aliases.{key}: expected a string, got {type(value).__name__}"
            )
    return AliasConfig(mapping=dict(aliases_raw))


def _build_config_from_raw(raw: dict) -> GroveConfig:
    sync_groups = _parse_sync_groups(raw)
    merge = _parse_merge_config(raw)
    worktree = _parse_worktree_section(raw)
    list_config = _parse_list_section(raw)
    commit = _parse_commit_section(raw)
    lifecycle_merge = _parse_lifecycle_merge_section(raw)
    ci = _parse_ci_section(raw)
    hooks = _parse_hooks(raw)
    cascade = _parse_cascade_section(raw, merge)
    aliases = _parse_aliases_config(raw)

    return GroveConfig(
        sync_groups=sync_groups,
        merge=merge,
        worktree=worktree,
        list=list_config,
        commit=commit,
        lifecycle_merge=lifecycle_merge,
        ci=ci,
        hooks=hooks,
        cascade=cascade,
        aliases=aliases,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(repo_root: Path) -> GroveConfig:
    """Load Grove config from user, project, and legacy config files.

    Precedence is:
    1. ``~/.config/grove/config.toml`` (lowest)
    2. ``.config/grove.toml``
    3. ``.grove.toml`` (legacy fallback only when #2 is missing)
    4. ``$GROVE_CONFIG_PATH`` (highest explicit override)
    """
    raw = _load_raw_config(repo_root)
    if not raw:
        return GroveConfig()
    return _build_config_from_raw(raw)


def get_sync_group_exclude_paths(repo_root: Path, config: GroveConfig) -> set[Path]:
    """Collect sync-group submodule paths to exclude from repo discovery."""
    from grove.sync import discover_sync_submodules

    paths: set[Path] = set()
    for group in config.sync_groups.values():
        for sub in discover_sync_submodules(repo_root, group.url_match):
            paths.add(sub.path)
    return paths
