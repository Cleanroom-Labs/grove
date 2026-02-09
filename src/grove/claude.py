"""
grove.claude
Install Claude Code skills shipped with grove.
"""

import filecmp
import subprocess
import tempfile
from importlib import resources
from pathlib import Path

from grove.repo_utils import Colors


def _find_project_root():
    """Find the git repo root for the current working directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _get_skill_files():
    """Return a dict of {filename: Traversable} for shipped skill files."""
    skills_pkg = resources.files("grove.claude_skills")
    return {
        item.name: item
        for item in skills_pkg.iterdir()
        if item.name.endswith(".md")
    }


def _resolve_target_dir(user_scope):
    """Resolve the target .claude/skills/ directory."""
    if user_scope:
        return Path.home() / ".claude" / "skills"
    root = _find_project_root()
    if root is None:
        print(Colors.red("Error: not inside a git repository."))
        print("Use --user to install to ~/.claude/skills/ instead.")
        return None
    return root / ".claude" / "skills"


def run_install(args):
    """Install Claude Code skills from grove's package data."""
    skills = _get_skill_files()
    if not skills:
        print(Colors.red("Error: no skill files found in grove package."))
        return 1

    target_dir = _resolve_target_dir(args.user)
    if target_dir is None:
        return 1

    if args.check:
        return _check_skills(skills, target_dir)

    return _install_skills(skills, target_dir)


def _check_skills(skills, target_dir):
    """Check if installed skills match shipped versions."""
    if not target_dir.exists():
        print(f"No skills installed at {target_dir}")
        print(f"Run {Colors.blue('grove claude install')} to install.")
        return 0

    all_current = True
    for name, source in sorted(skills.items()):
        installed = target_dir / name
        if not installed.exists():
            print(f"  {Colors.yellow('missing')}   {name}")
            all_current = False
        else:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
                tmp.write(source.read_text())
                tmp_path = tmp.name
            try:
                if filecmp.cmp(tmp_path, str(installed), shallow=False):
                    print(f"  {Colors.green('current')}   {name}")
                else:
                    print(f"  {Colors.yellow('outdated')}  {name}")
                    all_current = False
            finally:
                Path(tmp_path).unlink()

    if all_current:
        print(f"\n{Colors.green('All skills are up to date.')}")
    else:
        print(f"\nRun {Colors.blue('grove claude install')} to update.")
    return 0


def _install_skills(skills, target_dir):
    """Copy skill files to the target directory."""
    target_dir.mkdir(parents=True, exist_ok=True)

    installed = 0
    updated = 0
    unchanged = 0

    for name, source in sorted(skills.items()):
        dest = target_dir / name
        content = source.read_text()

        if dest.exists():
            if dest.read_text() == content:
                unchanged += 1
                continue
            updated += 1
        else:
            installed += 1

        dest.write_text(content)

    parts = []
    if installed:
        parts.append(f"{installed} installed")
    if updated:
        parts.append(f"{updated} updated")
    if unchanged:
        parts.append(f"{unchanged} unchanged")

    print(f"Skills: {', '.join(parts)}")
    print(f"Location: {target_dir}")
    return 0
