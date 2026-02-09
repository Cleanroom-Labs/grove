"""
grove.init
Generate a template .grove.toml configuration file.
"""

from pathlib import Path

from grove.config import CONFIG_FILENAME
from grove.repo_utils import Colors

TEMPLATE = """\
# Grove configuration
# See: https://github.com/Cleanroom-Labs/grove for full documentation

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
#   2. Repo's own .grove.toml test-command
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
"""


def run(args):
    """Write a template .grove.toml to the target directory."""
    target_dir = Path(args.path).resolve()

    if not target_dir.is_dir():
        print(Colors.red(f"Error: {target_dir} is not a directory."))
        return 1

    target_file = target_dir / CONFIG_FILENAME

    already_exists = target_file.exists()

    if already_exists and not args.force:
        print(Colors.red(f"Error: {target_file} already exists."))
        print(f"Use {Colors.blue('grove init --force')} to overwrite.")
        return 1

    target_file.write_text(TEMPLATE)
    action = "Overwrote" if already_exists else "Created"
    print(f"{action} {target_file}")
    return 0
