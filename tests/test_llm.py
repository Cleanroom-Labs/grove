"""Tests for grove.llm message generation helpers."""

from pathlib import Path

from grove.llm import generate_message


def _write_project_config(repo_root: Path, body: str) -> None:
    config_path = repo_root / ".config" / "grove.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body)


def test_generate_message_returns_none_when_not_configured(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert generate_message(repo_root, "prompt text") is None


def test_generate_message_uses_configured_command(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(
        repo_root,
        "[commit.generation]\ncommand = \"printf 'feat: generated\\n\\nbody line\\n'\"\n",
    )

    message = generate_message(repo_root, "prompt text")
    assert message == "feat: generated\n\nbody line"


def test_generate_message_warns_and_returns_none_on_failure(tmp_path: Path, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(repo_root, '[commit.generation]\ncommand = "exit 17"\n')

    assert generate_message(repo_root, "prompt text") is None
    output = capsys.readouterr().out
    assert "message generation command failed" in output


def test_generate_message_warns_and_returns_none_on_empty_output(
    tmp_path: Path, capsys
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_project_config(
        repo_root, '[commit.generation]\ncommand = "cat >/dev/null"\n'
    )

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
