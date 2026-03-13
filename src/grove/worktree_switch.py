"""
grove/worktree_switch.py
Native `grove worktree switch`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from grove.config import load_config
from grove.filelock import atomic_write_json
from grove.hooks import (
    has_configured_hooks,
    run_configured_hooks,
    warn_background_hook_native,
    warn_shell_only_hook_native,
)
from grove.repo_utils import Colors, find_repo_root, get_state_path, run_git
from grove.worktree_backend import maybe_delegate_switch
from grove.worktree_common import emit_switch_target, resolve_default_branch
from grove.worktree import add_worktree
from grove.worktree_list import collect_worktree_rows

_SWITCH_STATE_FILE = "worktree-switch-state.json"
_TEMPLATE_RE = re.compile(r"{{\s*(.*?)\s*}}")
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WT_ONLY_SHORTCUT_RE = re.compile(r"^(pr|mr):\d+$")


def generate_shell_wrapper(shell_name: str) -> str:
    """Generate a shell wrapper that applies grove directory directives."""
    if shell_name in ("bash", "zsh"):
        return f"""# grove shell integration ({shell_name})
# eval "$(grove shell init {shell_name})"
grove() {{
    local directive_file
    directive_file="$(mktemp)"
    command grove --directive-file "$directive_file" "$@"
    local exit_code=$?
    if [ -f "$directive_file" ]; then
        local target_dir
        target_dir="$(cat "$directive_file")"
        if [ -n "$target_dir" ]; then
            cd "$target_dir" || return $exit_code
        fi
        rm -f "$directive_file"
    fi
    return $exit_code
}}
"""

    if shell_name == "fish":
        return """# grove shell integration (fish)
# grove shell init fish | source
function grove
    set -l directive_file (mktemp)
    command grove --directive-file "$directive_file" $argv
    set -l exit_code $status
    if test -f "$directive_file"
        set -l target_dir (cat "$directive_file")
        if test -n "$target_dir"
            cd "$target_dir"
        end
        rm -f "$directive_file"
    end
    return $exit_code
end
"""

    raise ValueError(f"unsupported shell: {shell_name}")


def _manager_root(rows: list[dict], fallback: Path) -> Path:
    """Return the main worktree path used for shared state and branch ops."""
    if rows and rows[0].get("path"):
        return Path(rows[0]["path"]).resolve()
    return fallback


def _current_row(rows: list[dict]) -> dict | None:
    """Return the current worktree row."""
    return next((row for row in rows if row.get("is_current")), None)


def _state_path(manager_root: Path) -> Path:
    """Return the shared switch-state path rooted in the main worktree."""
    return get_state_path(manager_root, _SWITCH_STATE_FILE)


def _load_switch_state(manager_root: Path) -> dict:
    """Load the shared switch state."""
    path = _state_path(manager_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_switch_state(
    manager_root: Path,
    *,
    current_branch: str | None,
    current_path: Path | None,
    previous_branch: str | None,
    previous_path: Path | None,
) -> None:
    """Persist switch state for the `-` shortcut."""
    data = {
        "current_branch": current_branch,
        "current_path": str(current_path) if current_path else None,
        "previous_branch": previous_branch,
        "previous_path": str(previous_path) if previous_path else None,
    }
    atomic_write_json(_state_path(manager_root), json.dumps(data, indent=2))


def _sanitize_branch(branch: str) -> str:
    """Return a filesystem-safe branch token."""
    sanitized = _SANITIZE_RE.sub("-", branch).strip(".-")
    return sanitized or "worktree"


def _render_worktree_path(repo_root: Path, branch: str) -> Path:
    """Render the configured worktree path template for *branch*."""
    config = load_config(repo_root)
    template = config.worktree.worktree_path
    sanitized_branch = _sanitize_branch(branch)
    context = {
        "branch": branch,
        "repo": repo_root.name,
        "repo_path": str(repo_root),
        "worktree_name": sanitized_branch,
    }

    if not template:
        return repo_root.parent / f"{repo_root.name}.{sanitized_branch}"

    def _replace(match: re.Match[str]) -> str:
        expr = match.group(1)
        parts = [part.strip() for part in expr.split("|")]
        value = context.get(parts[0])
        if value is None:
            return match.group(0)
        rendered = str(value)
        for part in parts[1:]:
            if part == "sanitize":
                rendered = _sanitize_branch(rendered)
        return rendered

    rendered_path = Path(_TEMPLATE_RE.sub(_replace, template)).expanduser()
    if rendered_path.is_absolute():
        return rendered_path
    return (repo_root / rendered_path).resolve()


def _select_row_interactively(rows: list[dict]) -> dict | None:
    """Prompt the user to select a worktree/branch row."""
    if not rows:
        return None

    for index, row in enumerate(rows, start=1):
        branch = row.get("branch") or row.get("head_short") or "(unknown)"
        path = row.get("path") or "-"
        kind = row["kind"]
        marker = "*" if row.get("is_current") else " "
        print(f"{marker}{index:>2}. {branch:<20} {kind:<8} {path}")

    selection = input("Select worktree: ").strip()
    if not selection:
        return None
    if not selection.isdigit():
        print(f"{Colors.red('Error')}: expected a numeric selection")
        return None

    index = int(selection)
    if index < 1 or index > len(rows):
        print(f"{Colors.red('Error')}: selection out of range")
        return None
    return rows[index - 1]


def _resolve_branch_arg(
    manager_root: Path,
    rows: list[dict],
    branch_arg: str | None,
) -> tuple[str | None, dict | None]:
    """Resolve branch shortcuts or interactive selection to a branch/row."""
    current = _current_row(rows)
    default_branch = resolve_default_branch(manager_root, rows)

    if not branch_arg:
        row = _select_row_interactively(rows)
        if row is None:
            return (None, None)
        return (row.get("branch"), row)

    if branch_arg == "^":
        return (default_branch, None)
    if branch_arg == "@":
        if current is None:
            return (None, None)
        return (current.get("branch"), current)
    if branch_arg == "-":
        state = _load_switch_state(manager_root)
        previous_branch = state.get("previous_branch")
        previous_path = state.get("previous_path")
        if previous_branch:
            return (previous_branch, None)
        if previous_path:
            row = next((row for row in rows if row.get("path") == previous_path), None)
            return (row.get("branch") if row else None, row)
        return (None, None)

    row = next((row for row in rows if row.get("branch") == branch_arg), None)
    return (branch_arg, row)


def _is_wt_only_shortcut(branch_arg: str | None) -> bool:
    """Return True when branch_arg uses a wt-only remote shortcut format."""
    if not branch_arg:
        return False
    return _WT_ONLY_SHORTCUT_RE.match(branch_arg) is not None


def _branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True when *branch* exists locally."""
    result = run_git(
        repo_root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False
    )
    return result.returncode == 0


