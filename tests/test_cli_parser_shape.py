"""Parser shape parity checks for CLI refactors."""

from grove.cli import build_parser
from grove.cli_parsers import WORKTREE_ALIASES
from grove.completion import extract_structure


def test_parser_shape_top_level_commands_are_stable():
    structure = extract_structure(build_parser())
    assert set(structure["commands"].keys()) == {
        "init",
        "check",
        "push",
        "sync",
        "visualize",
        "shell",
        "worktree",
        "checkout",
        "cascade",
        "claude",
        "config",
        "completion",
    }


def test_parser_shape_worktree_subcommands_are_stable():
    structure = extract_structure(build_parser())
    worktree = structure["commands"]["worktree"]["commands"]
    expected = {
        "add",
        "init-submodules",
        "switch",
        "list",
        "remove",
        "hook",
        "step",
        "merge",
        "checkout-branches",
    }
    for aliases in WORKTREE_ALIASES.values():
        expected.update(aliases)
    assert set(worktree.keys()) == expected


def test_parser_shape_step_subcommands_are_stable():
    structure = extract_structure(build_parser())
    step = structure["commands"]["worktree"]["commands"]["step"]["commands"]
    assert set(step.keys()) == {
        "commit",
        "squash",
        "push",
        "rebase",
        "diff",
        "copy-ignored",
        "for-each",
        "promote",
        "prune",
        "relocate",
    }
