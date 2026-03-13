"""
grove.shell
Generate shell wrappers that support directory-switch directives.
"""

from __future__ import annotations

import os
from pathlib import Path

from grove.repo_utils import Colors
from grove.worktree_switch import generate_shell_wrapper


def _detect_shell_name() -> str | None:
    """Best-effort shell detection from $SHELL."""
    shell = os.environ.get("SHELL")
    if not shell:
        return None
    name = Path(shell).name
    return name if name in ("bash", "zsh", "fish") else None


def run(args) -> int:
    """Entry point for `grove shell` commands."""
    if args.shell_command != "init":
        return 2

    shell_name = args.shell_name or _detect_shell_name()
    if shell_name is None:
        print(
            f"{Colors.red('Error')}: could not detect shell. "
            "Pass one explicitly: bash, zsh, or fish."
        )
        return 1

    try:
        print(generate_shell_wrapper(shell_name))
        return 0
    except ValueError:
        print(f"{Colors.red('Error')}: unsupported shell: {shell_name}")
        return 1