def _is_active_worktree_path(target_path: Path, rows: list[dict]) -> bool:
    """Return True when target_path is currently registered as a worktree path."""
    resolved = target_path.resolve()
    for row in rows:
        if row.get("kind") != "worktree" or not row.get("path"):
            continue
        if Path(row["path"]).resolve() == resolved:
            return True
    return False


def _clobber_target_path(target_path: Path, rows: list[dict]) -> bool:
    """Remove an existing target path for `switch --clobber`."""
    if _is_active_worktree_path(target_path, rows):
        print(
            f"{Colors.red('Error')}: cannot clobber active worktree path: {target_path}"
        )
        return False

    print(f"{Colors.yellow('Clobbering path')}: {target_path}")
    if target_path.is_symlink() or target_path.is_file():
        target_path.unlink()
    elif target_path.is_dir():
        shutil.rmtree(target_path)
    else:
        target_path.unlink()
    return True


def _run_execute(command: str, target_path: Path) -> int:
    """Run a post-switch command in the target worktree."""
    result = subprocess.run(command, shell=True, cwd=str(target_path))
    return result.returncode


def _run_switch_hook(
    manager_root: Path,
    args,
    *,
    hook_type: str,
    branch: str,
    target_path: Path,
    default_branch: str | None,
) -> int:
    """Run a configured switch hook."""
    if getattr(args, "no_verify", False):
        return 0
    if hook_type in {"pre-switch", "post-switch"}:
        if has_configured_hooks(manager_root, hook_type):
            warn_shell_only_hook_native(hook_type)
        return 0

    hook_vars = {
        "branch": branch,
        "worktree_path": str(target_path),
        "default_branch": default_branch or "",
    }
    if hook_type == "post-start" and has_configured_hooks(manager_root, hook_type):
        warn_background_hook_native(hook_type)
    return run_configured_hooks(
        manager_root,
        hook_type,
        variables=hook_vars,
        yes=getattr(args, "yes", False),
    )


def _save_switch_state_from_current(
    manager_root: Path,
    *,
    current: dict | None,
    current_branch: str | None,
    current_path: Path,
) -> None:
    """Persist switch-state history when moving away from an existing row."""
    if current is None or not current.get("path"):
        return
    if Path(current["path"]).resolve() == current_path:
        return

    _save_switch_state(
        manager_root,
        current_branch=current_branch,
        current_path=current_path,
        previous_branch=current.get("branch"),
        previous_path=Path(current["path"]).resolve(),
    )


