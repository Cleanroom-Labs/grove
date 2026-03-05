"""
grove/worktree.py
Create and remove git worktrees with automatic submodule initialization.

Ports the zsh add-worktree/init_submodules_from_worktree helper (documented in
docs/submodule-workflow.md) to Python so it works from any shell.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from grove.config import get_sync_group_exclude_paths, load_config
from grove.hooks import run_configured_hooks
from grove.repo_utils import (
    Colors,
    discover_repos_from_gitmodules,
    find_repo_root,
    parse_gitmodules,
    run_git,
)
from grove.worktree_backend import maybe_delegate_remove
from grove.worktree_common import resolve_default_branch
from grove.worktree_list import discover_worktrees


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
    targets.extend(
        venv_dir.glob("lib/python*/site-packages/*.dist-info/direct_url.json")
    )

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


def _init_submodules(
    worktree_path: Path,
    ref_worktree: Path,
    *,
    local_remotes: bool = True,
) -> bool:
    """Recursively initialize submodules using the main worktree as reference.

    When the main worktree already has a submodule checked out, temporarily
    overrides the URL to point at that copy (avoiding redundant network
    fetches and resolving local filesystem URLs). Unless *local_remotes* is
    True, original URLs are restored via ``git submodule sync --recursive``.
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
                "config",
                f"submodule.{name}.url",
                str(ref_path),
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
        if not _init_submodules(sub_worktree, sub_ref, local_remotes=local_remotes):
            return False

    if not local_remotes:
        # Restore original remote URLs at all levels
        run_git(worktree_path, "submodule", "sync", "--recursive", check=False)

    return True


def _checkout_submodule_branches(worktree_path: Path, branch: str) -> int:
    """Create matching branches in submodules.

    After ``_init_submodules`` leaves submodules in detached HEAD, this puts
    each submodule onto a named branch matching the parent worktree's branch.

    Returns the number of submodules checked out onto branches.
    """
    return _checkout_submodule_branches_with_options(
        worktree_path,
        branch,
        exclude_sync_groups=False,
    )


def _checkout_submodule_branches_with_options(
    worktree_path: Path,
    branch: str,
    *,
    exclude_sync_groups: bool,
) -> int:
    """Create matching branches in submodules, optionally excluding sync groups."""
    exclude_paths = None
    if exclude_sync_groups:
        config = load_config(worktree_path)
        exclude_paths = get_sync_group_exclude_paths(worktree_path, config)

    repos = discover_repos_from_gitmodules(
        worktree_path,
        exclude_paths=exclude_paths,
    )

    count = 0
    for repo in repos:
        if repo.path == worktree_path:
            continue  # skip root — already on the branch

        rel = str(repo.path.relative_to(worktree_path))

        result = run_git(repo.path, "checkout", "-b", branch, check=False)
        if result.returncode != 0:
            # Branch may already exist — try a plain checkout
            result = run_git(repo.path, "checkout", branch, check=False)
            if result.returncode != 0:
                print(
                    f"  {Colors.yellow('Warning')}: could not create branch "
                    f"'{branch}' in {rel}"
                )
                continue
        count += 1

    return count


def _resolve_current_branch(repo_root: Path) -> str | None:
    """Resolve the current branch of *repo_root*, or None if detached."""
    result = run_git(repo_root, "branch", "--show-current", check=False)
    branch = result.stdout.strip()
    return branch or None


def _initialize_worktree_submodules(
    worktree_path: Path,
    reference_path: Path,
    *,
    branch: str,
    local_remotes: bool,
    exclude_sync_groups: bool,
) -> bool:
    """Initialize submodules, checkout branches, and run direnv hooks."""
    print(
        f"{Colors.blue('Initializing submodules')} (using main worktree as reference)..."
    )

    if not _init_submodules(worktree_path, reference_path, local_remotes=local_remotes):
        print(
            f"\n{Colors.yellow('Warning')}: worktree created but submodule initialization failed."
        )
        print(f"  Path:   {worktree_path}")
        print(f"  Branch: {branch}")
        print("  You may need to initialize submodules manually.")
        return False

    if not local_remotes:
        print(
            f"{Colors.blue('Upstream remotes')}: submodule pushes will go directly to upstream"
        )

    branch_scope = "non-sync-group submodules" if exclude_sync_groups else "submodules"
    print(f"{Colors.blue('Creating branches')} in {branch_scope}...")
    branched = _checkout_submodule_branches_with_options(
        worktree_path,
        branch,
        exclude_sync_groups=exclude_sync_groups,
    )
    if branched:
        print(
            f"  {Colors.green(f'{branched} submodule(s) checked out')} onto branch {Colors.green(branch)}"
        )
    else:
        print(f"  {Colors.yellow('No submodules needed branch creation')}")

    _run_direnv_allow(worktree_path)
    return True


