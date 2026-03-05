"""
grove.cli
Thin CLI entry point and orchestration layer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from grove.cli_dispatch import dispatch_command
from grove.cli_parsers import build_parser
from grove.repo_utils import Colors


def _expand_aliases(argv: list[str]) -> list[str]:
    """Expand the first token of *argv* if it matches a configured alias."""
    if not argv:
        return argv

    try:
        from grove.config import load_config
        from grove.repo_utils import find_repo_root

        repo_root = find_repo_root()
        config = load_config(repo_root)
    except (FileNotFoundError, ValueError):
        return argv

    alias_value = config.aliases.mapping.get(argv[0])
    if alias_value is None:
        return argv

    return alias_value.split() + argv[1:]


def _activate_worktree_config_override(args) -> tuple[bool, bool, str | None]:
    """Activate a one-shot GROVE_CONFIG_PATH override for worktree commands."""
    if getattr(args, "command", None) != "worktree":
        return (True, False, None)

    raw_path = getattr(args, "config", None)
    if not raw_path:
        return (True, False, None)

    config_path = Path(raw_path).expanduser().resolve()
    if not config_path.exists():
        print(Colors.red(f"Error: config file not found: {config_path}"))
        return (False, False, None)
    if not config_path.is_file():
        print(Colors.red(f"Error: config path is not a file: {config_path}"))
        return (False, False, None)

    previous = os.environ.get("GROVE_CONFIG_PATH")
    os.environ["GROVE_CONFIG_PATH"] = str(config_path)
    return (True, True, previous)


def _restore_worktree_config_override(active: bool, previous: str | None) -> None:
    """Restore GROVE_CONFIG_PATH to its prior value after dispatch."""
    if not active:
        return
    if previous is None:
        os.environ.pop("GROVE_CONFIG_PATH", None)
        return
    os.environ["GROVE_CONFIG_PATH"] = previous


def main(argv=None):
    parser = build_parser()

    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    effective_argv = _expand_aliases(effective_argv)

    args = parser.parse_args(effective_argv)

    # Handle --no-color and NO_COLOR env var
    if args.no_color or os.environ.get("NO_COLOR") is not None:
        Colors.disable()

    if not args.command:
        parser.print_help()
        return 2

    override_ok, override_active, previous_override = (
        _activate_worktree_config_override(args)
    )
    if not override_ok:
        return 1

    try:
        return dispatch_command(args, parser)
    finally:
        _restore_worktree_config_override(override_active, previous_override)


if __name__ == "__main__":
    sys.exit(main())
