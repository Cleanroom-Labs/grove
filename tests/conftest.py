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


def _init_repo(path: Path) -> None:
    """Create a git repo at *path* with git user config and an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text(f"# {path.name}\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "Initial commit")


@pytest.fixture()
def tmp_sync_group_multi_instance(tmp_path: Path) -> Path:
    """Create a tree with a sync-group submodule in three separate parents.

    Layout::

        root/                              (main repo -- project root)
        +-- frontend/                      (submodule → frontend_origin)
        |   +-- libs/common/               (submodule → common_origin)
        +-- backend/                       (submodule → backend_origin)
        |   +-- libs/common/               (submodule → common_origin)
        +-- shared/                        (submodule → shared_origin)
        |   +-- libs/common/               (submodule → common_origin)
        +-- .grove.toml

    All three ``libs/common`` instances point to the same origin repo,
    creating a sync group with three instances.

    Returns the *root* repository path.
    """
    # ---- common_origin: the shared library ----
    common_origin = tmp_path / "common_origin"
    _init_repo(common_origin)
    (common_origin / "lib.py").write_text("def hello(): return 'hello'\n")
    _git(common_origin, "add", "lib.py")
    _git(common_origin, "commit", "-m", "Add library code")

    # ---- Three parent repos, each with libs/common as submodule ----
    parent_names = ["frontend_origin", "backend_origin", "shared_origin"]
    for name in parent_names:
        origin = tmp_path / name
        _init_repo(origin)
        (origin / "app.py").write_text(f"# {name} app\n")
        _git(origin, "add", "app.py")
        _git(origin, "commit", "-m", f"Add {name} app code")
        _git(origin, "submodule", "add", str(common_origin), "libs/common")
        _git(origin, "commit", "-m", "Add libs/common submodule")

    # ---- root repo: adds all three as submodules ----
    root = tmp_path / "root"
    _init_repo(root)

    (root / ".grove.toml").write_text(
        '[sync-groups.common]\n'
        f'url-match = "common_origin"\n'
        f'standalone-repo = "{common_origin}"\n'
        '\n'
        '[cascade]\n'
        'local-tests = "true"\n'
        'contract-tests = "true"\n'
    )
    _git(root, "add", ".grove.toml")
    _git(root, "commit", "-m", "Add grove config")

    for sub_name, origin_name in [
        ("frontend", "frontend_origin"),
        ("backend", "backend_origin"),
        ("shared", "shared_origin"),
    ]:
        _git(root, "submodule", "add", str(tmp_path / origin_name), sub_name)

    _git(root, "commit", "-m", "Add frontend, backend, shared submodules")

    # Recursively initialise all nested submodules
    _git(root, "submodule", "update", "--init", "--recursive")

    # Configure git user in all submodule worktrees
    for sub_dir in [
        root / "frontend",
        root / "frontend" / "libs" / "common",
        root / "backend",
        root / "backend" / "libs" / "common",
        root / "shared",
        root / "shared" / "libs" / "common",
    ]:
        if (sub_dir / ".git").exists():
            _git(sub_dir, "config", "user.email", "test@example.com")
            _git(sub_dir, "config", "user.name", "Test User")

    return root


@pytest.fixture()
def tmp_sync_group_diverged(tmp_sync_group_multi_instance: Path) -> Path:
    """Extend multi-instance fixture with diverged commits in common instances.

    After this fixture:
    - frontend/libs/common has commit A (feature-a.txt)
    - backend/libs/common has commit B (feature-b.txt)
    - shared/libs/common is unchanged (at original commit)

    Commits A and B are siblings (neither is ancestor of the other),
    so ``resolve_local_tip()`` will return None (diverged).

    Returns the *root* repository path.
    """
    root = tmp_sync_group_multi_instance

    # Put the common instances on branches so we can commit
    for parent_name in ["frontend", "backend"]:
        common = root / parent_name / "libs" / "common"
        # Create a branch from detached HEAD
        _git(common, "checkout", "-b", f"{parent_name}-work")

    # Diverge: different commits in each instance
    frontend_common = root / "frontend" / "libs" / "common"
    (frontend_common / "feature-a.txt").write_text("feature A\n")
    _git(frontend_common, "add", "feature-a.txt")
    _git(frontend_common, "commit", "-m", "Add feature A")

    backend_common = root / "backend" / "libs" / "common"
    (backend_common / "feature-b.txt").write_text("feature B\n")
    _git(backend_common, "add", "feature-b.txt")
    _git(backend_common, "commit", "-m", "Add feature B")

    return root


@pytest.fixture()
def tmp_intermediate_sync_group(tmp_path: Path) -> Path:
    """Create a tree where intermediate repos (not leaves) form a sync group.

    Layout::

        root/                              (main repo -- project root)
        +-- workspace-a/                   (submodule → service_origin)
        |   +-- libs/common/               (submodule → common_origin)
        +-- workspace-b/                   (submodule → service_origin, same origin!)
        |   +-- libs/common/               (submodule → common_origin)
        +-- .grove.toml

    ``workspace-a`` and ``workspace-b`` share the same origin (service_origin),
    forming a sync group called "services".  They are intermediates in the
    cascade chain (libs/common → workspace-{a,b} → root).

    Returns the *root* repository path.
    """
    # ---- common_origin: the shared library (leaf) ----
    common_origin = tmp_path / "common_origin"
    _init_repo(common_origin)
    (common_origin / "lib.py").write_text("def hello(): return 'hello'\n")
    _git(common_origin, "add", "lib.py")
    _git(common_origin, "commit", "-m", "Add library code")

    # ---- service_origin: the shared intermediate repo ----
    service_origin = tmp_path / "service_origin"
    _init_repo(service_origin)
    (service_origin / "service.py").write_text("# service code\n")
    _git(service_origin, "add", "service.py")
    _git(service_origin, "commit", "-m", "Add service code")
    _git(service_origin, "submodule", "add", str(common_origin), "libs/common")
    _git(service_origin, "commit", "-m", "Add libs/common submodule")

    # ---- root repo ----
    root = tmp_path / "root"
    _init_repo(root)

    (root / ".grove.toml").write_text(
        '[sync-groups.services]\n'
        f'url-match = "service_origin"\n'
        '\n'
        '[cascade]\n'
        'local-tests = "true"\n'
        'contract-tests = "true"\n'
    )
    _git(root, "add", ".grove.toml")
    _git(root, "commit", "-m", "Add grove config")

    # Both workspaces point to the SAME service_origin
    _git(root, "submodule", "add", str(service_origin), "workspace-a")
    _git(root, "submodule", "add", str(service_origin), "workspace-b")
    _git(root, "commit", "-m", "Add workspace-a and workspace-b submodules")

    # Recursively initialise all nested submodules
    _git(root, "submodule", "update", "--init", "--recursive")

    # Configure git user in all submodule worktrees
    for sub_dir in [
        root / "workspace-a",
        root / "workspace-a" / "libs" / "common",
        root / "workspace-b",
        root / "workspace-b" / "libs" / "common",
    ]:
        if (sub_dir / ".git").exists():
            _git(sub_dir, "config", "user.email", "test@example.com")
            _git(sub_dir, "config", "user.name", "Test User")

    return root


@pytest.fixture()
def tmp_intermediate_sync_group_diverged(tmp_intermediate_sync_group: Path) -> Path:
    """Extend intermediate sync group fixture with diverged workspace commits.

    After this fixture:
    - workspace-a has an extra commit (extra-a.txt)
    - workspace-b has a different extra commit (extra-b.txt)

    The "services" sync group has diverged instances (neither is ancestor
    of the other).

    Returns the *root* repository path.
    """
    root = tmp_intermediate_sync_group

    # Put workspaces on branches so we can commit
    for ws_name in ["workspace-a", "workspace-b"]:
        ws = root / ws_name
        _git(ws, "checkout", "-b", f"{ws_name}-work")

    # Diverge: different commits in each workspace
    ws_a = root / "workspace-a"
    (ws_a / "extra-a.txt").write_text("workspace A extra\n")
    _git(ws_a, "add", "extra-a.txt")
    _git(ws_a, "commit", "-m", "Add extra-a.txt")

    ws_b = root / "workspace-b"
    (ws_b / "extra-b.txt").write_text("workspace B extra\n")
    _git(ws_b, "add", "extra-b.txt")
    _git(ws_b, "commit", "-m", "Add extra-b.txt")

    return root