def init_submodules(args) -> int:
    """Initialize submodules and checkout matching branches in an existing worktree."""
    worktree_path = Path(args.path).resolve()
    if not worktree_path.exists():
        print(f"{Colors.red('Error')}: path does not exist: {worktree_path}")
        return 1
    if not (worktree_path / ".git").exists():
        print(f"{Colors.red('Error')}: not a git worktree: {worktree_path}")
        return 1

    if getattr(args, "reference", None):
        reference_path = Path(args.reference).resolve()
    else:
        try:
            reference_path = find_repo_root()
        except FileNotFoundError:
            reference_path = worktree_path

    if not reference_path.exists():
        print(f"{Colors.red('Error')}: reference path does not exist: {reference_path}")
        return 1

    branch = getattr(args, "branch", None) or _resolve_current_branch(worktree_path)
    if not branch:
        print(
            Colors.red(
                "Target worktree is in detached HEAD state. Use --branch to specify a branch name."
            )
        )
        return 1

    local_remotes = not getattr(args, "no_local_remotes", False)
    exclude_sync_groups = getattr(args, "exclude_sync_group", False)

    if not _initialize_worktree_submodules(
        worktree_path,
        reference_path,
        branch=branch,
        local_remotes=local_remotes,
        exclude_sync_groups=exclude_sync_groups,
    ):
        return 1

    print(f"\n{Colors.green('Submodules initialized successfully')}")
    print(f"  Path:   {worktree_path}")
    print(f"  Branch: {branch}")
    return 0


def add_worktree(args) -> int:
    """Create a git worktree and recursively initialize submodules."""
    repo_root = getattr(args, "repo_root", None)
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except FileNotFoundError as e:
            print(Colors.red(str(e)))
            return 1
    else:
        repo_root = Path(repo_root).resolve()

    worktree_path = Path(args.path).resolve()

    if worktree_path.exists():
        print(f"{Colors.red('Error')}: path already exists: {worktree_path}")
        return 1

    branch = args.branch

    # Build git worktree add command
    git_args = ["worktree", "add"]
    if args.create_branch:
        git_args.extend(["-b", branch, str(worktree_path)])
        base = getattr(args, "base", None)
        if base:
            git_args.append(base)
    else:
        git_args.extend([str(worktree_path), branch])

    print(
        f"{Colors.blue('Creating worktree')} at {worktree_path} on branch {Colors.green(branch)}..."
    )

    result = run_git(repo_root, *git_args, check=False, capture=False)
    if result.returncode != 0:
        print(f"{Colors.red('Failed to create worktree')}")
        return 1

    # Resolve copy-venv: CLI flag takes priority, then Grove config
    config = load_config(repo_root)
    copy_venv = getattr(args, "copy_venv", False) or config.worktree.copy_venv

    if copy_venv:
        print(f"{Colors.blue('Copying Python venv')} from main worktree...")
        if _copy_venv(repo_root, worktree_path):
            print(f"  {Colors.green('Venv copied and paths updated')}")
        else:
            print(f"  {Colors.yellow('Warning')}: no Python venv found in {repo_root}")

    local_remotes = not getattr(args, "no_local_remotes", False)
    exclude_sync_groups = getattr(args, "exclude_sync_group", False)

    if not _initialize_worktree_submodules(
        worktree_path,
        repo_root,
        branch=branch,
        local_remotes=local_remotes,
        exclude_sync_groups=exclude_sync_groups,
    ):
        return 1

    print(f"\n{Colors.green('Worktree created successfully')}")
    print(f"  Path:   {worktree_path}")
    print(f"  Branch: {branch}")
    return 0


