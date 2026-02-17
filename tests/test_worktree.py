"""Tests for grove.worktree."""

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

from grove.repo_utils import parse_gitmodules
from grove.worktree import (
    _copy_venv,
    _detect_venv,
    _fixup_venv_paths,
    _init_submodules,
    _run_direnv_allow,
    add_worktree,
    remove_worktree,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True, text=True, check=True,
    )


# ---------------------------------------------------------------------------
# parse_gitmodules (shared from repo_utils, used by worktree)
# ---------------------------------------------------------------------------

class TestParseGitmodulesAll:
    def test_missing_file(self, tmp_path: Path):
        """Nonexistent .gitmodules should return empty list."""
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert result == []

    def test_empty_file(self, tmp_path: Path):
        """Empty .gitmodules should return empty list."""
        (tmp_path / ".gitmodules").write_text("")
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert result == []

    def test_single_submodule(self, tmp_path: Path):
        (tmp_path / ".gitmodules").write_text(
            '[submodule "technical-docs"]\n'
            "    path = technical-docs\n"
            "    url = /path/to/technical-docs\n"
        )
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert len(result) == 1
        name, path, url = result[0]
        assert name == "technical-docs"
        assert path == "technical-docs"
        assert url == "/path/to/technical-docs"

    def test_multiple_submodules(self, tmp_path: Path):
        """Should return ALL submodules when no url_match is given."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "technical-docs"]\n'
            "    path = technical-docs\n"
            "    url = /path/to/technical-docs\n"
            '[submodule "common"]\n'
            "    path = source/common\n"
            "    url = /path/to/common\n"
        )
        result = parse_gitmodules(tmp_path / ".gitmodules")
        assert len(result) == 2
        names = [r[0] for r in result]
        assert "technical-docs" in names
        assert "common" in names


# ---------------------------------------------------------------------------
# _init_submodules
# ---------------------------------------------------------------------------

class TestInitSubmodules:
    def test_no_gitmodules_returns_true(self, tmp_path: Path):
        """Directory without .gitmodules should succeed immediately."""
        worktree = tmp_path / "wt"
        worktree.mkdir()
        assert _init_submodules(worktree, tmp_path) is True


# ---------------------------------------------------------------------------
# add_worktree (integration)
# ---------------------------------------------------------------------------

class TestAddWorktree:
    def test_creates_worktree_directory(self, tmp_submodule_tree: Path):
        """Worktree directory should exist after add."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="test-branch", path=str(wt_path), create_branch=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        assert wt_path.exists()
        assert (wt_path / ".git").exists()

    def test_initializes_nested_submodules(self, tmp_submodule_tree: Path):
        """Nested submodules should be checked out in the new worktree."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="test-branch", path=str(wt_path), create_branch=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        # Level 2: technical-docs submodule
        assert (wt_path / "technical-docs" / ".git").exists()
        # Level 3: common submodule inside technical-docs
        assert (wt_path / "technical-docs" / "common" / ".git").exists()
        # Verify actual content from the grandchild repo
        assert (wt_path / "technical-docs" / "common" / "theme.txt").exists()

    def test_checkout_existing_branch(self, tmp_submodule_tree: Path):
        """--checkout should use an existing branch without creating a new one."""
        _git(tmp_submodule_tree, "branch", "existing-branch")

        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(branch="existing-branch", path=str(wt_path), create_branch=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        assert wt_path.exists()

    def test_local_remotes_is_default(self, tmp_submodule_tree: Path):
        """Default behavior keeps submodule origins pointing to the main worktree."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(
            branch="local-branch", path=str(wt_path), create_branch=True,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0

        # The worktree's submodule origin should point to the main worktree's submodule
        out = _git(wt_path / "technical-docs", "config", "--get", "remote.origin.url")
        assert out.stdout.strip() == str(tmp_submodule_tree / "technical-docs")

        # Nested submodule should also have local URL
        out = _git(
            wt_path / "technical-docs" / "common",
            "config", "--get", "remote.origin.url",
        )
        assert out.stdout.strip() == str(tmp_submodule_tree / "technical-docs" / "common")

    def test_no_local_remotes_restores_upstream_urls(self, tmp_submodule_tree: Path):
        """--no-local-remotes should restore submodule origins to upstream URLs."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args = argparse.Namespace(
            branch="upstream-branch", path=str(wt_path), create_branch=True, no_local_remotes=True,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0

        # The origin should NOT point to the main worktree's submodule
        out = _git(wt_path / "technical-docs", "config", "--get", "remote.origin.url")
        assert out.stdout.strip() != str(tmp_submodule_tree / "technical-docs")

    def test_path_already_exists_returns_1(self, tmp_submodule_tree: Path):
        """Should fail if the target path already exists."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        wt_path.mkdir()  # pre-create

        args = argparse.Namespace(branch="test-branch", path=str(wt_path), create_branch=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 1


# ---------------------------------------------------------------------------
# remove_worktree (integration)
# ---------------------------------------------------------------------------

class TestRemoveWorktree:
    def test_removes_worktree(self, tmp_submodule_tree: Path):
        """Worktree directory should be gone after remove."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "rm-branch", str(wt_path))
        assert wt_path.exists()

        args = argparse.Namespace(path=str(wt_path), force=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = remove_worktree(args)

        assert result == 0
        assert not wt_path.exists()

    def test_force_removes_dirty_worktree(self, tmp_submodule_tree: Path):
        """--force should remove a worktree with uncommitted changes."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        _git(tmp_submodule_tree, "worktree", "add", "-b", "dirty-branch", str(wt_path))
        (wt_path / "dirty.txt").write_text("uncommitted\n")

        args = argparse.Namespace(path=str(wt_path), force=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = remove_worktree(args)

        assert result == 0
        assert not wt_path.exists()

    def test_removes_worktree_with_submodules(self, tmp_submodule_tree: Path):
        """Worktree with initialized submodules should be removed successfully."""
        wt_path = tmp_submodule_tree.parent / "test-wt"
        args_add = argparse.Namespace(
            branch="rm-sub-branch", path=str(wt_path), create_branch=True,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            add_worktree(args_add)

        # Confirm submodules are initialized in the worktree
        assert (wt_path / "technical-docs" / ".git").exists()
        assert (wt_path / "technical-docs" / "common" / ".git").exists()

        args_rm = argparse.Namespace(path=str(wt_path), force=False)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = remove_worktree(args_rm)

        assert result == 0
        assert not wt_path.exists()


# ---------------------------------------------------------------------------
# Helper: create a minimal fake venv directory
# ---------------------------------------------------------------------------

def _make_fake_venv(root: Path, venv_rel: str = ".venv") -> Path:
    """Create a minimal fake venv under *root* / *venv_rel* for testing.

    Includes pyvenv.cfg, bin/activate, bin/python (symlink), bin/grove (script
    with shebang), and an editable-install .pth file — all containing
    the absolute path to *root* so that fixup can be verified.
    """
    venv = root / venv_rel
    venv.mkdir(parents=True)

    old_root = str(root)

    (venv / "pyvenv.cfg").write_text(
        f"home = /usr/bin\n"
        f"command = {old_root}/{venv_rel}/bin/python -m venv {old_root}/{venv_rel}\n"
    )

    bin_dir = venv / "bin"
    bin_dir.mkdir()

    (bin_dir / "activate").write_text(
        f'VIRTUAL_ENV="{old_root}/{venv_rel}"\nexport VIRTUAL_ENV\n'
    )
    (bin_dir / "activate.csh").write_text(
        f'setenv VIRTUAL_ENV "{old_root}/{venv_rel}"\n'
    )
    (bin_dir / "activate.fish").write_text(
        f'set -gx VIRTUAL_ENV "{old_root}/{venv_rel}"\n'
    )

    # Symlink: bin/python -> bin/python3 (should be preserved, not fixed)
    (bin_dir / "python3").write_text("not a real python\n")
    (bin_dir / "python").symlink_to("python3")

    # Script with shebang referencing the venv
    grove_script = bin_dir / "grove"
    grove_script.write_text(
        f"#!{old_root}/{venv_rel}/bin/python\n"
        f"# entry point\n"
    )

    # Editable install .pth file
    sp = venv / "lib" / "python3.14" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "__editable__.grove-0.1.0.pth").write_text(f"{old_root}/src\n")

    # direct_url.json
    di = sp / "grove-0.1.0.dist-info"
    di.mkdir()
    (di / "direct_url.json").write_text(
        f'{{"url": "file://{old_root}", "dir_info": {{"editable": true}}}}\n'
    )

    return venv


# ---------------------------------------------------------------------------
# _detect_venv
# ---------------------------------------------------------------------------

class TestDetectVenv:
    def test_detects_direnv_layout(self, tmp_path: Path):
        venv = tmp_path / ".direnv" / "python-3.14"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        assert _detect_venv(tmp_path) == venv

    def test_detects_dot_venv_direct(self, tmp_path: Path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        assert _detect_venv(tmp_path) == venv

    def test_detects_dot_venv_named(self, tmp_path: Path):
        venv = tmp_path / ".venv" / "myproject"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        assert _detect_venv(tmp_path) == venv

    def test_detects_venv(self, tmp_path: Path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        assert _detect_venv(tmp_path) == venv

    def test_returns_none_when_missing(self, tmp_path: Path):
        assert _detect_venv(tmp_path) is None

    def test_direnv_takes_priority(self, tmp_path: Path):
        """When both .direnv/python-* and .venv/ exist, .direnv wins."""
        direnv_venv = tmp_path / ".direnv" / "python-3.14"
        direnv_venv.mkdir(parents=True)
        (direnv_venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        dot_venv = tmp_path / ".venv"
        dot_venv.mkdir()
        (dot_venv / "pyvenv.cfg").write_text("home = /usr/bin\n")

        assert _detect_venv(tmp_path) == direnv_venv

    def test_picks_highest_direnv_version(self, tmp_path: Path):
        """When multiple .direnv/python-* exist, pick the highest version."""
        for ver in ("python-3.11", "python-3.14", "python-3.12"):
            d = tmp_path / ".direnv" / ver
            d.mkdir(parents=True)
            (d / "pyvenv.cfg").write_text("home = /usr/bin\n")

        result = _detect_venv(tmp_path)
        assert result is not None
        assert result.name == "python-3.14"


# ---------------------------------------------------------------------------
# _fixup_venv_paths
# ---------------------------------------------------------------------------

class TestFixupVenvPaths:
    def test_replaces_paths_in_all_targets(self, tmp_path: Path):
        old_root = tmp_path / "source"
        old_root.mkdir()
        venv = _make_fake_venv(old_root)

        new_root = tmp_path / "target"
        new_root.mkdir()
        new_venv = new_root / ".venv"

        # Copy the venv manually so we can test fixup in isolation
        import shutil
        shutil.copytree(venv, new_venv, symlinks=True)

        _fixup_venv_paths(new_venv, str(old_root), str(new_root))

        old = str(old_root)
        new = str(new_root)

        # pyvenv.cfg
        cfg = (new_venv / "pyvenv.cfg").read_text()
        assert new in cfg
        assert old not in cfg

        # activate scripts
        for name in ("activate", "activate.csh", "activate.fish"):
            text = (new_venv / "bin" / name).read_text()
            assert new in text
            assert old not in text

        # Shebang in grove script
        grove_text = (new_venv / "bin" / "grove").read_text()
        assert grove_text.startswith(f"#!{new}/")
        assert old not in grove_text

        # .pth file
        pth = (new_venv / "lib" / "python3.14" / "site-packages" / "__editable__.grove-0.1.0.pth").read_text()
        assert f"{new}/src" in pth
        assert old not in pth

        # direct_url.json
        durl = (new_venv / "lib" / "python3.14" / "site-packages" / "grove-0.1.0.dist-info" / "direct_url.json").read_text()
        assert new in durl
        assert old not in durl

    def test_preserves_symlinks(self, tmp_path: Path):
        """Symlinks in bin/ should not be modified by fixup."""
        old_root = tmp_path / "source"
        old_root.mkdir()
        venv = _make_fake_venv(old_root)

        # The python symlink should still point to python3
        assert (venv / "bin" / "python").is_symlink()
        assert (venv / "bin" / "python").resolve() == (venv / "bin" / "python3").resolve()

    def test_skips_missing_files_gracefully(self, tmp_path: Path):
        """Should not crash if expected files are missing."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        # No bin/ dir, no lib/ dir — should not crash
        _fixup_venv_paths(venv, "/old", "/new")


# ---------------------------------------------------------------------------
# _copy_venv
# ---------------------------------------------------------------------------

class TestCopyVenv:
    def test_copies_and_fixes_paths(self, tmp_path: Path):
        source = tmp_path / "source"
        source.mkdir()
        _make_fake_venv(source)

        target = tmp_path / "target"
        target.mkdir()

        assert _copy_venv(source, target) is True

        # Venv should exist in target
        target_venv = target / ".venv"
        assert target_venv.exists()
        assert (target_venv / "pyvenv.cfg").exists()

        # Paths should reference target, not source
        pth = (target_venv / "lib" / "python3.14" / "site-packages" / "__editable__.grove-0.1.0.pth").read_text()
        assert str(target) in pth
        assert str(source) not in pth

    def test_preserves_symlinks_in_copy(self, tmp_path: Path):
        source = tmp_path / "source"
        source.mkdir()
        _make_fake_venv(source)

        target = tmp_path / "target"
        target.mkdir()
        _copy_venv(source, target)

        python_link = target / ".venv" / "bin" / "python"
        assert python_link.is_symlink()

    def test_no_venv_returns_false(self, tmp_path: Path):
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        assert _copy_venv(source, target) is False

    def test_handles_nested_venv_path(self, tmp_path: Path):
        """A named venv like .venv/myproject/ should be copied correctly."""
        source = tmp_path / "source"
        source.mkdir()
        _make_fake_venv(source, venv_rel=".venv/myproject")

        target = tmp_path / "target"
        target.mkdir()

        assert _copy_venv(source, target) is True
        assert (target / ".venv" / "myproject" / "pyvenv.cfg").exists()


# ---------------------------------------------------------------------------
# _run_direnv_allow
# ---------------------------------------------------------------------------

class TestRunDirenvAllow:
    def test_calls_direnv_when_envrc_exists(self, tmp_path: Path):
        (tmp_path / ".envrc").write_text("layout python\n")

        with patch("grove.worktree.shutil.which", return_value="/usr/bin/direnv"), \
             patch("grove.worktree.subprocess.run") as mock_run:
            _run_direnv_allow(tmp_path)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["direnv", "allow"]
        assert call_args[1]["cwd"] == tmp_path

    def test_skips_when_no_envrc(self, tmp_path: Path):
        with patch("grove.worktree.subprocess.run") as mock_run:
            _run_direnv_allow(tmp_path)
        mock_run.assert_not_called()

    def test_skips_when_direnv_not_installed(self, tmp_path: Path):
        (tmp_path / ".envrc").write_text("layout python\n")

        with patch("grove.worktree.shutil.which", return_value=None), \
             patch("grove.worktree.subprocess.run") as mock_run:
            _run_direnv_allow(tmp_path)

        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Config-driven copy-venv
# ---------------------------------------------------------------------------

class TestConfigCopyVenv:
    def test_config_copy_venv_applies_when_flag_not_passed(self, tmp_submodule_tree: Path):
        """copy-venv = true in .grove.toml should trigger venv copy without CLI flag."""
        # Create a fake venv in the repo root
        _make_fake_venv(tmp_submodule_tree)

        # Add [worktree] section to existing .grove.toml
        existing_config = (tmp_submodule_tree / ".grove.toml").read_text()
        (tmp_submodule_tree / ".grove.toml").write_text(
            existing_config + "\n[worktree]\ncopy-venv = true\n"
        )

        wt_path = tmp_submodule_tree.parent / "cfg-venv-wt"
        args = argparse.Namespace(branch="cfg-venv-branch", path=str(wt_path), create_branch=True)

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        # Venv should have been copied (from config, not CLI flag)
        assert (wt_path / ".venv" / "pyvenv.cfg").exists()

    def test_cli_flag_works_without_config(self, tmp_submodule_tree: Path):
        """--copy-venv CLI flag should work even without [worktree] in config."""
        _make_fake_venv(tmp_submodule_tree)

        wt_path = tmp_submodule_tree.parent / "cli-venv-wt"
        args = argparse.Namespace(
            branch="cli-venv-branch", path=str(wt_path), create_branch=True,
            copy_venv=True,
        )

        with patch("grove.worktree.find_repo_root", return_value=tmp_submodule_tree):
            result = add_worktree(args)

        assert result == 0
        assert (wt_path / ".venv" / "pyvenv.cfg").exists()