def _finish_switch(args, target_path: Path) -> int:
    """Emit target path and optionally execute a command."""
    emit_switch_target(args, target_path)
    if getattr(args, "execute", None):
        return _run_execute(args.execute, target_path)
    return 0


def _switch_existing_worktree(
    manager_root: Path,
    args,
    *,
    row: dict,
    current: dict | None,
    default_branch: str | None,
) -> int:
    """Switch to an already-existing worktree row."""
    target_path = Path(row["path"]).resolve()
    branch = row.get("branch") or ""

    pre_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="pre-switch",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if pre_result != 0:
        return 1

    _save_switch_state_from_current(
        manager_root,
        current=current,
        current_branch=branch,
        current_path=target_path,
    )

    post_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="post-switch",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if post_result != 0:
        return 1

    return _finish_switch(args, target_path)


def _create_and_switch_worktree(
    manager_root: Path,
    args,
    *,
    branch: str,
    row: dict | None,
    rows: list[dict],
    current: dict | None,
    default_branch: str | None,
) -> int:
    """Create a missing worktree, then switch into it."""
    if not getattr(args, "create", False):
        print(
            f"{Colors.red('Error')}: no worktree exists for branch "
            f"{Colors.green(branch)}. Use {Colors.blue('grove worktree switch -c')} to create one."
        )
        return 1

    if row is not None and row["kind"] == "remote":
        print(
            f"{Colors.red('Error')}: native switch cannot create directly from remote-only ref "
            f"{Colors.green(branch)}"
        )
        return 1

    create_branch = not _branch_exists(manager_root, branch)
    base_branch = getattr(args, "base", None) or default_branch
    target_path = _render_worktree_path(manager_root, branch).resolve()

    if target_path.exists():
        if not getattr(args, "clobber", False):
            print(f"{Colors.red('Error')}: path already exists: {target_path}")
            return 1
        if not _clobber_target_path(target_path, rows):
            return 1

    pre_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="pre-switch",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if pre_result != 0:
        return 1

    add_args = argparse.Namespace(
        repo_root=str(manager_root),
        path=str(target_path),
        branch=branch,
        create_branch=create_branch,
        base=base_branch,
        copy_venv=False,
        no_local_remotes=False,
        exclude_sync_group=False,
    )
    result = add_worktree(add_args)
    if result != 0:
        return result

    post_create_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="post-create",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if post_create_result != 0:
        return 1

    post_start_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="post-start",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if post_start_result != 0:
        return 1

    post_switch_result = _run_switch_hook(
        manager_root,
        args,
        hook_type="post-switch",
        branch=branch,
        target_path=target_path,
        default_branch=default_branch,
    )
    if post_switch_result != 0:
        return 1

    _save_switch_state_from_current(
        manager_root,
        current=current,
        current_branch=branch,
        current_path=target_path,
    )
    return _finish_switch(args, target_path)


def switch_worktree(args) -> int:
    """Switch to an existing worktree or create one on demand."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    delegated = maybe_delegate_switch(repo_root, args)
    if delegated is not None:
        return delegated

    if _is_wt_only_shortcut(getattr(args, "branch", None)):
        print(
            f"{Colors.red('Error')}: shortcut {Colors.green(args.branch)} requires "
            "the worktrunk backend (wt)."
        )
        print('Set [worktree].backend = "wt" to enable PR/MR shortcuts.')
        return 1

    include_branches = bool(getattr(args, "branches", False))
    include_remotes = bool(getattr(args, "remotes", False))
    rows = collect_worktree_rows(
        repo_root,
        include_branches=include_branches,
        include_remotes=include_remotes,
    )
    manager_root = _manager_root(rows, repo_root)
    current = _current_row(rows)
    default_branch = resolve_default_branch(manager_root, rows)

    branch, row = _resolve_branch_arg(manager_root, rows, getattr(args, "branch", None))
    if branch is None and row is None:
        print(f"{Colors.red('Error')}: could not resolve a worktree target")
        return 1

    if row is None and branch is not None:
        row = next((entry for entry in rows if entry.get("branch") == branch), None)

    if row is not None and row["kind"] == "worktree":
        return _switch_existing_worktree(
            manager_root,
            args,
            row=row,
            current=current,
            default_branch=default_branch,
        )

    if branch is None:
        print(f"{Colors.red('Error')}: selected item cannot be switched to directly")
        return 1

    return _create_and_switch_worktree(
        manager_root,
        args,
        branch=branch,
        row=row,
        rows=rows,
        current=current,
        default_branch=default_branch,
    )


def run(args) -> int:
    """Module entry point."""
    return switch_worktree(args)