def _remove_worktree_path(repo_root: Path, worktree_path: Path, *, force: bool) -> int:
    """Remove one worktree path and prune stale entries."""
    git_args = ["worktree", "remove"]
    if force:
        git_args.append("--force")
    git_args.append(str(worktree_path))

    print(f"{Colors.blue('Removing worktree')} at {worktree_path}...")

    result = run_git(repo_root, *git_args, check=False, capture=True)
    if result.returncode != 0:
        if "submodules" in result.stderr:
            dirty_repos = [
                repo.rel_path
                for repo in discover_repos_from_gitmodules(worktree_path)
                if repo.has_uncommitted_changes()
            ]
            if dirty_repos and not force:
                print(
                    f"{Colors.red('Failed to remove worktree')}: "
                    "submodule worktree has uncommitted changes."
                )
                print(
                    f"Use {Colors.blue('grove worktree remove --force')} "
                    "to remove it anyway."
                )
                for rel_path in dirty_repos:
                    print(f"  Dirty: {rel_path}")
                return 1
            # git worktree remove refuses when submodules are present.
            # Fall back to manual removal + prune.
            print(
                f"  {Colors.yellow('Worktree contains submodules')}; removing manually..."
            )
            try:
                cwd = Path.cwd().resolve()
            except FileNotFoundError:
                cwd = repo_root
            if cwd == worktree_path or cwd.is_relative_to(worktree_path):
                os.chdir(repo_root)
            shutil.rmtree(worktree_path)
        else:
            print(result.stderr.rstrip())
            print(f"{Colors.red('Failed to remove worktree')}")
            return 1

    # Prune stale worktree entries
    run_git(repo_root, "worktree", "prune", check=False)

    print(f"{Colors.green('Worktree removed successfully')}")
    return 0


def _has_local_branch(repo_root: Path, branch: str) -> bool:
    """Return True when *branch* exists locally."""
    result = run_git(
        repo_root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
    )
    return result.returncode == 0


