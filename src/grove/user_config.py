"""
grove.user_config
User/project config path helpers and TOML utilities.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

USER_CONFIG_FILENAME = "config.toml"
PROJECT_CONFIG_RELATIVE = Path(".config") / "grove.toml"
LEGACY_CONFIG_FILENAME = ".grove.toml"
EXPLICIT_GROVE_CONFIG_ENV = "GROVE_CONFIG_PATH"

WT_USER_CONFIG_FILENAME = "config.toml"
WT_PROJECT_CONFIG_RELATIVE = Path(".config") / "wt.toml"

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_user_config_dir() -> Path:
    """Return the Grove user config directory."""
    override = os.environ.get("GROVE_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".config" / "grove"


def get_user_config_path() -> Path:
    """Return the Grove user config path."""
    return get_user_config_dir() / USER_CONFIG_FILENAME


def get_project_config_path(repo_root: Path) -> Path:
    """Return the project-scoped Grove config path."""
    return repo_root / PROJECT_CONFIG_RELATIVE


def get_legacy_config_path(repo_root: Path) -> Path:
    """Return the legacy project-scoped Grove config path."""
    return repo_root / LEGACY_CONFIG_FILENAME


def get_explicit_grove_config_path() -> Path | None:
    """Return an explicit Grove config override path, if configured."""
    override = os.environ.get(EXPLICIT_GROVE_CONFIG_ENV)
    if not override:
        return None
    return Path(override).expanduser().resolve()


def iter_grove_config_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return Grove config paths in merge order (lowest -> highest precedence).

    Legacy ``.grove.toml`` is only included as a fallback when the canonical
    project config (``.config/grove.toml``) is absent.
    """
    repo_root = repo_root.resolve()
    project = get_project_config_path(repo_root)
    paths = [get_user_config_path(), project]

    legacy = get_legacy_config_path(repo_root)
    if not project.exists() and legacy.exists():
        paths.append(legacy)

    explicit = get_explicit_grove_config_path()
    if explicit is not None:
        paths.append(explicit)
    return tuple(paths)


def get_wt_user_config_path() -> Path:
    """Return the WorkTrunk user config path."""
    override = os.environ.get("WORKTRUNK_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".config" / "worktrunk" / WT_USER_CONFIG_FILENAME


def get_wt_project_config_path(repo_root: Path) -> Path:
    """Return the WorkTrunk project config path."""
    return repo_root / WT_PROJECT_CONFIG_RELATIVE


def load_toml_file(path: Path) -> dict:
    """Load a TOML file into a dict."""
    path = path.resolve()
    with open(path, "rb") as f:
        return tomllib.load(f)


def merge_dicts(base: dict, override: dict) -> dict:
    """Deep-merge two dictionaries, with *override* winning."""
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = merge_dicts(existing, value)
        else:
            result[key] = value
    return result


def dump_toml(data: dict) -> str:
    """Serialize a nested dict to TOML.

    Supports strings, booleans, ints, floats, lists, and nested tables.
    """
    lines: list[str] = []
    _emit_table(lines, [], data)
    return "\n".join(lines).rstrip() + "\n"


def _emit_table(lines: list[str], path: list[str], table: dict) -> None:
    scalar_items: list[tuple[str, object]] = []
    dict_items: list[tuple[str, dict]] = []

    for key, value in table.items():
        if isinstance(value, dict):
            dict_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if path:
        if lines:
            lines.append("")
        lines.append(f"[{'.'.join(_format_key(part) for part in path)}]")

    for key, value in scalar_items:
        lines.append(f"{_format_key(key)} = {_format_value(value)}")

    for key, value in dict_items:
        _emit_table(lines, [*path, key], value)


def _format_key(key: str) -> str:
    if _BARE_KEY_RE.match(key):
        return key
    return _quote_string(key)


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _quote_string(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if value is None:
        raise TypeError("TOML does not support null values")
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _quote_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
