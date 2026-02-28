"""Tests for grove.completion — shell completion script generation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from grove.cli import build_parser
from grove.completion import (
    _build_profile_block,
    _detect_shell,
    _generate_bash,
    _generate_fish,
    _generate_zsh,
    _has_grove_block,
    _inject_block,
    _replace_block,
    _SENTINEL_BEGIN,
    _SENTINEL_END,
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
            "init",
            "check",
            "checkout",
            "push",
            "sync",
            "visualize",
            "worktree",
            "claude",
            "cascade",
            "completion",
        }
        assert expected == set(self.structure["commands"].keys())

    def test_top_level_flags(self):
        assert "--no-color" in self.structure["flags"]

    def test_worktree_subcommands(self):
        wt = self.structure["commands"]["worktree"]
        assert {"add", "remove", "merge", "checkout-branches"} == set(
            wt["commands"].keys()
        )

    def test_claude_subcommands(self):
        cl = self.structure["commands"]["claude"]
        assert "install" in cl["commands"]

    def test_sync_flags(self):
        sync_flags = self.structure["commands"]["sync"]["flags"]
        assert "--dry-run" in sync_flags
        assert "--no-push" in sync_flags
        assert "--skip-checks" in sync_flags

    def test_worktree_merge_flags(self):
        merge_flags = self.structure["commands"]["worktree"]["commands"]["merge"][
            "flags"
        ]
        expected = {
            "--continue",
            "--abort",
            "--status",
            "--dry-run",
            "--no-recurse",
            "--no-ff",
            "--no-test",
            "--verbose",
        }
        assert expected.issubset(set(merge_flags))

    def test_worktree_add_flags(self):
        add_flags = self.structure["commands"]["worktree"]["commands"]["add"]["flags"]
        assert "-b" in add_flags
        assert "--no-local-remotes" in add_flags

    def test_completion_subcommands(self):
        comp = self.structure["commands"]["completion"]
        assert {"bash", "zsh", "fish", "install"} == set(comp["commands"].keys())
        assert comp["choices"] == []


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
        for cmd in [
            "init",
            "check",
            "push",
            "sync",
            "visualize",
            "worktree",
            "claude",
            "completion",
        ]:
            assert cmd in self.script

    def test_contains_nested_subcommands(self):
        for cmd in ["add", "remove", "merge", "install"]:
            assert cmd in self.script

    def test_contains_flags(self):
        for flag in [
            "--no-color",
            "--dry-run",
            "--force",
            "--skip-checks",
            "--verbose",
        ]:
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
        for cmd in [
            "init",
            "check",
            "push",
            "sync",
            "visualize",
            "worktree",
            "claude",
            "completion",
        ]:
            assert cmd in self.script

    def test_contains_flags(self):
        for flag in [
            "--no-color",
            "--dry-run",
            "--force",
            "--skip-checks",
            "--verbose",
        ]:
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
        for cmd in [
            "init",
            "check",
            "push",
            "sync",
            "visualize",
            "worktree",
            "claude",
            "completion",
        ]:
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


# ---------------------------------------------------------------------------
# Completion install — helpers
# ---------------------------------------------------------------------------


class TestCompletionInstallHelpers:
    """Unit tests for install helper functions."""

    def test_detect_shell_bash(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/bash")
        assert _detect_shell() == "bash"

    def test_detect_shell_zsh(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert _detect_shell() == "zsh"

    def test_detect_shell_fish(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
        assert _detect_shell() == "fish"

    def test_detect_shell_unknown(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/csh")
        assert _detect_shell() is None

    def test_detect_shell_unset(self, monkeypatch):
        monkeypatch.delenv("SHELL", raising=False)
        assert _detect_shell() is None

    def test_has_grove_block_present(self):
        content = f"stuff\n{_SENTINEL_BEGIN}\neval ...\n{_SENTINEL_END}\nmore"
        assert _has_grove_block(content) is True

    def test_has_grove_block_absent(self):
        assert _has_grove_block("normal profile content\n") is False

    def test_build_profile_block(self):
        block = _build_profile_block("# my completion script\necho done")
        assert _SENTINEL_BEGIN in block
        assert _SENTINEL_END in block
        assert "# my completion script" in block
        assert "echo done" in block

    def test_inject_block_appends_with_separator(self):
        content = "existing line\n"
        block = "# new block\n"
        result = _inject_block(content, block)
        assert result.endswith(block)
        assert "existing line" in result
        # Should have blank line separator
        assert "\n\n# new block" in result

    def test_inject_block_handles_no_trailing_newline(self):
        content = "existing line"
        block = "# new block\n"
        result = _inject_block(content, block)
        assert result.endswith(block)
        assert "\n\n# new block" in result

    def test_inject_block_empty_content(self):
        result = _inject_block("", "# block\n")
        assert result == "# block\n"

    def test_replace_block(self):
        old = f"before\n{_SENTINEL_BEGIN}\nold stuff\n{_SENTINEL_END}\nafter\n"
        new_block = f"{_SENTINEL_BEGIN}\nnew stuff\n{_SENTINEL_END}\n"
        result = _replace_block(old, new_block)
        assert "old stuff" not in result
        assert "new stuff" in result
        assert "before" in result
        assert "after" in result


# ---------------------------------------------------------------------------
# Completion install — bash / zsh
# ---------------------------------------------------------------------------


class TestCompletionInstallBashZsh:
    """Integration tests for bash/zsh install."""

    def test_install_creates_zshrc(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            result = _install_bash_zsh("zsh", dry_run=False, force=False)

        assert result == 0
        zshrc = fake_home / ".zshrc"
        assert zshrc.exists()
        content = zshrc.read_text()
        assert "#compdef grove" in content  # static zsh completion script
        assert _SENTINEL_BEGIN in content
        assert "Created" in capsys.readouterr().out

    def test_install_appends_to_existing(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        zshrc = fake_home / ".zshrc"
        zshrc.write_text("export PATH=/usr/bin\n")

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            result = _install_bash_zsh("zsh", dry_run=False, force=False)

        assert result == 0
        content = zshrc.read_text()
        assert "export PATH=/usr/bin" in content
        assert "#compdef grove" in content  # static zsh completion script
        assert "Added" in capsys.readouterr().out

    def test_install_idempotent(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            _install_bash_zsh("zsh", dry_run=False, force=False)
            capsys.readouterr()

            result = _install_bash_zsh("zsh", dry_run=False, force=False)

        assert result == 0
        assert "already installed" in capsys.readouterr().out

    def test_install_force_rewrites(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            _install_bash_zsh("zsh", dry_run=False, force=False)
            capsys.readouterr()

            result = _install_bash_zsh("zsh", dry_run=False, force=True)

        assert result == 0
        assert "Updated" in capsys.readouterr().out

    def test_install_dry_run_no_write(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            result = _install_bash_zsh("zsh", dry_run=True, force=False)

        assert result == 0
        assert not (fake_home / ".zshrc").exists()
        assert "Would create" in capsys.readouterr().out

    def test_install_bash_prefers_bashrc(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        bashrc = fake_home / ".bashrc"
        bashrc.write_text("# bashrc\n")
        bash_profile = fake_home / ".bash_profile"
        bash_profile.write_text("# bash_profile\n")

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            _install_bash_zsh("bash", dry_run=False, force=False)

        assert "_grove_completion" in bashrc.read_text()
        assert "_grove_completion" not in bash_profile.read_text()

    def test_install_bash_falls_back_to_bash_profile(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        bash_profile = fake_home / ".bash_profile"
        bash_profile.write_text("# bash_profile\n")

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh

            _install_bash_zsh("bash", dry_run=False, force=False)

        assert "_grove_completion" in bash_profile.read_text()


# ---------------------------------------------------------------------------
# Completion install — fish
# ---------------------------------------------------------------------------


class TestCompletionInstallFish:
    """Integration tests for fish install."""

    def test_install_creates_fish_file(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_fish

            result = _install_fish(dry_run=False, force=False)

        assert result == 0
        fish_path = fake_home / ".config" / "fish" / "completions" / "grove.fish"
        assert fish_path.exists()
        assert "complete -c grove" in fish_path.read_text()
        assert "Installed" in capsys.readouterr().out

    def test_install_fish_idempotent(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_fish

            _install_fish(dry_run=False, force=False)
            capsys.readouterr()

            result = _install_fish(dry_run=False, force=False)

        assert result == 0
        assert "already installed" in capsys.readouterr().out

    def test_install_fish_force(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_fish

            _install_fish(dry_run=False, force=False)
            capsys.readouterr()

            result = _install_fish(dry_run=False, force=True)

        assert result == 0
        assert "Updated" in capsys.readouterr().out

    def test_install_fish_dry_run(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_fish

            result = _install_fish(dry_run=True, force=False)

        assert result == 0
        fish_path = fake_home / ".config" / "fish" / "completions" / "grove.fish"
        assert not fish_path.exists()
        assert "Would install" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Completion install — check
# ---------------------------------------------------------------------------


class TestCompletionCheck:
    """Tests for --check flag."""

    def test_check_missing(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _check_installed

            result = _check_installed("zsh")

        assert result == 0
        assert "missing" in capsys.readouterr().out

    def test_check_installed(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_bash_zsh, _check_installed

            _install_bash_zsh("zsh", dry_run=False, force=False)
            capsys.readouterr()

            result = _check_installed("zsh")

        assert result == 0
        assert "installed" in capsys.readouterr().out

    def test_check_fish_missing(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _check_installed

            result = _check_installed("fish")

        assert result == 0
        assert "missing" in capsys.readouterr().out

    def test_check_fish_installed(self, tmp_path, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            from grove.completion import _install_fish, _check_installed

            _install_fish(dry_run=False, force=False)
            capsys.readouterr()

            result = _check_installed("fish")

        assert result == 0
        assert "installed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Completion install — CLI integration
# ---------------------------------------------------------------------------


class TestCompletionInstallCLI:
    """End-to-end CLI tests for grove completion install."""

    def test_install_no_shell_detected(self, monkeypatch, capsys):
        monkeypatch.delenv("SHELL", raising=False)
        from grove.cli import main

        result = main(["completion", "install"])
        assert result == 1
        assert "could not detect shell" in capsys.readouterr().out

    def test_install_with_shell_override(self, tmp_path, monkeypatch, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.delenv("SHELL", raising=False)

        with patch.object(Path, "home", return_value=fake_home):
            from grove.cli import main

            result = main(["completion", "install", "--shell", "zsh"])

        assert result == 0
        assert (fake_home / ".zshrc").exists()

    def test_completion_bash_still_works(self, capsys):
        from grove.cli import main

        result = main(["completion", "bash"])
        assert result == 0
        assert "_grove_completion" in capsys.readouterr().out

    def test_completion_zsh_still_works(self, capsys):
        from grove.cli import main

        result = main(["completion", "zsh"])
        assert result == 0
        assert "#compdef grove" in capsys.readouterr().out

    def test_completion_no_subcommand_returns_2(self, capsys):
        from grove.cli import main

        result = main(["completion"])
        assert result == 2
