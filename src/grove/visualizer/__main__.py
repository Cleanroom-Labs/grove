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

import argparse
import sys
from pathlib import Path


def run(args=None) -> int:
    """Run the visualizer, callable from the CLI entry point."""
    if not isinstance(args, argparse.Namespace):
        parser = argparse.ArgumentParser(
            description="Visualize git repositories and their submodules"
        )
        parser.add_argument(
            "path",
            nargs="?",
            default=".",
            help="Path to the git repository (default: current directory)",
        )
        args = parser.parse_args(args)

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


def main() -> int:
    """Main entry point."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
