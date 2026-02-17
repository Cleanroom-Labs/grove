"""
grove/config.py
Load and validate .grove.toml configuration.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = ".grove.toml"
DEFAULT_COMMIT_MESSAGE = "chore: sync {group} submodule to {sha}"


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
class WorktreeConfig:
    """Configuration for ``grove worktree add``."""
    copy_venv: bool = False


# The four cascade test tiers, in execution order.
CASCADE_TIERS = ("local-tests", "contract-tests", "integration-tests", "system-tests")


@dataclass
class CascadeConfig:
    """Configuration for ``grove cascade``.

    Four test tiers form a progressive confidence ladder:

    - **local-tests** — project-internal, all deps mocked
    - **contract-tests** — interface boundaries, other side mocked
    - **integration-tests** — direct deps real, transitive deps mocked
    - **system-tests** — everything real, no mocking

    Each tier is optional.  When a tier is *None* it is skipped during
    cascade execution.  ``local-tests`` falls back to
    ``[worktree-merge].test-command`` when not configured explicitly.

    Per-repo overrides are stored as
    ``{repo_rel_path: {tier_name: command}}``.
    """
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
    """Top-level configuration loaded from .grove.toml."""
    sync_groups: dict[str, SyncGroup] = field(default_factory=dict)
    merge: MergeConfig = field(default_factory=MergeConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
    aliases: AliasConfig = field(default_factory=AliasConfig)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_cascade_section(raw: dict, merge: MergeConfig) -> CascadeConfig:
    """Parse the ``[cascade]`` section from raw TOML data.

    Falls back to ``[worktree-merge].test-command`` for ``local-tests``
    when neither ``[cascade].local-tests`` nor an override is configured.
    """
    cascade_raw = raw.get("cascade", {})
    if not isinstance(cascade_raw, dict):
        raise ValueError(
            f"cascade: expected a table, got {type(cascade_raw).__name__}"
        )

    tier_values: dict[str, str | None] = {}
    for tier in CASCADE_TIERS:
        val = cascade_raw.get(tier)
        if val is not None and not isinstance(val, str):
            raise ValueError(f"cascade.{tier}: expected a string, got {type(val).__name__}")
        tier_values[tier] = val

    # Fallback: local-tests inherits from worktree-merge.test-command
    if tier_values["local-tests"] is None and merge.test_command is not None:
        tier_values["local-tests"] = merge.test_command

    # Parse per-repo overrides
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
        for key, val in repo_data.items():
            if key not in CASCADE_TIERS:
                raise ValueError(
                    f"cascade.overrides.{repo_path}.{key}: "
                    f"unknown tier (expected one of {', '.join(CASCADE_TIERS)})"
                )
            if not isinstance(val, str):
                raise ValueError(
                    f"cascade.overrides.{repo_path}.{key}: expected a string, "
                    f"got {type(val).__name__}"
                )
            repo_overrides[key] = val
        overrides[repo_path] = repo_overrides

    return CascadeConfig(
        local_tests=tier_values["local-tests"],
        contract_tests=tier_values["contract-tests"],
        integration_tests=tier_values["integration-tests"],
        system_tests=tier_values["system-tests"],
        overrides=overrides,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(repo_root: Path) -> GroveConfig:
    """Load .grove.toml from *repo_root*.

    Returns a config with an empty ``sync_groups`` dict when the file
    is missing or the ``[sync-groups]`` section is absent.

    Raises:
        ValueError: If the file contains invalid or incomplete configuration.
    """
    config_path = repo_root / CONFIG_FILENAME
    if not config_path.exists():
        return GroveConfig()

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Invalid TOML in {config_path}: {e}") from e

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
        standalone_repo = Path(standalone_repo_str).expanduser() if standalone_repo_str else None

        commit_message = group_data.get("commit-message", DEFAULT_COMMIT_MESSAGE)

        allow_drift_raw = group_data.get("allow-drift", [])
        if not isinstance(allow_drift_raw, list) or not all(
            isinstance(p, str) for p in allow_drift_raw
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

    # --- [worktree-merge] section ---
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
    for key, val in test_overrides_raw.items():
        if not isinstance(val, str):
            raise ValueError(
                f"worktree-merge.test-overrides.{key}: expected a string, "
                f"got {type(val).__name__}"
            )
    merge = MergeConfig(
        test_command=test_command,
        test_overrides=dict(test_overrides_raw),
    )

    # --- [worktree] section ---
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
    worktree_config = WorktreeConfig(copy_venv=copy_venv)

    # --- [cascade] section ---
    cascade = _parse_cascade_section(raw, merge)

    # --- [aliases] section ---
    aliases_raw = raw.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        raise ValueError(
            f"aliases: expected a table, got {type(aliases_raw).__name__}"
        )
    for key, val in aliases_raw.items():
        if not isinstance(val, str):
            raise ValueError(
                f"aliases.{key}: expected a string, got {type(val).__name__}"
            )
    aliases = AliasConfig(mapping=dict(aliases_raw))

    return GroveConfig(
        sync_groups=sync_groups, merge=merge, worktree=worktree_config,
        cascade=cascade, aliases=aliases,
    )


def get_sync_group_exclude_paths(repo_root: Path, config: GroveConfig) -> set[Path]:
    """Collect sync-group submodule paths to exclude from repo discovery.

    Avoids the 3-line pattern duplicated across push, merge, and check.
    Uses a lazy import to prevent a circular dependency with ``grove.sync``.
    """
    from grove.sync import discover_sync_submodules

    paths: set[Path] = set()
    for group in config.sync_groups.values():
        for sub in discover_sync_submodules(repo_root, group.url_match):
            paths.add(sub.path)
    return paths