def _resolve_branch_delete_target(repo_root: Path, default_branch: str) -> str:
    """Return the ref that branch-deletion safety checks should compare against."""
    upstream_result = run_git(
        repo_root,
        "for-each-ref",
        "--format=%(upstream:short)",
        f"refs/heads/{default_branch}",
        check=False,
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
    if not upstream:
        return f"refs/heads/{default_branch}"

    counts_result = run_git(
        repo_root,
        "rev-list",
        "--left-right",
        "--count",
        f"{upstream}...refs/heads/{default_branch}",
        check=False,
    )
    if counts_result.returncode != 0:
        return f"refs/heads/{default_branch}"

    behind_str, ahead_str = counts_result.stdout.strip().split()
    if int(behind_str) == 0 and int(ahead_str) > 0:
        return upstream

    return f"refs/heads/{default_branch}"


def _ref_value(repo_root: Path, ref: str) -> str | None:
    """Resolve a git ref to its SHA or tree SHA."""
    result = run_git(repo_root, "rev-parse", ref, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _branch_delete_disposition(
    repo_root: Path,
    branch: str,
    default_branch: str | None,
) -> tuple[bool, str]:
    """Return whether *branch* is safe to delete and why."""
    if not _has_local_branch(repo_root, branch):
        return (False, "branch does not exist locally")
    if default_branch is None or not _has_local_branch(repo_root, default_branch):
        return (False, "could not determine the default branch")
    if branch == default_branch:
        return (False, f"{branch} is the default branch")

    branch_ref = f"refs/heads/{branch}"
    default_ref = f"refs/heads/{default_branch}"

    branch_head = _ref_value(repo_root, branch_ref)
    default_head = _ref_value(repo_root, default_ref)
    if branch_head and default_head and branch_head == default_head:
        return (True, f"same commit as {default_branch}")

    target_ref = _resolve_branch_delete_target(repo_root, default_branch)

    ancestor_result = run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        branch_ref,
        target_ref,
        check=False,
    )
    if ancestor_result.returncode == 0:
        return (True, f"merged into {target_ref}")

    diff_result = run_git(
        repo_root,
        "diff",
        "--quiet",
        f"{target_ref}...{branch_ref}",
        "--",
        check=False,
    )
    if diff_result.returncode == 0:
        return (True, f"adds no changes relative to {target_ref}")

    branch_tree = _ref_value(repo_root, f"{branch_ref}^{{tree}}")
    target_tree = _ref_value(repo_root, f"{target_ref}^{{tree}}")
    if branch_tree and target_tree and branch_tree == target_tree:
        return (True, f"tree matches {target_ref}")

    merge_tree_result = run_git(
        repo_root,
        "merge-tree",
        "--write-tree",
        target_ref,
        branch_ref,
        check=False,
    )
    if (
        merge_tree_result.returncode == 0
        and target_tree
        and merge_tree_result.stdout.strip() == target_tree
    ):
        return (True, f"merging into {target_ref} adds no tree changes")

    return (False, f"contains changes not represented by {target_ref}")


def _delete_branch_if_requested(
    repo_root: Path,
    branch: str | None,
    *,
    default_branch: str | None,
    force_delete: bool,
    no_delete_branch: bool,
) -> bool:
    """Delete a branch after worktree removal when policy allows it."""
    if not branch:
        return True

    if no_delete_branch:
        print(f"  {Colors.yellow('Keeping branch')}: {branch}")
        return True

    safe_to_delete, reason = _branch_delete_disposition(
        repo_root, branch, default_branch
    )
    if not safe_to_delete and not force_delete:
        print(f"  {Colors.yellow('Keeping branch')}: {branch} ({reason})")
        print(
            f"  Use {Colors.blue('grove worktree remove -D')} {branch} to delete it anyway."
        )
        return True

    result = run_git(repo_root, "branch", "-D", branch, check=False)
    if result.returncode != 0:
        print(result.stderr.rstrip())
        print(f"  {Colors.red('Failed to delete branch')}: {branch}")
        return False

    suffix = "forced" if force_delete and not safe_to_delete else reason
    print(f"  {Colors.green('Deleted branch')}: {branch} ({suffix})")
    return True


def _resolve_remove_target(
    repo_root: Path,
    target: str,
    worktrees: list[dict],
) -> tuple[dict | None, str | None]:
    """Resolve a remove target to a worktree row plus its branch, if any."""
    by_branch = {
        row["branch"]: row for row in worktrees if row.get("branch") and row.get("path")
    }
    if target in by_branch:
        row = by_branch[target]
        return (row, row.get("branch"))

    if _has_local_branch(repo_root, target):
        return (None, target)

    resolved_target = Path(target).expanduser().resolve()
    by_path = {Path(row["path"]).resolve(): row for row in worktrees if row.get("path")}
    row = by_path.get(resolved_target)
    if row is not None:
        return (row, row.get("branch"))

    return (None, None)


def _resolve_remove_manager_root(repo_root: Path, worktrees: list[dict]) -> Path:
    """Return the manager root used for worktree removal operations."""
    if worktrees and worktrees[0].get("path"):
        return Path(worktrees[0]["path"]).resolve()
    return repo_root


def _resolve_remove_targets(args, current_row: dict | None) -> list[str] | None:
    """Resolve explicit removal targets, defaulting to current branch when omitted."""
    raw_targets = list(getattr(args, "targets", []) or [])
    legacy_path = getattr(args, "path", None)
    if legacy_path is not None:
        raw_targets.append(legacy_path)
    if raw_targets:
        return raw_targets

    if current_row is None:
        print(f"{Colors.red('Error')}: could not determine the current worktree")
        return None
    if not current_row.get("branch"):
        print(
            Colors.red(
                "Current worktree is detached. Pass a branch name or worktree path explicitly."
            )
        )
        return None
    return [current_row["branch"]]


def _run_remove_hook(
    manager_root: Path,
    args,
    *,
    hook_type: str,
    branch: str | None,
    worktree_path: Path,
    target: str,
    default_branch: str | None,
) -> int:
    """Run a configured remove hook and return its exit code."""
    if getattr(args, "no_verify", False):
        return 0

    variables = {
        "branch": branch or "",
        "worktree_path": str(worktree_path),
        "target": target,
        "default_branch": default_branch or "",
    }
    return run_configured_hooks(
        manager_root,
        hook_type,
        variables=variables,
        yes=getattr(args, "yes", False),
    )


def _remove_single_target(
    manager_root: Path,
    args,
    *,
    target: str,
    worktrees: list[dict],
    default_branch: str | None,
) -> int:
    """Remove a single target and return a per-target exit code."""
    row, branch = _resolve_remove_target(manager_root, target, worktrees)
    if row is None:
        if branch is not None:
            print(
                f"{Colors.red('Error')}: no worktree found for branch {Colors.green(branch)}"
            )
        else:
            print(
                f"{Colors.red('Error')}: no worktree found for target {Colors.green(target)}"
            )
        return 1

    worktree_path = Path(row["path"]).resolve()
    if row.get("is_main"):
        print(
            f"{Colors.red('Error')}: cannot remove the main worktree "
            f"({Colors.green(_display_remove_name(row, branch, target))})"
        )
        return 1

    pre_result = _run_remove_hook(
        manager_root,
        args,
        hook_type="pre-remove",
        branch=branch,
        worktree_path=worktree_path,
        target=target,
        default_branch=default_branch,
    )
    if pre_result != 0:
        return 1

    remove_result = _remove_worktree_path(
        manager_root,
        worktree_path,
        force=getattr(args, "force", False),
    )
    if remove_result != 0:
        return remove_result

    if not _delete_branch_if_requested(
        manager_root,
        branch,
        default_branch=default_branch,
        force_delete=getattr(args, "force_delete", False),
        no_delete_branch=getattr(args, "no_delete_branch", False),
    ):
        return 1

    post_result = _run_remove_hook(
        manager_root,
        args,
        hook_type="post-remove",
        branch=branch,
        worktree_path=worktree_path,
        target=target,
        default_branch=default_branch,
    )
    if post_result != 0:
        return 1

    return 0


def remove_worktree(args) -> int:
    """Remove git worktrees by branch, with path compatibility fallback."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    delegated = maybe_delegate_remove(repo_root, args)
    if delegated is not None:
        return delegated

    worktrees = discover_worktrees(repo_root)
    manager_root = _resolve_remove_manager_root(repo_root, worktrees)
    current_row = next((row for row in worktrees if row.get("is_current")), None)

    raw_targets = _resolve_remove_targets(args, current_row)
    if raw_targets is None:
        return 1

    default_branch = resolve_default_branch(manager_root, worktrees)
    exit_code = 0

    for target in raw_targets:
        result = _remove_single_target(
            manager_root,
            args,
            target=target,
            worktrees=worktrees,
            default_branch=default_branch,
        )
        if result != 0:
            exit_code = result

    return exit_code


def _display_remove_name(row: dict, branch: str | None, fallback: str) -> str:
    """Return the human-readable name for a removal target."""
    if branch:
        return branch
    if row.get("path"):
        return row["path"]
    return fallback


def checkout_branches(args) -> int:
    """Put submodules onto a named branch matching the parent worktree."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    # Determine target branch
    branch = getattr(args, "branch", None) or _resolve_current_branch(repo_root)
    if not branch:
        print(
            Colors.red(
                "Root worktree is in detached HEAD state. "
                "Use --branch to specify a branch name."
            )
        )
        return 1

    print(
        f"{Colors.blue('Checking out branches')} in submodules "
        f"(target: {Colors.green(branch)})..."
    )

    count = _checkout_submodule_branches_with_options(
        repo_root,
        branch,
        exclude_sync_groups=getattr(args, "exclude_sync_group", False),
    )

    if count:
        print(
            f"\n{Colors.green(f'{count} submodule(s)')} checked out "
            f"onto branch {Colors.green(branch)}"
        )
    else:
        print(f"\n{Colors.yellow('No submodules needed branch checkout')}")

    return 0


def run(args) -> int:
    """Entry point for the worktree subcommand."""
    if args.worktree_command == "add":
        return add_worktree(args)
    if args.worktree_command == "init-submodules":
        return init_submodules(args)
    if args.worktree_command == "switch":
        from grove.worktree_switch import run as run_switch

        return run_switch(args)
    if args.worktree_command == "list":
        from grove.worktree_list import run as run_list

        return run_list(args)
    if args.worktree_command == "remove":
        return remove_worktree(args)
    if args.worktree_command == "hook":
        from grove.hooks import run as run_hooks

        return run_hooks(args)
    if args.worktree_command == "step":
        from grove.worktree_step import run as run_step

        return run_step(args)
    if args.worktree_command == "checkout-branches":
        return checkout_branches(args)
    # Should not be reached (argparse handles unknown subcommands)
    return 2
