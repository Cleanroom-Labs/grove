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
class GroveConfig:
    """Top-level configuration loaded from .grove.toml."""
    sync_groups: dict[str, SyncGroup] = field(default_factory=dict)
    merge: MergeConfig = field(default_factory=MergeConfig)


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

    return GroveConfig(sync_groups=sync_groups, merge=merge)


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
