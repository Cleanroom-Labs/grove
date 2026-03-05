"""
grove.llm
Optional commit/squash message generation helpers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from grove.config import load_config
from grove.repo_utils import Colors


def _generation_command(repo_root: Path) -> str | None:
    """Resolve commit generation command from Grove config."""
    try:
        command = load_config(repo_root).commit.generation.command
    except ValueError as exc:
        print(
            f"{Colors.yellow('Warning')}: failed to load config for message "
            f"generation ({exc}); falling back to editor."
        )
        return None

    if not command or not command.strip():
        return None

    return command


def generate_message(repo_root: Path, prompt: str) -> str | None:
    """Generate a commit/squash message from configured command.

    Returns None when generation is not configured or command execution fails.
    """
    command = _generation_command(repo_root)
    if not command:
        return None

    result = subprocess.run(
        command,
        shell=True,
        cwd=str(repo_root),
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"{Colors.yellow('Warning')}: message generation command failed "
            f"(exit {result.returncode}); falling back to editor."
        )
        return None

    message = result.stdout.strip()
    if not message:
        print(
            f"{Colors.yellow('Warning')}: message generation returned empty output; "
            "falling back to editor."
        )
        return None
    return message
