"""
grove/worktree.py
Create and remove git worktrees with automatic submodule initialization.

Ports the zsh add-worktree/init_submodules_from_worktree helper (documented in
docs/submodule-workflow.md) to Python so it works from any shell.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from grove.repo_utils import Colors, find_repo_root, parse_gitmodules, run_git

# Prefixes of git config keys that are structural and should not be copied.
_CONFIG_EXCLUDE_PREFIXES = (
    "core.",
    "remote.",
    "submodule.",
    "extensions.",
    "gc.",
)


def _copy_local_config(source: Path, target: Path) -> None:
    """Copy local git config entries from *source* to *target*, skipping structural keys."""
    result = run_git(source, "config", "--local", "--list", check=False)
    if result.returncode != 0:
        return

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(key.startswith(prefix) for prefix in _CONFIG_EXCLUDE_PREFIXES):
            continue
        run_git(target, "config", key, value, check=False)


def _detect_venv(root: Path) -> Path | None:
    """Find a Python venv inside *root*, checking common locations.

    Search order: .direnv/python-* (direnv layout python), .venv/, venv/.
    Returns the venv directory or None.
    """
    # direnv layout python: .direnv/python-X.Y.Z/
    direnv_candidates = sorted(root.glob(".direnv/python-*"), reverse=True)
    for candidate in direnv_candidates:
        if (candidate / "pyvenv.cfg").exists():
            return candidate

    # .venv/ — could be a venv directly or contain a named subdirectory
    dot_venv = root / ".venv"
    if dot_venv.is_dir():
        if (dot_venv / "pyvenv.cfg").exists():
            return dot_venv
        # Named venv: .venv/<name>/pyvenv.cfg
        for child in dot_venv.iterdir():
            if child.is_dir() and (child / "pyvenv.cfg").exists():
                return child

    # venv/
    venv_dir = root / "venv"
    if venv_dir.is_dir() and (venv_dir / "pyvenv.cfg").exists():
        return venv_dir

    return None


def _fixup_venv_paths(venv_dir: Path, old_prefix: str, new_prefix: str) -> None:
    """Replace *old_prefix* with *new_prefix* in venv text files that contain hardcoded paths."""
    targets: list[Path] = []

    # Config and activate scripts
    targets.append(venv_dir / "pyvenv.cfg")
    for name in ("activate", "activate.csh", "activate.fish"):
        targets.append(venv_dir / "bin" / name)

    # Entry-point scripts in bin/ with shebangs referencing old_prefix
    bin_dir = venv_dir / "bin"
    if bin_dir.is_dir():
        for entry in bin_dir.iterdir():
            if entry.is_dir() or entry.is_symlink() or entry in targets:
                continue
            try:
                first_line = entry.read_bytes()[:256]
                if old_prefix.encode() in first_line:
                    targets.append(entry)
            except OSError:
                continue

    # Editable-install .pth files
    targets.extend(venv_dir.glob("lib/python*/site-packages/__editable__*.pth"))

    # direct_url.json in .dist-info dirs
    targets.extend(venv_dir.glob("lib/python*/site-packages/*.dist-info/direct_url.json"))

    for path in targets:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if old_prefix not in text:
            continue
        path.write_text(text.replace(old_prefix, new_prefix), encoding="utf-8")


def _copy_venv(source_root: Path, target_root: Path) -> bool:
    """Copy a detected Python venv from *source_root* to *target_root* and fix paths.

    Returns True if a venv was found and copied, False otherwise.
    """
    venv_dir = _detect_venv(source_root)
    if venv_dir is None:
        return False

    venv_rel = venv_dir.relative_to(source_root)
    target_venv = target_root / venv_rel

    # Ensure parent directories exist (e.g. .venv/ for .venv/myproject/)
    target_venv.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(venv_dir, target_venv, symlinks=True)
    _fixup_venv_paths(target_venv, str(source_root), str(target_root))
    return True


def _run_direnv_allow(worktree_path: Path) -> None:
    """Run ``direnv allow`` in *worktree_path* if .envrc exists and direnv is available."""
    if not (worktree_path / ".envrc").exists():
        return
    if shutil.which("direnv") is None:
        return

    print(f"{Colors.blue('Running direnv allow')} in worktree...")
    subprocess.run(
        ["direnv", "allow"],
        cwd=worktree_path,
        capture_output=True,
    )


def _init_submodules(worktree_path: Path, ref_worktree: Path, *, copy_config: bool = True) -> bool:
    """Recursively initialize submodules using the main worktree as reference.

    When the main worktree already has a submodule checked out, temporarily
    overrides the URL to point at that copy (avoiding redundant network
    fetches and resolving local filesystem URLs). Original URLs are restored
    via ``git submodule sync --recursive``.
    """
    gitmodules = worktree_path / ".gitmodules"
    if not gitmodules.exists():
        return True

    # git submodule init
    result = run_git(worktree_path, "submodule", "init", check=False)
    if result.returncode != 0:
        print(f"  {Colors.red('git submodule init failed')} in {worktree_path}")
        return False

    entries = parse_gitmodules(gitmodules)

    # Override each submodule URL to point to the main worktree's copy
    # (only when the reference path exists; otherwise let git use the
    # original URL from .gitmodules, which works for remote URLs)
    for name, subpath, _url in entries:
        ref_path = ref_worktree / subpath
        if ref_path.exists():
            run_git(
                worktree_path,
                "config", f"submodule.{name}.url", str(ref_path),
                check=False,
            )

    # git submodule update
    result = run_git(worktree_path, "submodule", "update", check=False)
    if result.returncode != 0:
        print(f"  {Colors.red('git submodule update failed')} in {worktree_path}")
        return False

    # Recurse into each submodule
    for _name, subpath, _url in entries:
        sub_worktree = worktree_path / subpath
        sub_ref = ref_worktree / subpath
        if not _init_submodules(sub_worktree, sub_ref, copy_config=copy_config):
            return False
        if copy_config and sub_ref.exists() and sub_worktree.exists():
            _copy_local_config(sub_ref, sub_worktree)

    # Restore original remote URLs at all levels
    run_git(worktree_path, "submodule", "sync", "--recursive", check=False)

    return True


def add_worktree(args) -> int:
    """Create a git worktree and recursively initialize submodules."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    worktree_path = Path(args.path).resolve()

    if worktree_path.exists():
        print(f"{Colors.red('Error')}: path already exists: {worktree_path}")
        return 1

    branch = args.branch

    # Build git worktree add command
    git_args = ["worktree", "add"]
    if not args.checkout:
        git_args.extend(["-b", branch, str(worktree_path)])
    else:
        git_args.extend([str(worktree_path), branch])

    print(f"{Colors.blue('Creating worktree')} at {worktree_path} on branch {Colors.green(branch)}...")

    result = run_git(repo_root, *git_args, check=False, capture=False)
    if result.returncode != 0:
        print(f"{Colors.red('Failed to create worktree')}")
        return 1

    copy_config = not getattr(args, "no_copy_config", False)

    if copy_config:
        print(f"{Colors.blue('Copying local git config')} to worktree...")
        _copy_local_config(repo_root, worktree_path)

    if getattr(args, "copy_venv", False):
        print(f"{Colors.blue('Copying Python venv')} from main worktree...")
        if _copy_venv(repo_root, worktree_path):
            print(f"  {Colors.green('Venv copied and paths updated')}")
        else:
            print(f"  {Colors.yellow('Warning')}: no Python venv found in {repo_root}")

    print(f"{Colors.blue('Initializing submodules')} (using main worktree as reference)...")

    if not _init_submodules(worktree_path, repo_root, copy_config=copy_config):
        print(f"\n{Colors.yellow('Warning')}: worktree created but submodule initialization failed.")
        print(f"  Path:   {worktree_path}")
        print(f"  Branch: {branch}")
        print("  You may need to initialize submodules manually.")
        return 1

    _run_direnv_allow(worktree_path)

    print(f"\n{Colors.green('Worktree created successfully')}")
    print(f"  Path:   {worktree_path}")
    print(f"  Branch: {branch}")
    return 0


def remove_worktree(args) -> int:
    """Remove a git worktree and prune stale entries."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    worktree_path = Path(args.path).resolve()

    git_args = ["worktree", "remove"]
    if args.force:
        git_args.append("--force")
    git_args.append(str(worktree_path))

    print(f"{Colors.blue('Removing worktree')} at {worktree_path}...")

    result = run_git(repo_root, *git_args, check=False, capture=False)
    if result.returncode != 0:
        print(f"{Colors.red('Failed to remove worktree')}")
        return 1

    # Prune stale worktree entries
    run_git(repo_root, "worktree", "prune", check=False)

    print(f"{Colors.green('Worktree removed successfully')}")
    return 0


def run(args) -> int:
    """Entry point for the worktree subcommand."""
    if args.worktree_command == "add":
        return add_worktree(args)
    if args.worktree_command == "remove":
        return remove_worktree(args)
    # Should not be reached (argparse handles unknown subcommands)
    return 2
