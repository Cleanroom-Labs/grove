"""Tests for grove.claude (skill installation)."""

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from grove.claude import _get_skill_files, _install_skills, _skill_name, run_install


class TestSkillName:
    def test_strips_md_suffix(self):
        assert _skill_name("grove-add.md") == "grove-add"

    def test_no_suffix(self):
        assert _skill_name("grove-add") == "grove-add"

    def test_dotted_name(self):
        assert _skill_name("grove.md") == "grove"


class TestGetSkillFiles:
    def test_returns_dict(self):
        skills = _get_skill_files()
        assert isinstance(skills, dict)
        assert len(skills) > 0

    def test_all_keys_are_md(self):
        skills = _get_skill_files()
        for name in skills:
            assert name.endswith(".md"), f"{name} does not end with .md"

    def test_known_skill_present(self):
        """The grove-add skill should always exist."""
        skills = _get_skill_files()
        assert "grove-add.md" in skills


class TestInstallSkills:
    def test_installs_to_target(self, tmp_path: Path):
        """Skills should be installed as <name>/SKILL.md."""
        skills = _get_skill_files()
        target = tmp_path / "skills"

        _install_skills(skills, target)

        assert target.exists()
        for name in skills:
            skill_dir = target / _skill_name(name)
            assert (skill_dir / "SKILL.md").exists()

    def test_unchanged_on_reinstall(self, tmp_path: Path, capsys):
        """Reinstalling same skills should report unchanged."""
        skills = _get_skill_files()
        target = tmp_path / "skills"

        _install_skills(skills, target)
        _install_skills(skills, target)

        output = capsys.readouterr().out
        assert "unchanged" in output


class TestRunInstall:
    def test_check_mode_no_install(self, tmp_path: Path, capsys):
        """--check should report status without writing files."""
        target = tmp_path / ".claude" / "skills"
        args = SimpleNamespace(user=False, check=True)

        with patch("grove.claude._find_project_root", return_value=tmp_path):
            result = run_install(args)

        assert result == 0
        # No skills should be installed
        assert not target.exists()

    def test_install_creates_files(self, tmp_path: Path):
        """Install should create skill directories."""
        args = SimpleNamespace(user=False, check=False)

        with patch("grove.claude._find_project_root", return_value=tmp_path):
            result = run_install(args)

        assert result == 0
        target = tmp_path / ".claude" / "skills"
        assert target.exists()
        # At least one skill should exist
        skill_dirs = list(target.iterdir())
        assert len(skill_dirs) > 0

    def test_user_scope(self, tmp_path: Path):
        """--user should install to ~/.claude/skills/."""
        args = SimpleNamespace(user=True, check=False)

        with patch("grove.claude.Path.home", return_value=tmp_path):
            result = run_install(args)

        assert result == 0
        target = tmp_path / ".claude" / "skills"
        assert target.exists()
