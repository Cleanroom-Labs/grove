"""Tests for grove.completion — shell completion script generation."""

import pytest

from grove.cli import build_parser
from grove.completion import (
    _generate_bash,
    _generate_fish,
    _generate_zsh,
    extract_structure,
)


# ---------------------------------------------------------------------------
# extract_structure
# ---------------------------------------------------------------------------

class TestExtractStructure:
    """Verify the introspection captures all commands and flags."""

    def setup_method(self):
        self.parser = build_parser()
        self.structure = extract_structure(self.parser)

    def test_top_level_commands(self):
        expected = {
            "init", "check", "push", "sync", "visualize",
            "worktree", "claude", "completion",
        }
        assert expected == set(self.structure["commands"].keys())

    def test_top_level_flags(self):
        assert "--no-color" in self.structure["flags"]

    def test_worktree_subcommands(self):
        wt = self.structure["commands"]["worktree"]
        assert {"add", "remove", "merge"} == set(wt["commands"].keys())

    def test_claude_subcommands(self):
        cl = self.structure["commands"]["claude"]
        assert "install" in cl["commands"]

    def test_sync_flags(self):
        sync_flags = self.structure["commands"]["sync"]["flags"]
        assert "--dry-run" in sync_flags
        assert "--no-push" in sync_flags
        assert "--force" in sync_flags

    def test_worktree_merge_flags(self):
        merge_flags = self.structure["commands"]["worktree"]["commands"]["merge"]["flags"]
        expected = {"--continue", "--abort", "--status", "--dry-run",
                    "--no-recurse", "--no-ff", "--no-test"}
        assert expected.issubset(set(merge_flags))

    def test_worktree_add_flags(self):
        add_flags = self.structure["commands"]["worktree"]["commands"]["add"]["flags"]
        assert "--checkout" in add_flags
        assert "--no-copy-config" in add_flags

    def test_completion_choices(self):
        comp = self.structure["commands"]["completion"]
        assert set(comp["choices"]) == {"bash", "zsh", "fish"}


# ---------------------------------------------------------------------------
# Bash completion
# ---------------------------------------------------------------------------

class TestBashCompletion:
    def setup_method(self):
        parser = build_parser()
        structure = extract_structure(parser)
        self.script = _generate_bash(structure)

    def test_contains_function_definition(self):
        assert "_grove_completion" in self.script

    def test_contains_complete_command(self):
        assert "complete -o default -F _grove_completion grove" in self.script

    def test_contains_all_subcommands(self):
        for cmd in ["init", "check", "push", "sync", "visualize",
                     "worktree", "claude", "completion"]:
            assert cmd in self.script

    def test_contains_nested_subcommands(self):
        for cmd in ["add", "remove", "merge", "install"]:
            assert cmd in self.script

    def test_contains_flags(self):
        for flag in ["--no-color", "--dry-run", "--force", "--verbose"]:
            assert flag in self.script

    def test_init_completion_fallback(self):
        assert "_init_completion" in self.script
        assert "COMPREPLY=()" in self.script


# ---------------------------------------------------------------------------
# Zsh completion
# ---------------------------------------------------------------------------

class TestZshCompletion:
    def setup_method(self):
        parser = build_parser()
        structure = extract_structure(parser)
        self.script = _generate_zsh(structure)

    def test_contains_compdef(self):
        assert "#compdef grove" in self.script

    def test_contains_compadd(self):
        assert "compadd" in self.script

    def test_contains_all_subcommands(self):
        for cmd in ["init", "check", "push", "sync", "visualize",
                     "worktree", "claude", "completion"]:
            assert cmd in self.script

    def test_contains_flags(self):
        for flag in ["--no-color", "--dry-run", "--force", "--verbose"]:
            assert flag in self.script


# ---------------------------------------------------------------------------
# Fish completion
# ---------------------------------------------------------------------------

class TestFishCompletion:
    def setup_method(self):
        parser = build_parser()
        structure = extract_structure(parser)
        self.script = _generate_fish(structure)

    def test_contains_complete_commands(self):
        assert "complete -c grove" in self.script

    def test_contains_all_subcommands(self):
        for cmd in ["init", "check", "push", "sync", "visualize",
                     "worktree", "claude", "completion"]:
            assert f"-a '{cmd}'" in self.script

    def test_contains_long_flags(self):
        for flag in ["no-color", "dry-run", "force", "verbose"]:
            assert f"-l '{flag}'" in self.script

    def test_contains_short_flags(self):
        assert "-s 'v'" in self.script

    def test_completion_choices(self):
        for shell in ["bash", "zsh", "fish"]:
            assert f"-a '{shell}'" in self.script


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCompletionCLI:
    def test_bash_returns_0(self, capsys):
        from grove.cli import main
        result = main(["completion", "bash"])
        assert result == 0
        assert "_grove_completion" in capsys.readouterr().out

    def test_zsh_returns_0(self, capsys):
        from grove.cli import main
        result = main(["completion", "zsh"])
        assert result == 0
        assert "#compdef grove" in capsys.readouterr().out

    def test_fish_returns_0(self, capsys):
        from grove.cli import main
        result = main(["completion", "fish"])
        assert result == 0
        assert "complete -c grove" in capsys.readouterr().out

    def test_invalid_shell_exits_2(self):
        from grove.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["completion", "powershell"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Regression guard — ensures every flag/command appears in generated scripts
# ---------------------------------------------------------------------------

class TestCompletionCoversAllTokens:
    """If a new flag or command is added to the parser, the generated scripts
    must include it. This test catches regressions automatically."""

    def _all_tokens(self, structure: dict) -> set[str]:
        """Recursively collect every flag, choice, and command name."""
        items = set(structure["flags"])
        items.update(structure.get("choices", []))
        items.update(structure["commands"].keys())
        for sub in structure["commands"].values():
            items.update(self._all_tokens(sub))
        return items

    def setup_method(self):
        parser = build_parser()
        self.structure = extract_structure(parser)
        self.tokens = self._all_tokens(self.structure)

    def test_bash_covers_all(self):
        script = _generate_bash(self.structure)
        for token in self.tokens:
            assert token in script, f"Bash script missing: {token}"

    def test_zsh_covers_all(self):
        script = _generate_zsh(self.structure)
        for token in self.tokens:
            assert token in script, f"Zsh script missing: {token}"

    def test_fish_covers_all(self):
        script = _generate_fish(self.structure)
        for token in self.tokens:
            # Fish expresses --flag as -l 'flag', so check both forms
            if token.startswith("--"):
                stripped = token[2:]
                assert stripped in script, f"Fish script missing: {token}"
            elif token.startswith("-") and len(token) == 2:
                assert f"-s '{token[1]}'" in script, f"Fish script missing: {token}"
            else:
                assert token in script, f"Fish script missing: {token}"
