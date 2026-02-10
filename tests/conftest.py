"""Shared fixtures for grove tests."""

import subprocess
import sys
from pathlib import Path

import pytest

# Make the src/ directory importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command inside *cwd* and return the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a bare-bones temporary git repository with one initial commit.

    Returns the repository root path.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    # Create an initial commit so HEAD exists.
    readme = repo / "README.md"
    readme.write_text("# test repo\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")

    return repo


@pytest.fixture()
def tmp_submodule_tree(tmp_path: Path) -> Path:
    """Create a parent repo with nested submodules.

    Layout::

        parent/                     (main repo -- project root)
        +-- technical-docs/         (submodule pointing at child repo)
        |   +-- common/             (submodule pointing at grandchild repo)
        +-- .grove.toml        (optional config)

    Returns the *parent* repository path.
    """
    # ---- grandchild repo (stands in for a shared submodule) ----
    grandchild = tmp_path / "grandchild_origin"
    grandchild.mkdir()
    _git(grandchild, "init")
    _git(grandchild, "config", "user.email", "test@example.com")
    _git(grandchild, "config", "user.name", "Test User")
    (grandchild / "theme.txt").write_text("theme content\n")
    (grandchild / ".grove.toml").write_text(
        '[worktree-merge]\n'
        'test-command = "true"\n'
    )
    _git(grandchild, "add", "theme.txt", ".grove.toml")
    _git(grandchild, "commit", "-m", "Initial grandchild commit")

    # ---- child repo (stands in for technical-docs) ----
    child = tmp_path / "child_origin"
    child.mkdir()
    _git(child, "init")
    _git(child, "config", "user.email", "test@example.com")
    _git(child, "config", "user.name", "Test User")
    (child / "index.rst").write_text("index\n")
    (child / ".grove.toml").write_text(
        '[worktree-merge]\n'
        'test-command = "true"\n'
    )
    _git(child, "add", "index.rst", ".grove.toml")
    _git(child, "commit", "-m", "Initial child commit")

    # Add grandchild as a submodule named "common" inside child.
    _git(child, "submodule", "add", str(grandchild), "common")
    _git(child, "commit", "-m", "Add common submodule")

    # ---- parent repo (stands in for the project root) ----
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init")
    _git(parent, "config", "user.email", "test@example.com")
    _git(parent, "config", "user.name", "Test User")

    # Create .grove.toml with a sync group for testing.
    # The grandchild repo URL will be a local path; url-match uses a
    # substring that appears in that path.
    (parent / ".grove.toml").write_text(
        '[sync-groups.common]\n'
        f'url-match = "grandchild_origin"\n'
        f'standalone-repo = "{grandchild}"\n'
        '\n'
        '[worktree-merge]\n'
        'test-command = "true"\n'
    )

    _git(parent, "add", ".grove.toml")
    _git(parent, "commit", "-m", "Initial parent commit")

    # Add child as a submodule named "technical-docs" inside parent.
    _git(parent, "submodule", "add", str(child), "technical-docs")
    _git(parent, "commit", "-m", "Add technical-docs submodule")

    # Recursively initialise so the nested grandchild submodule is available.
    _git(parent, "submodule", "update", "--init", "--recursive")

    # Configure git user inside the submodule worktrees (needed for commits
    # that tests may make inside these directories).
    for sub in [parent / "technical-docs", parent / "technical-docs" / "common"]:
        if (sub / ".git").exists():
            _git(sub, "config", "user.email", "test@example.com")
            _git(sub, "config", "user.name", "Test User")

    return parent


@pytest.fixture()
def tmp_submodule_tree_with_branches(tmp_submodule_tree: Path) -> Path:
    """Extend tmp_submodule_tree by creating a ``my-feature`` branch in each
    repo with divergent commits, then switching back to ``main``.

    Layout after this fixture:

    - Each repo (parent, technical-docs, technical-docs/common) has a
      ``my-feature`` branch with one extra commit beyond ``main``.
    - All repos are checked out on their original branch (main-equivalent).

    Returns the *parent* (root) repository path.
    """
    parent = tmp_submodule_tree
    grandchild = parent / "technical-docs" / "common"
    child = parent / "technical-docs"

    # Submodules are in detached HEAD after init. Put them on a named
    # branch so merge operations can work on them.
    for sub in [grandchild, child]:
        # Try to checkout an existing main branch; create one if it doesn't exist
        result = subprocess.run(
            ["git", "-C", str(sub), "checkout", "main"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _git(sub, "checkout", "-b", "main")

    # Create feature branches with divergent commits, bottom-up

    # Grandchild
    _git(grandchild, "checkout", "-b", "my-feature")
    (grandchild / "feature.txt").write_text("grandchild feature\n")
    _git(grandchild, "add", "feature.txt")
    _git(grandchild, "commit", "-m", "grandchild feature commit")
    _git(grandchild, "checkout", "main")

    # Child
    _git(child, "checkout", "-b", "my-feature")
    (child / "feature.txt").write_text("child feature\n")
    _git(child, "add", "feature.txt")
    _git(child, "commit", "-m", "child feature commit")
    _git(child, "checkout", "main")

    # Parent (root)
    _git(parent, "checkout", "-b", "my-feature")
    (parent / "feature.txt").write_text("parent feature\n")
    _git(parent, "add", "feature.txt")
    _git(parent, "commit", "-m", "parent feature commit")
    _git(parent, "checkout", "main")

    return parent


@pytest.fixture()
def tmp_submodule_tree_with_sync_branches(tmp_submodule_tree: Path) -> Path:
    """Extend tmp_submodule_tree with feature branches that include
    sync-group submodule pointer changes.

    On the ``my-feature`` branch:

    - common has a content change (feature.txt)
    - technical-docs has a content change AND an updated common pointer
    - parent has a content change

    This simulates the real-world scenario where a feature branch updates
    a sync-group submodule, requiring sync propagation during merge.

    Returns the *parent* (root) repository path.
    """
    parent = tmp_submodule_tree
    grandchild = parent / "technical-docs" / "common"
    child = parent / "technical-docs"

    # Submodules are in detached HEAD after init. Put them on named branches.
    for sub in [grandchild, child]:
        result = subprocess.run(
            ["git", "-C", str(sub), "checkout", "main"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _git(sub, "checkout", "-b", "main")

    # --- Grandchild (common): content change on feature branch ---
    _git(grandchild, "checkout", "-b", "my-feature")
    (grandchild / "feature.txt").write_text("grandchild feature\n")
    _git(grandchild, "add", "feature.txt")
    _git(grandchild, "commit", "-m", "grandchild feature commit")
    grandchild_feature_sha = _git(
        grandchild, "rev-parse", "HEAD"
    ).stdout.strip()
    _git(grandchild, "checkout", "main")

    # --- Child (technical-docs): content change + updated common pointer ---
    _git(child, "checkout", "-b", "my-feature")
    (child / "feature.txt").write_text("child feature\n")
    _git(child, "add", "feature.txt")
    # Update common pointer to grandchild's feature commit
    _git(grandchild, "checkout", grandchild_feature_sha)
    _git(child, "add", "common")
    _git(child, "commit", "-m", "child feature commit with updated common")
    # Restore grandchild to main before switching child
    _git(grandchild, "checkout", "main")
    _git(child, "checkout", "-f", "main")

    # --- Parent (root): content change on feature branch ---
    _git(parent, "checkout", "-b", "my-feature")
    (parent / "feature.txt").write_text("parent feature\n")
    _git(parent, "add", "feature.txt")
    _git(parent, "commit", "-m", "parent feature commit")
    _git(parent, "checkout", "main")

    return parent
