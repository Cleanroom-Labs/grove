"""Tests for grove.checkout."""

import subprocess
from pathlib import Path

from grove.checkout import run


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside *cwd* and return the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


def _make_args(path: str, ref: str, no_recurse: bool = False, no_fetch: bool = False):
    """Build a minimal namespace mimicking argparse output."""

    class Args:
        pass

    args = Args()
    args.path = path
    args.ref = ref
    args.no_recurse = no_recurse
    args.no_fetch = no_fetch
    return args


# ---------------------------------------------------------------------------
# Basic checkout operations
# ---------------------------------------------------------------------------


class TestCheckoutByBranch:
    def test_checkout_branch(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """Checkout a branch by name in a submodule."""
        child = tmp_submodule_tree / "technical-docs"

        # Create a new branch in the child origin with a new commit
        child_origin = Path(_git(child, "remote", "get-url", "origin").stdout.strip())
        _git(child_origin, "checkout", "-b", "test-branch")
        (child_origin / "new-file.txt").write_text("new content\n")
        _git(child_origin, "add", "new-file.txt")
        _git(child_origin, "commit", "-m", "Add new file on test-branch")
        _git(child_origin, "checkout", "main")

        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("technical-docs", "test-branch")
        result = run(args)
        assert result == 0

        # Verify we're on the right branch content
        assert (child / "new-file.txt").exists()

    def test_checkout_commit_sha(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """Checkout a specific commit SHA in a submodule."""
        child = tmp_submodule_tree / "technical-docs"

        # Get current SHA
        sha = _git(child, "rev-parse", "HEAD").stdout.strip()

        # Create a new commit in the origin
        child_origin = Path(_git(child, "remote", "get-url", "origin").stdout.strip())
        (child_origin / "extra.txt").write_text("extra\n")
        _git(child_origin, "add", "extra.txt")
        _git(child_origin, "commit", "-m", "Extra commit")

        monkeypatch.chdir(tmp_submodule_tree)
        # Checkout the original SHA (should succeed and leave us at that commit)
        args = _make_args("technical-docs", sha, no_fetch=True)
        result = run(args)
        assert result == 0

        current_sha = _git(child, "rev-parse", "HEAD").stdout.strip()
        assert current_sha == sha


# ---------------------------------------------------------------------------
# Recursive submodule update
# ---------------------------------------------------------------------------


class TestRecursiveUpdate:
    def test_submodules_updated_after_checkout(
        self, tmp_submodule_tree: Path, capsys, monkeypatch
    ):
        """After checkout, nested submodules should be initialized/updated."""
        child = tmp_submodule_tree / "technical-docs"
        grandchild = child / "common"

        # Verify the grandchild exists before checkout
        assert (grandchild / "theme.txt").exists()

        # Make a new commit in child_origin that updates common pointer
        child_origin = Path(_git(child, "remote", "get-url", "origin").stdout.strip())
        grandchild_origin = Path(
            _git(grandchild, "remote", "get-url", "origin").stdout.strip()
        )

        # Add a new commit to grandchild origin
        (grandchild_origin / "new-theme.txt").write_text("new theme\n")
        _git(grandchild_origin, "add", "new-theme.txt")
        _git(grandchild_origin, "commit", "-m", "Update theme")
        new_gc_sha = _git(grandchild_origin, "rev-parse", "HEAD").stdout.strip()

        # Update the common pointer in child_origin
        _git(child_origin, "checkout", "-b", "updated-common")
        child_common = child_origin / "common"
        _git(child_common, "fetch", "origin")
        _git(child_common, "checkout", new_gc_sha)
        _git(child_origin, "add", "common")
        _git(child_origin, "commit", "-m", "Update common pointer")

        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("technical-docs", "updated-common")
        result = run(args)
        assert result == 0

        # The grandchild should now have the new file
        assert (grandchild / "new-theme.txt").exists()

    def test_no_recurse_skips_submodule_update(
        self, tmp_submodule_tree: Path, capsys, monkeypatch
    ):
        """--no-recurse should skip submodule update."""
        child = tmp_submodule_tree / "technical-docs"

        # Get current SHA to checkout (just re-checkout current state)
        sha = _git(child, "rev-parse", "HEAD").stdout.strip()

        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("technical-docs", sha, no_recurse=True, no_fetch=True)
        result = run(args)
        assert result == 0

        captured = capsys.readouterr()
        # Should NOT contain submodule update messages
        assert "Updating" not in captured.out or "submodules" not in captured.out


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestCheckoutErrors:
    def test_invalid_ref(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """Checkout with a non-existent ref should return error."""
        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("technical-docs", "nonexistent-branch-xyz", no_fetch=True)
        result = run(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_invalid_path(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """Checkout with a non-existent path should return error."""
        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("nonexistent-path", "main")
        result = run(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "does not exist" in captured.out.lower()

    def test_path_not_git_repo(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """Checkout on a path that isn't a git repo should return error."""
        # Create a plain directory
        plain_dir = tmp_submodule_tree / "not-a-repo"
        plain_dir.mkdir()

        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("not-a-repo", "main")
        result = run(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "not a git repository" in captured.out.lower()


# ---------------------------------------------------------------------------
# No-fetch flag
# ---------------------------------------------------------------------------


class TestNoFetch:
    def test_no_fetch_skips_fetch(self, tmp_submodule_tree: Path, capsys, monkeypatch):
        """--no-fetch should skip the fetch step."""
        child = tmp_submodule_tree / "technical-docs"
        sha = _git(child, "rev-parse", "HEAD").stdout.strip()

        monkeypatch.chdir(tmp_submodule_tree)
        args = _make_args("technical-docs", sha, no_fetch=True)
        result = run(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Fetching" not in captured.out
