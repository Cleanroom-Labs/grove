#!/usr/bin/env python3
"""
Entry point for the git submodule visualizer.

Usage:
    python -m grove.visualizer [path]
    grove visualize [path]

Args:
    path: Path to the git repository (default: auto-detected from cwd).
          Can be run from any subdirectory within a repository.
"""

from __future__ import annotations
import sys
from pathlib import Path


def run(args) -> int:
    """Run the visualizer, callable from the CLI entry point."""
    from grove.repo_utils import find_repo_root

    start_path = Path(args.path).resolve()
    if not start_path.exists():
        print(f"Error: Path does not exist: {start_path}", file=sys.stderr)
        return 1

    try:
        repo_path = find_repo_root(start_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Import here to avoid tkinter import on --help
    from .app import SubmoduleVisualizerApp

    app = SubmoduleVisualizerApp(repo_path)
    app.run()
    return 0


if __name__ == "__main__":
    from grove.cli import build_parser
    sys.exit(run(build_parser().parse_args(["visualize"] + sys.argv[1:])))
