"""
grove.shell
Generate shell wrappers that support directory-switch directives.
"""

from __future__ import annotations

import os
from pathlib import Path

from grove.repo_utils import Colors


def _detect_shell_name() -> str | None:
    """Best-effort shell detection from $SHELL."""
    shell = os.environ.get("SHELL")
    if not shell:
        return None
    name = Path(shell).name
    return name if name in ("bash", "zsh", "fish") else None


def _render_bash_like_wrapper(shell_name: str) -> str:
    """Render a bash/zsh wrapper for `grove`."""
    return f"""# grove shell integration ({shell_name})
# eval "$(grove shell init {shell_name})"
grove() {{
    local directive_file
    directive_file="$(mktemp)"
    command grove --directive-file "$directive_file" "$@"
    local exit_code=$?
    if [ -f "$directive_file" ]; then
        local target_dir
        target_dir="$(cat "$directive_file")"
        if [ -n "$target_dir" ]; then
            cd "$target_dir" || return $exit_code
        fi
        rm -f "$directive_file"
    fi
    return $exit_code
}}
"""


def _render_fish_wrapper() -> str:
    """Render a fish wrapper for `grove`."""
    return """# grove shell integration (fish)
# grove shell init fish | source
function grove
    set -l directive_file (mktemp)
    command grove --directive-file "$directive_file" $argv
    set -l exit_code $status
    if test -f "$directive_file"
        set -l target_dir (cat "$directive_file")
        if test -n "$target_dir"
            cd "$target_dir"
        end
        rm -f "$directive_file"
    end
    return $exit_code
end
"""


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

    if shell_name in ("bash", "zsh"):
        print(_render_bash_like_wrapper(shell_name))
        return 0
    if shell_name == "fish":
        print(_render_fish_wrapper())
        return 0

    print(f"{Colors.red('Error')}: unsupported shell: {shell_name}")
    return 1
