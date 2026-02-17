"""
grove.cli
Main CLI entry point: grove {check,push,sync,visualize,worktree,claude,completion}

Exit codes:
    0 — Success
    1 — Error (validation failure, runtime error)
    2 — Usage error (no command, missing required subcommand)
"""

import argparse
import os
import sys

from grove.repo_utils import Colors


def build_parser():
    """Construct the grove argument parser.

    Separated from main() so other modules (e.g. completion) can
    introspect the parser without side effects.
    """
    parser = argparse.ArgumentParser(
        prog="grove",
        description="Git submodule management tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove check              Check submodule health and sync groups
  grove check -v           Verbose check with commit SHAs
  grove push --dry-run     Preview what would be pushed
  grove sync               Sync all groups to latest
  grove sync common        Sync just "common" group
  grove sync common --commit abc123  Sync "common" to specific commit
  grove init               Generate template .grove.toml in current directory
  grove init ../other-repo  Generate template .grove.toml at specified path
  grove visualize          Open interactive submodule visualizer
  grove worktree add ../website-wt1 my-feature
  grove worktree add ../website-wt2 existing-branch
  grove worktree add -b ../website-wt3 new-feature
  grove worktree remove ../website-wt1
""",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- grove init ---
    init_parser = subparsers.add_parser(
        "init",
        help="Generate a template .grove.toml configuration file",
        description="Write a commented .grove.toml template showing all "
        "available configuration options.",
    )
    init_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to write .grove.toml to (default: current directory)",
    )
    init_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite an existing .grove.toml",
    )

    # --- grove check ---
    check_parser = subparsers.add_parser(
        "check",
        help="Verify submodules are on branches and sync groups are consistent",
        description="Verify all submodules are correctly configured: on a branch "
        "(not detached HEAD) and all sync-group submodules at the same commit.",
    )
    check_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional details (commits, remotes)",
    )

    # --- grove push ---
    push_parser = subparsers.add_parser(
        "push",
        help="Push committed changes through nested submodules bottom-up",
        description="Push committed changes through nested submodules using "
        "topological sort to ensure children are pushed before parents.\n\n"
        "By default, pushes all repos with unpushed commits (excluding "
        "sync-group submodules). Use filter options to push a subset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove push                              Push all repos with unpushed commits
  grove push --dry-run                    Preview what would be pushed
  grove push frontend backend             Push specific repos by path
  grove push --sync-group common          Push parents of a sync group
  grove push --cascade libs/common        Push cascade chain from a leaf
  grove push --cascade libs/common --dry-run  Preview cascade push
""",
    )
    push_parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="Specific repo paths to push (exact match on relative path)",
    )
    push_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be pushed without pushing",
    )
    push_parser.add_argument(
        "--skip-checks", "-f",
        action="store_true",
        dest="skip_checks",
        help="Skip validation (for recovery scenarios)",
    )
    push_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional details during push",
    )
    push_parser.add_argument(
        "--sync-group",
        metavar="NAME",
        help="Push parent repos of a sync group",
    )
    push_parser.add_argument(
        "--cascade",
        metavar="PATH",
        help="Push repos in the cascade chain from a leaf submodule to root",
    )

    # --- grove sync ---
    sync_parser = subparsers.add_parser(
        "sync",
        help="Synchronize submodule sync groups across all locations",
        description="Synchronize submodule sync groups across all "
        "locations with validation and push support.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove sync                       Sync all groups (local-first)
  grove sync common                Sync just "common" group
  grove sync common --commit abc1234  Sync "common" to specific commit
  grove sync --remote              Resolve target from remote
  grove sync --dry-run             Preview what would happen
  grove sync --no-push             Commit only, skip pushing
""",
    )
    sync_parser.add_argument(
        "group",
        nargs="?",
        help="Sync group name (syncs all groups if omitted)",
    )
    sync_parser.add_argument(
        "--commit",
        metavar="SHA",
        help="Target commit SHA (defaults to most advanced local instance)",
    )
    sync_parser.add_argument(
        "--remote",
        action="store_true",
        help="Resolve target from remote instead of local instances",
    )
    sync_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview changes without making them",
    )
    sync_parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit only, skip pushing (push is default)",
    )
    sync_parser.add_argument(
        "--skip-checks", "-f",
        action="store_true",
        dest="skip_checks",
        help="Skip remote sync validation",
    )
    sync_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional details during sync",
    )
    sync_parser.add_argument(
        "--continue",
        action="store_true",
        dest="continue_sync",
        help="Resume a paused sync merge after resolving conflicts",
    )
    sync_parser.add_argument(
        "--abort",
        action="store_true",
        help="Abort an in-progress sync merge",
    )
    sync_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current sync merge progress",
    )

    # --- grove visualize ---
    viz_parser = subparsers.add_parser(
        "visualize",
        help="Open interactive submodule visualizer in browser",
        description="Launch a browser-based visualizer that displays the git "
        "repository hierarchy and submodule relationships.",
    )
    viz_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to git repository (default: current directory)",
    )

    # --- grove worktree ---
    worktree_parser = subparsers.add_parser(
        "worktree",
        help="Manage git worktrees with automatic submodule initialization",
        description="Create and remove git worktrees with recursive submodule "
        "initialization using the main worktree as a reference.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove worktree add ../my-project-wt1 my-feature
  grove worktree add -b ../wt2 new-feature
  grove worktree remove ../my-project-wt1
  grove worktree remove --force ../my-project-wt1
  grove worktree merge my-feature
  grove worktree merge --continue
  grove worktree merge --abort
  grove worktree merge --status
  grove claude install
  grove claude install --user
  grove claude install --check
""",
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command")

    worktree_add_parser = worktree_subparsers.add_parser(
        "add",
        help="Create a new worktree with submodules initialized",
        description="Create a git worktree on a new or existing branch, then "
        "recursively initialize all submodules using the main worktree's "
        "copies as references.  Submodule remotes are kept pointing to the "
        "main worktree by default (use --no-local-remotes to restore upstream URLs).",
    )
    worktree_add_parser.add_argument(
        "path",
        help="Path where the worktree should be created",
    )
    worktree_add_parser.add_argument(
        "branch",
        help="Branch name to checkout (or create with -b)",
    )
    worktree_add_parser.add_argument(
        "-b",
        action="store_true",
        dest="create_branch",
        help="Create a new branch instead of checking out an existing one",
    )
    worktree_add_parser.add_argument(
        "--copy-venv",
        action="store_true",
        help="Copy Python venv from the main worktree (auto-detects location, fixes paths)",
    )
    worktree_add_parser.add_argument(
        "--no-local-remotes",
        action="store_true",
        help="Point submodule remotes to upstream instead of the main worktree",
    )

    worktree_remove_parser = worktree_subparsers.add_parser(
        "remove",
        help="Remove a worktree and prune stale entries",
        description="Remove a git worktree and run git worktree prune.",
    )
    worktree_remove_parser.add_argument(
        "path",
        help="Path to the worktree to remove",
    )
    worktree_remove_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force removal even if the worktree has uncommitted changes",
    )

    worktree_merge_parser = worktree_subparsers.add_parser(
        "merge",
        help="Merge a branch across all submodules bottom-up",
        description="Merge a feature branch into the current branch across all "
        "repos in the submodule tree, processing leaves first.",
    )
    worktree_merge_parser.add_argument(
        "branch",
        nargs="?",
        help="Branch to merge into the current branch",
    )
    worktree_merge_parser.add_argument(
        "--continue",
        action="store_true",
        dest="continue_merge",
        help="Resume after resolving a conflict or test failure",
    )
    worktree_merge_parser.add_argument(
        "--abort",
        action="store_true",
        help="Undo all merges and restore pre-merge state",
    )
    worktree_merge_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current merge progress",
    )
    worktree_merge_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would happen without merging",
    )
    worktree_merge_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional details during merge",
    )
    worktree_merge_parser.add_argument(
        "--no-recurse",
        action="store_true",
        help="Only operate on the root repo",
    )
    worktree_merge_parser.add_argument(
        "--no-ff",
        action="store_true",
        help="Always create a merge commit (even for fast-forwards)",
    )
    worktree_merge_parser.add_argument(
        "--no-test",
        action="store_true",
        help="Skip running test commands",
    )

    worktree_checkout_parser = worktree_subparsers.add_parser(
        "checkout-branches",
        help="Put submodules onto named branches (fix detached HEAD)",
        description="Create or checkout a branch in each non-sync-group "
        "submodule, matching the parent worktree's current branch. "
        "Use this to fix worktrees where submodules are in detached HEAD.",
    )
    worktree_checkout_parser.add_argument(
        "--branch",
        help="Branch name to use (default: current branch of root worktree)",
    )

    # --- grove cascade ---
    cascade_parser = subparsers.add_parser(
        "cascade",
        help="Propagate a submodule change upward with tiered testing",
        description="Bottom-up cascade integration: propagate a change from "
        "a leaf submodule through intermediate parents to the root, running "
        "tests at each level and committing submodule pointer updates.\n\n"
        "Four test tiers form a progressive confidence ladder:\n"
        "  local-tests        Project-internal, all deps mocked\n"
        "  contract-tests     Interface boundaries, other side mocked\n"
        "  integration-tests  Direct deps real, transitive deps mocked\n"
        "  system-tests       Everything real, no mocking\n\n"
        "Configure tiers in .grove.toml under [cascade].",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove cascade libs/common               Start cascade from a leaf submodule
  grove cascade libs/common --dry-run     Preview cascade chain and test plan
  grove cascade --status                  Show current cascade state
  grove cascade --continue                Resume after fixing a test failure
  grove cascade --abort                   Rollback all pointer commits
  grove cascade libs/common --quick       Run only local + contract tests
  grove cascade libs/common --system      Run system-tests at every level
  grove cascade libs/common --no-system   Skip system-tests even at root
  grove cascade --sync-group common       Cascade all instances of a sync group
""",
    )
    cascade_parser.add_argument(
        "path",
        nargs="?",
        help="Path to the leaf submodule to cascade from",
    )
    cascade_parser.add_argument(
        "--continue",
        action="store_true",
        dest="continue_cascade",
        help="Resume after fixing a test failure",
    )
    cascade_parser.add_argument(
        "--abort",
        action="store_true",
        help="Rollback all cascade commits and restore pre-cascade state",
    )
    cascade_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current cascade progress",
    )
    cascade_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview the cascade chain and test plan without making changes",
    )
    cascade_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional details during cascade",
    )
    cascade_system = cascade_parser.add_mutually_exclusive_group()
    cascade_system.add_argument(
        "--system",
        action="store_true",
        help="Run system-tests at every level (thorough mode)",
    )
    cascade_system.add_argument(
        "--no-system",
        action="store_true",
        help="Skip system-tests even at root (fast mode)",
    )
    cascade_parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only local-tests and contract-tests everywhere (fastest)",
    )
    cascade_parser.add_argument(
        "--skip-checks", "-f",
        action="store_true",
        dest="skip_checks",
        help="Skip sync-group consistency check (proceed even if instances differ)",
    )
    cascade_parser.add_argument(
        "--sync-group",
        metavar="NAME",
        help="Cascade all instances of a sync group (alternative to specifying a path)",
    )
    cascade_parser.add_argument(
        "--push",
        action="store_true",
        help="Push all cascade repos after successful completion",
    )

    # --- grove claude ---
    claude_parser = subparsers.add_parser(
        "claude",
        help="Manage Claude Code skills shipped with grove",
        description="Install or check Claude Code skills that teach Claude "
        "how to use grove workflows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove claude install           Install skills to .claude/skills/ in current project
  grove claude install --user    Install skills to ~/.claude/skills/
  grove claude install --check   Check if installed skills are up to date
""",
    )
    claude_subparsers = claude_parser.add_subparsers(dest="claude_command")

    claude_install_parser = claude_subparsers.add_parser(
        "install",
        help="Install Claude Code skills from grove's package data",
        description="Copy skill files to the project's .claude/skills/ "
        "directory (or ~/.claude/skills/ with --user).",
    )
    claude_install_parser.add_argument(
        "--user",
        action="store_true",
        help="Install to ~/.claude/skills/ instead of the project",
    )
    claude_install_parser.add_argument(
        "--check",
        action="store_true",
        help="Check if installed skills match shipped versions",
    )

    # --- grove completion ---
    completion_parser = subparsers.add_parser(
        "completion",
        help="Generate or install shell completion scripts",
        description="Generate a shell completion script for bash, zsh, or fish.\n\n"
        "To generate a script:\n"
        "  grove completion bash\n"
        "  grove completion zsh\n"
        "  grove completion fish\n\n"
        "To install automatically:\n"
        "  grove completion install\n"
        "  grove completion install --shell zsh\n"
        "  grove completion install --check\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    completion_subparsers = completion_parser.add_subparsers(
        dest="completion_command",
    )

    for shell_name in ("bash", "zsh", "fish"):
        completion_subparsers.add_parser(
            shell_name,
            help=f"Generate {shell_name} completion script",
        )

    completion_install_parser = completion_subparsers.add_parser(
        "install",
        help="Install shell completions into your shell profile",
        description="Auto-detect your shell and install tab-completion.\n\n"
        "For bash/zsh: adds an eval line to your profile.\n"
        "For fish: writes a completions file to "
        "~/.config/fish/completions/grove.fish.",
    )
    completion_install_parser.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish"],
        default=None,
        help="Shell to install for (default: auto-detect from $SHELL)",
    )
    completion_install_parser.add_argument(
        "--check",
        action="store_true",
        help="Check if completions are already installed",
    )
    completion_install_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview changes without modifying any files",
    )
    completion_install_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Re-write the completion block even if already installed",
    )

    # Store references for help printing (avoids argparse private API)
    parser.grove_subparsers = {
        "worktree": worktree_parser,
        "claude": claude_parser,
        "completion": completion_parser,
    }

    return parser


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

    if args.command == "init":
        from grove.init import run
        return run(args)

    if args.command == "check":
        from grove.check import run
        return run(args)

    if args.command == "push":
        from grove.push import run
        return run(args)

    if args.command == "sync":
        from grove.sync import run
        return run(args)

    if args.command == "cascade":
        from grove.cascade import run
        return run(args)

    if args.command == "visualize":
        from grove.visualizer.__main__ import run
        return run(args)

    if args.command == "worktree":
        if not args.worktree_command:
            parser.grove_subparsers["worktree"].print_help()
            return 2
        if args.worktree_command == "merge":
            from grove.worktree_merge import run
            return run(args)
        from grove.worktree import run
        return run(args)

    if args.command == "claude":
        if not args.claude_command:
            parser.grove_subparsers["claude"].print_help()
            return 2
        if args.claude_command == "install":
            from grove.claude import run_install
            return run_install(args)

    if args.command == "completion":
        if not args.completion_command:
            parser.grove_subparsers["completion"].print_help()
            return 2
        if args.completion_command == "install":
            from grove.completion import run_install
            return run_install(args)
        from grove.completion import run
        return run(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
