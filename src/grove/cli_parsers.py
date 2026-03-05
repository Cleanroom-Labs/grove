"""
grove.cli_parsers
CLI parser construction helpers.
"""

import argparse


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
  grove checkout sub origin/main  Checkout ref with recursive submodule update
  grove init               Generate template .config/grove.toml in current directory
  grove init ../other-repo  Generate template .config/grove.toml at specified path
  grove init --legacy      Generate deprecated legacy .grove.toml
  grove visualize          Open interactive submodule visualizer
  grove worktree add ../website-wt1 my-feature
  grove worktree add ../website-wt2 existing-branch
  grove worktree add -b ../website-wt3 new-feature
  grove worktree remove ../website-wt1
  grove shell init zsh
""",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--directive-file",
        default=None,
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- grove init ---
    init_parser = subparsers.add_parser(
        "init",
        help="Generate a template Grove configuration file",
        description="Write a commented .config/grove.toml template showing all "
        "available configuration options.",
    )
    init_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to write config to (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite an existing target config file",
    )
    init_parser.add_argument(
        "--legacy",
        action="store_true",
        help="Write deprecated legacy .grove.toml instead of .config/grove.toml",
    )

    # --- grove check ---
    check_parser = subparsers.add_parser(
        "check",
        help="Verify submodules are on branches and sync groups are consistent",
        description="Verify all submodules are correctly configured: on a branch "
        "(not detached HEAD) and all sync-group submodules at the same commit.",
    )
    check_parser.add_argument(
        "--verbose",
        "-v",
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
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be pushed without pushing",
    )
    push_parser.add_argument(
        "--skip-checks",
        "-f",
        action="store_true",
        dest="skip_checks",
        help="Skip validation (for recovery scenarios)",
    )
    push_parser.add_argument(
        "--verbose",
        "-v",
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
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview changes without making them",
    )
    sync_parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit only, skip pushing (push is default)",
    )
    sync_parser.add_argument(
        "--skip-checks",
        "-f",
        action="store_true",
        dest="skip_checks",
        help="Skip remote sync validation",
    )
    sync_parser.add_argument(
        "--verbose",
        "-v",
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

    # --- grove shell ---
    shell_parser = subparsers.add_parser(
        "shell",
        help="Generate shell wrappers for directory-switch integration",
        description="Generate shell integration wrappers so commands like "
        "`grove worktree switch` can change the caller's directory.",
    )
    shell_subparsers = shell_parser.add_subparsers(dest="shell_command")
    shell_init_parser = shell_subparsers.add_parser(
        "init",
        help="Generate wrapper code for bash, zsh, or fish",
        description="Print wrapper code that forwards commands to grove and "
        "applies directory-change directives.",
    )
    shell_init_parser.add_argument(
        "shell_name",
        nargs="?",
        choices=("bash", "zsh", "fish"),
        help="Shell to generate for (default: detect from $SHELL)",
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
  grove worktree switch my-feature
  grove worktree switch -c new-feature
  grove worktree list
  grove worktree list --format json --branches
  grove worktree init-submodules ../existing-wt --reference .
  grove worktree remove my-feature
  grove worktree remove --force my-feature
  grove worktree remove ../my-project-wt1
  grove worktree merge my-feature
  grove worktree merge --continue
  grove worktree merge --abort
  grove worktree merge --status
  grove claude install
  grove claude install --user
  grove claude install --check
""",
    )

    def _add_worktree_config_arg(target_parser):
        target_parser.add_argument(
            "--config",
            default=argparse.SUPPRESS,
            help="Use an explicit Grove config file for this worktree command invocation",
        )

    _add_worktree_config_arg(worktree_parser)
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command")

    worktree_add_parser = worktree_subparsers.add_parser(
        "add",
        help="Create a new worktree with submodules initialized",
        description="Create a git worktree on a new or existing branch, then "
        "recursively initialize all submodules using the main worktree's "
        "copies as references.  Submodule remotes are kept pointing to the "
        "main worktree by default (use --no-local-remotes to restore upstream URLs).",
    )
    _add_worktree_config_arg(worktree_add_parser)
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
    worktree_add_parser.add_argument(
        "--exclude-sync-group",
        action="store_true",
        help="Leave sync-group submodules detached instead of checking out branches",
    )

    worktree_init_parser = worktree_subparsers.add_parser(
        "init-submodules",
        help="Initialize submodules and checkout matching branches in a worktree",
        description="Initialize submodules in an existing worktree, then checkout "
        "matching branches in submodules. By default, sync-group submodules are "
        "also checked out onto the worktree branch.",
    )
    _add_worktree_config_arg(worktree_init_parser)
    worktree_init_parser.add_argument(
        "path",
        help="Path to the worktree to initialize",
    )
    worktree_init_parser.add_argument(
        "--reference",
        help="Reference worktree to use for local submodule URLs (default: current repo root)",
    )
    worktree_init_parser.add_argument(
        "--branch",
        help="Branch name to use (default: current branch of target worktree)",
    )
    worktree_init_parser.add_argument(
        "--no-local-remotes",
        action="store_true",
        help="Point submodule remotes to upstream instead of the reference worktree",
    )
    worktree_init_parser.add_argument(
        "--exclude-sync-group",
        action="store_true",
        help="Leave sync-group submodules detached instead of checking out branches",
    )

    worktree_switch_parser = worktree_subparsers.add_parser(
        "switch",
        help="Switch to an existing worktree or create one",
        description="Switch to a worktree by branch. With -c, create the worktree "
        "if it does not exist yet. Without shell integration, Grove prints the "
        "target path so the caller can cd there.",
    )
    _add_worktree_config_arg(worktree_switch_parser)
    worktree_switch_parser.add_argument(
        "branch",
        nargs="?",
        help="Branch name or shortcut (^, @, -)",
    )
    worktree_switch_parser.add_argument(
        "--branches",
        action="store_true",
        help="Include local branches without worktrees in interactive selection",
    )
    worktree_switch_parser.add_argument(
        "--remotes",
        action="store_true",
        help="Include remote branches in interactive selection",
    )
    worktree_switch_parser.add_argument(
        "-c",
        "--create",
        action="store_true",
        help="Create a worktree when the branch does not already have one",
    )
    worktree_switch_parser.add_argument(
        "-b",
        "--base",
        help="Base branch/ref for new worktree creation",
    )
    worktree_switch_parser.add_argument(
        "-x",
        "--execute",
        help="Command to run in the target worktree after switching",
    )
    worktree_switch_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts",
    )
    worktree_switch_parser.add_argument(
        "--clobber",
        action="store_true",
        help="Allow switching even when the target path already exists",
    )
    worktree_switch_parser.add_argument(
        "--no-cd",
        action="store_true",
        help="Print the target path without trying to change directories",
    )
    worktree_switch_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip pre/post-switch hooks",
    )

    worktree_list_parser = worktree_subparsers.add_parser(
        "list",
        help="List native worktrees and optional branch inventory",
        description="List worktrees for the current repository. Optionally include "
        "local branches without worktrees and remote branches.",
    )
    _add_worktree_config_arg(worktree_list_parser)
    worktree_list_parser.add_argument(
        "--format",
        choices=("table", "json"),
        default=None,
        help="Output format (default: table)",
    )
    worktree_list_parser.add_argument(
        "--branches",
        action="store_true",
        default=None,
        help="Include local branches that do not currently have worktrees",
    )
    worktree_list_parser.add_argument(
        "--remotes",
        action="store_true",
        default=None,
        help="Include remote branches in addition to local branches",
    )
    worktree_list_parser.add_argument(
        "--full",
        action="store_true",
        default=None,
        help="Show additional native metadata columns",
    )
    worktree_list_parser.add_argument(
        "--progressive",
        action="store_true",
        default=None,
        help="Progressively render list output (delegated to wt backend)",
    )

    worktree_remove_parser = worktree_subparsers.add_parser(
        "remove",
        help="Remove worktrees by branch, with compatibility for path targets",
        description="Remove one or more worktrees by branch name. With no target, "
        "remove the current worktree branch. Explicit worktree paths are still "
        "accepted for compatibility.",
    )
    _add_worktree_config_arg(worktree_remove_parser)
    worktree_remove_parser.add_argument(
        "targets",
        nargs="*",
        help="Branch names to remove (or worktree paths for compatibility)",
    )
    worktree_remove_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force removal even if the worktree has uncommitted changes",
    )
    worktree_remove_parser.add_argument(
        "--no-delete-branch",
        action="store_true",
        help="Keep the local branch after removing the worktree",
    )
    worktree_remove_parser.add_argument(
        "-D",
        "--force-delete",
        action="store_true",
        help="Delete the branch even if Grove considers it unmerged",
    )
    worktree_remove_parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run removal in the foreground (native mode always does this)",
    )
    worktree_remove_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip pre/post-remove verification hooks",
    )
    worktree_remove_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts",
    )

    worktree_hook_parser = worktree_subparsers.add_parser(
        "hook",
        help="Show or run configured lifecycle hooks",
        description="Inspect configured hooks or run a specific hook type.",
    )
    _add_worktree_config_arg(worktree_hook_parser)
    worktree_hook_parser.add_argument(
        "hook_type",
        help="Hook type to run, or 'show' to list hooks",
    )
    worktree_hook_parser.add_argument(
        "name",
        nargs="?",
        help="Optional hook name (or hook type when using 'show')",
    )
    worktree_hook_parser.add_argument(
        "--expanded",
        action="store_true",
        help="Show expanded templates when listing hooks",
    )
    worktree_hook_parser.add_argument(
        "--var",
        action="append",
        help="Template variable override in KEY=VALUE form",
    )
    worktree_hook_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts for hook execution",
    )

    worktree_step_parser = worktree_subparsers.add_parser(
        "step",
        help="Run step-by-step worktree workflow commands",
        description="Run incremental worktree lifecycle steps such as diff, "
        "push, and rebase.",
    )
    _add_worktree_config_arg(worktree_step_parser)
    worktree_step_subparsers = worktree_step_parser.add_subparsers(dest="step_command")

    worktree_step_commit_parser = worktree_step_subparsers.add_parser(
        "commit",
        help="Create a commit for the current worktree changes",
    )
    _add_worktree_config_arg(worktree_step_commit_parser)
    worktree_step_commit_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts",
    )
    worktree_step_commit_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip pre-commit hooks",
    )
    worktree_step_commit_parser.add_argument(
        "--stage",
        choices=("all", "tracked", "none"),
        help="Staging mode for commit preparation",
    )
    worktree_step_commit_parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="Show the generated commit prompt and exit",
    )

    worktree_step_squash_parser = worktree_step_subparsers.add_parser(
        "squash",
        help="Squash commits on the current branch",
    )
    _add_worktree_config_arg(worktree_step_squash_parser)
    worktree_step_squash_parser.add_argument(
        "target",
        nargs="?",
        help="Target branch/ref to squash against (default: inferred default branch)",
    )
    worktree_step_squash_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts",
    )
    worktree_step_squash_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip pre-commit hooks",
    )
    worktree_step_squash_parser.add_argument(
        "--stage",
        choices=("all", "tracked", "none"),
        help="Staging mode for squash preparation",
    )
    worktree_step_squash_parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="Show the generated squash prompt and exit",
    )

    worktree_step_push_parser = worktree_step_subparsers.add_parser(
        "push",
        help="Push current HEAD to a target branch",
    )
    _add_worktree_config_arg(worktree_step_push_parser)
    worktree_step_push_parser.add_argument(
        "target",
        nargs="?",
        help="Target branch/ref (default: inferred default branch)",
    )

    worktree_step_rebase_parser = worktree_step_subparsers.add_parser(
        "rebase",
        help="Rebase current branch onto a target branch",
    )
    _add_worktree_config_arg(worktree_step_rebase_parser)
    worktree_step_rebase_parser.add_argument(
        "target",
        nargs="?",
        help="Target branch/ref (default: inferred default branch)",
    )

    worktree_step_diff_parser = worktree_step_subparsers.add_parser(
        "diff",
        help="Show diff between current branch and a target branch",
    )
    _add_worktree_config_arg(worktree_step_diff_parser)
    worktree_step_diff_parser.add_argument(
        "target",
        nargs="?",
        help="Target branch/ref (default: inferred default branch)",
    )
    worktree_step_diff_parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to git diff after '--'",
    )

    worktree_step_copy_ignored_parser = worktree_step_subparsers.add_parser(
        "copy-ignored",
        help="Copy gitignored files between worktrees",
    )
    _add_worktree_config_arg(worktree_step_copy_ignored_parser)
    worktree_step_copy_ignored_parser.add_argument(
        "--from",
        dest="from_branch",
        help="Source branch/worktree",
    )
    worktree_step_copy_ignored_parser.add_argument(
        "--to",
        dest="to_branch",
        help="Destination branch/worktree",
    )
    worktree_step_copy_ignored_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview copied files without modifying anything",
    )
    worktree_step_copy_ignored_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination files when they already exist",
    )

    worktree_step_for_each_parser = worktree_step_subparsers.add_parser(
        "for-each",
        help="Run a command template across worktrees",
    )
    _add_worktree_config_arg(worktree_step_for_each_parser)
    worktree_step_for_each_parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Command template and arguments",
    )

    worktree_step_promote_parser = worktree_step_subparsers.add_parser(
        "promote",
        help="Promote a worktree to become the primary worktree",
    )
    _add_worktree_config_arg(worktree_step_promote_parser)
    worktree_step_promote_parser.add_argument(
        "branch",
        nargs="?",
        help="Branch/worktree to promote",
    )

    worktree_step_prune_parser = worktree_step_subparsers.add_parser(
        "prune",
        help="Prune stale or integrated worktrees",
    )
    _add_worktree_config_arg(worktree_step_prune_parser)
    worktree_step_prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview candidates without removing them",
    )
    worktree_step_prune_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip approval prompts",
    )
    worktree_step_prune_parser.add_argument(
        "--min-age",
        help="Minimum worktree age required before pruning (for example: 1h, 2d)",
    )
    worktree_step_prune_parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run prune in the foreground",
    )

    worktree_step_relocate_parser = worktree_step_subparsers.add_parser(
        "relocate",
        help="Relocate one or more worktrees to managed paths",
    )
    _add_worktree_config_arg(worktree_step_relocate_parser)
    worktree_step_relocate_parser.add_argument(
        "branches",
        nargs="*",
        help="Branch/worktree names to relocate (default: interactive in wt)",
    )
    worktree_step_relocate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview relocation operations",
    )
    worktree_step_relocate_parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit staged changes before relocating",
    )
    worktree_step_relocate_parser.add_argument(
        "--clobber",
        action="store_true",
        help="Allow clobbering existing destination paths",
    )

    worktree_merge_parser = worktree_subparsers.add_parser(
        "merge",
        help="Merge a branch across all submodules bottom-up",
        description="Merge a feature branch into the current branch across all "
        "repos in the submodule tree, processing leaves first.",
    )
    _add_worktree_config_arg(worktree_merge_parser)
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
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would happen without merging",
    )
    worktree_merge_parser.add_argument(
        "--verbose",
        "-v",
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
    _add_worktree_config_arg(worktree_checkout_parser)
    worktree_checkout_parser.add_argument(
        "--branch",
        help="Branch name to use (default: current branch of root worktree)",
    )
    worktree_checkout_parser.add_argument(
        "--exclude-sync-group",
        action="store_true",
        help="Leave sync-group submodules detached instead of checking out branches",
    )

    # --- grove checkout ---
    checkout_parser = subparsers.add_parser(
        "checkout",
        help="Check out a ref on a submodule with recursive submodule init",
        description="Check out a branch, tag, or commit SHA on a submodule "
        "and recursively initialize/update all nested sub-submodules.\n\n"
        "Replaces the manual git checkout + git submodule update dance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove checkout technical-docs/transfer origin/main
  grove checkout docs/spec-docs abc1234
  grove checkout technical-docs/transfer origin/main --no-recurse
  grove checkout technical-docs/transfer v1.0.0 --no-fetch
""",
    )
    checkout_parser.add_argument(
        "path",
        help="Path to target repo (relative to repo root)",
    )
    checkout_parser.add_argument(
        "ref",
        help="Branch, tag, or commit SHA to checkout",
    )
    checkout_parser.add_argument(
        "--no-recurse",
        action="store_true",
        help="Only checkout, skip recursive submodule update",
    )
    checkout_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip git fetch before checkout",
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
        "Configure tiers in .config/grove.toml under [cascade].",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  grove cascade libs/common               Start cascade from a leaf submodule
  grove cascade libs/common --dry-run     Preview cascade chain and test plan
  grove cascade path1 path2               Cascade from multiple leaves (shared ancestors deduplicated)
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
        nargs="*",
        help="Path(s) to leaf submodule(s) to cascade from (1 or more)",
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
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview the cascade chain and test plan without making changes",
    )
    cascade_parser.add_argument(
        "--verbose",
        "-v",
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
        "--skip-checks",
        "-f",
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

    # --- grove config ---
    config_parser = subparsers.add_parser(
        "config",
        help="Manage Grove configuration files",
        description="Inspect and migrate Grove configuration files.",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    config_import_parser = config_subparsers.add_parser(
        "import-wt",
        help="Import WorkTrunk config into Grove config locations",
        description="Import WorkTrunk config into Grove's canonical user/project "
        "config files. Existing Grove config is merged by default.",
    )
    config_import_parser.add_argument(
        "--user",
        action="store_true",
        help="Import only the WorkTrunk user config",
    )
    config_import_parser.add_argument(
        "--project",
        action="store_true",
        help="Import only the WorkTrunk project config",
    )
    config_import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the imported Grove config without writing files",
    )
    config_import_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Replace the target Grove config instead of merging into it",
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
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview changes without modifying any files",
    )
    completion_install_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Re-write the completion block even if already installed",
    )

    # Store references for help printing (avoids argparse private API)
    parser.grove_subparsers = {
        "worktree": worktree_parser,
        "claude": claude_parser,
        "config": config_parser,
        "completion": completion_parser,
        "shell": shell_parser,
    }

    return parser
