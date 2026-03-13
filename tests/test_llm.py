"""Tests for grove.llm message generation helpers."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from grove.config import LLMConfig, LLMProviderEntry
from grove.llm import (
    LLMUnavailableError,
    _load_strands_runtime,
    _truncate,
    build_commit_prompt,
    build_squash_prompt,
    generate_message,
)


def _write_project_config(repo_root: Path, body: str) -> None:
    config_path = repo_root / ".config" / "grove.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body)


def _cp(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_generate_message_returns_none_when_not_configured(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert generate_message(repo_root, "prompt text") is None


def test_generate_message_uses_configured_command_with_stdin_prompt(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(repo_root, '[commit.generation]\ncommand = "fake-cmd"\n')

    with patch(
        "grove.llm.subprocess.run",
        return_value=_cp(returncode=0, stdout="feat: generated\n\nbody line\n"),
    ) as mock_run:
        message = generate_message(repo_root, "prompt text")

    assert message == "feat: generated\n\nbody line"
    assert mock_run.call_args.kwargs["input"] == "prompt text"
    assert mock_run.call_args.kwargs["cwd"] == str(repo_root)
    assert mock_run.call_args.kwargs["shell"] is True


def test_generate_message_warns_and_returns_none_on_failure(tmp_path: Path, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(repo_root, '[commit.generation]\ncommand = "fake-cmd"\n')

    with patch(
        "grove.llm.subprocess.run",
        return_value=_cp(returncode=17, stderr="boom"),
    ):
        assert generate_message(repo_root, "prompt text") is None
    output = capsys.readouterr().out
    assert "message generation command failed" in output


def test_generate_message_warns_and_returns_none_on_empty_output(
    tmp_path: Path, capsys
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(repo_root, '[commit.generation]\ncommand = "fake-cmd"\n')

    with patch(
        "grove.llm.subprocess.run",
        return_value=_cp(returncode=0, stdout=" \n"),
    ):
        assert generate_message(repo_root, "prompt text") is None
    output = capsys.readouterr().out
    assert "message generation returned empty output" in output


def test_generate_message_warns_and_returns_none_on_invalid_config(
    tmp_path: Path, capsys
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(repo_root, "[commit.generation]\ncommand = true\n")

    assert generate_message(repo_root, "prompt text") is None
    output = capsys.readouterr().out
    assert "failed to load config for message generation" in output


def test_generate_message_falls_back_to_strands_providers(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(
        repo_root,
        '[worktree.llm]\nproviders = [{ provider = "openai", model = "gpt-4" }]\n',
    )

    with patch("grove.llm._try_strands_providers", return_value="feat: from provider"):
        message = generate_message(repo_root, "prompt text")

    assert message == "feat: from provider"


def test_generate_message_prefers_command_before_providers(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(
        repo_root,
        '[commit.generation]\ncommand = "fake-cmd"\n\n'
        '[worktree.llm]\nproviders = [{ provider = "openai", model = "gpt-4" }]\n',
    )

    with (
        patch("grove.llm._run_generation_command", return_value="feat: command") as cmd,
        patch("grove.llm._try_strands_providers") as providers,
    ):
        message = generate_message(repo_root, "prompt text")

    assert message == "feat: command"
    cmd.assert_called_once()
    providers.assert_not_called()


def test_generate_message_handles_missing_strands_dependency(tmp_path: Path, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(
        repo_root,
        '[worktree.llm]\nproviders = [{ provider = "openai", model = "gpt-4" }]\n',
    )

    with patch(
        "grove.llm._try_strands_providers",
        side_effect=LLMUnavailableError("install grove[llm]"),
    ):
        message = generate_message(repo_root, "prompt text")

    assert message is None
    assert "install grove[llm]" in capsys.readouterr().out


def test_try_strands_providers_tries_next_provider_on_failure():
    llm_config = LLMConfig(
        providers=[
            LLMProviderEntry(provider="openai", model="gpt-4"),
            LLMProviderEntry(provider="ollama", model="llama3"),
        ]
    )

    with (
        patch("grove.llm._load_strands_runtime", return_value=object()),
        patch("grove.llm._build_model", side_effect=["first", "second"]),
        patch(
            "grove.llm._run_strands_prompt",
            side_effect=[RuntimeError("first failed"), "feat: provider ok"],
        ),
    ):
        from grove.llm import _try_strands_providers

        result = _try_strands_providers("prompt", llm_config)

    assert result == "feat: provider ok"


def test_load_strands_runtime_raises_llm_unavailable_when_missing():
    with patch("grove.llm.importlib.import_module", side_effect=ModuleNotFoundError):
        with pytest.raises(LLMUnavailableError):
            _load_strands_runtime()


def test_truncate_short_and_long_text():
    assert _truncate("abc", 10) == "abc"
    truncated = _truncate("x" * 50, 20)
    assert len(truncated) <= 20
    assert "[truncated]" in truncated


def test_build_commit_prompt_contains_expected_sections(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch(
        "grove.llm.run_git",
        side_effect=[
            _cp(returncode=0, stdout=" 1 file changed"),
            _cp(returncode=0, stdout="diff --git a b"),
            _cp(returncode=0, stdout="abc123 chore: setup"),
            _cp(returncode=0, stdout="feature\n"),
        ],
    ):
        prompt = build_commit_prompt(repo_root)

    assert "<diffstat>1 file changed</diffstat>" in prompt
    assert "<diff>diff --git a b</diff>" in prompt
    assert "Branch: feature" in prompt
    assert "<recent_commits>abc123 chore: setup</recent_commits>" in prompt


def test_build_commit_prompt_truncates_large_diff(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    huge_diff = "x" * 25_000

    with patch(
        "grove.llm.run_git",
        side_effect=[
            _cp(returncode=0, stdout="ok"),
            _cp(returncode=0, stdout=huge_diff),
            _cp(returncode=0, stdout="recent"),
            _cp(returncode=0, stdout="feature\n"),
        ],
    ):
        prompt = build_commit_prompt(repo_root)

    assert "[truncated]" in prompt


def test_build_squash_prompt_contains_expected_sections(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch(
        "grove.llm.run_git",
        side_effect=[
            _cp(returncode=0, stdout="abc123 feat: one"),
            _cp(returncode=0, stdout=" 2 files changed"),
            _cp(returncode=0, stdout="diff --git"),
            _cp(returncode=0, stdout="feature\n"),
        ],
    ):
        prompt = build_squash_prompt(repo_root, "base123", "main")

    assert 'target="main"' in prompt
    assert (
        '<commits branch="feature" target="main">abc123 feat: one</commits>' in prompt
    )
    assert "<diffstat>2 files changed</diffstat>" in prompt
    assert "<diff>diff --git</diff>" in prompt
