"""Tests for grove.shell."""

from types import SimpleNamespace
from unittest.mock import patch

from grove.shell import run


class TestShellInit:
    def test_init_bash_outputs_wrapper(self, capsys):
        args = SimpleNamespace(shell_command="init", shell_name="bash")

        result = run(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "grove shell integration (bash)" in output
        assert 'command grove --directive-file "$directive_file" "$@"' in output

    def test_init_zsh_outputs_wrapper(self, capsys):
        args = SimpleNamespace(shell_command="init", shell_name="zsh")

        result = run(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "grove shell integration (zsh)" in output

    def test_init_fish_outputs_wrapper(self, capsys):
        args = SimpleNamespace(shell_command="init", shell_name="fish")

        result = run(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "grove shell integration (fish)" in output
        assert "function grove" in output

    def test_detects_shell_from_env(self, capsys, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        args = SimpleNamespace(shell_command="init", shell_name=None)

        result = run(args)

        assert result == 0
        assert "grove shell integration (zsh)" in capsys.readouterr().out

    def test_unsupported_shell_returns_1(self, capsys):
        args = SimpleNamespace(shell_command="init", shell_name="tcsh")

        result = run(args)

        assert result == 1
        assert "unsupported shell" in capsys.readouterr().out

    def test_init_reuses_worktree_switch_wrapper_generator(self):
        args = SimpleNamespace(shell_command="init", shell_name="bash")

        with patch(
            "grove.shell.generate_shell_wrapper", return_value="# wrapper\n"
        ) as mock_generate:
            result = run(args)

        assert result == 0
        mock_generate.assert_called_once_with("bash")
