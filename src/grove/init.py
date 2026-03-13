"""
grove.init
Generate a template project configuration file.
"""

from pathlib import Path

from grove.repo_utils import Colors
from grove.user_config import get_legacy_config_path, get_project_config_path

TEMPLATE = """\
# Grove configuration
# See: https://github.com/Cleanroom-Labs/grove for full documentation
#
# Config precedence (lowest -> highest):
#   1) ~/.config/grove/config.toml
#   2) .config/grove.toml
#   3) .grove.toml (legacy fallback; deprecated)

# Sync groups keep submodules at the same commit across all locations.
# Each group matches submodule URLs containing the url-match pattern.
#
# [sync-groups.<name>]
# url-match = "<pattern>"                                               # Required
# standalone-repo = "~/path/to/local/clone"                             # Optional: resolve latest commit locally
# commit-message = "chore: sync {group} submodule to {sha}"            # Optional: custom commit message
# allow-drift = ["path/to/submodule"]                                   # Optional: submodules allowed to diverge

# Worktree merge test commands run after each repo is merged.
# Test command resolution order (highest priority first):
#   1. test-overrides[repo's relative path]
#   2. Repo's own config test-command (.config/grove.toml; legacy fallback)
#   3. Root's test-command
#   4. No test command — skip testing
#
# [worktree-merge]
# test-command = "npm test"                                             # Default for all repos
#
# [worktree-merge.test-overrides]
# "." = "npm run build"                                                 # Override for root repo
# "submodule-name" = "make html"                                        # Override for a submodule
# "skip-this" = ""                                                      # Empty string = skip tests

# Worktree settings used by lifecycle commands.
#
# [worktree]
# backend = "auto"                                                     # auto | native | wt
# copy-venv = true                                                      # Copy Python venv (auto-detects location, fixes paths)
#
# worktree-path = "../{{ repo }}.{{ branch | sanitize }}"              # Optional default worktree path template
#
# [list]
# full = false
# branches = false
# remotes = false
# url = "https://github.com/org/repo/pull/{branch}"
#
# [commit]
# stage = "all"                                                         # all | tracked | none
#
# [commit.generation]
# command = "wt msg --prompt -"                                         # Read prompt from stdin, output commit message
#
# [merge]
# squash = true
# commit = true
# rebase = true
# remove = true
# verify = true
#
# [ci]
# platform = "github"                                                   # Optional

# Command aliases map short names to full subcommand strings.
# Example: `grove wm --status` expands to `grove worktree merge --status`.
#
# [aliases]
# wm = "worktree merge"
# wa = "worktree add"
# c = "check"
# s = "sync"
# p = "push"
#
# Hooks can be configured as a string:
# post-create = "grove worktree init-submodules {{ worktree_path }}"
#
# ...or as a named command table:
# [pre-merge]
# test = "npm test"
# lint = "npm run lint"
"""


def run(args):
    """Write a template Grove config file to the target directory."""
    target_dir = Path(args.path).resolve()

    if not target_dir.is_dir():
        print(Colors.red(f"Error: {target_dir} is not a directory."))
        return 1

    target_file = (
        get_legacy_config_path(target_dir)
        if getattr(args, "legacy", False)
        else get_project_config_path(target_dir)
    )
    if getattr(args, "legacy", False):
        print(
            f"{Colors.yellow('Warning')}: --legacy is deprecated; "
            "prefer .config/grove.toml."
        )
    target_file.parent.mkdir(parents=True, exist_ok=True)

    already_exists = target_file.exists()

    if already_exists and not args.force:
        print(Colors.red(f"Error: {target_file} already exists."))
        print(f"Use {Colors.blue('grove init --force')} to overwrite.")
        return 1

    target_file.write_text(TEMPLATE)
    action = "Overwrote" if already_exists else "Created"
    print(f"{action} {target_file}")
    return 0
