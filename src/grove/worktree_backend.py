"""
grove/worktree_backend.py
Optional delegation of lifecycle commands to WorkTrunk (`wt`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from grove.config import HOOK_TYPES, load_config
from grove.repo_utils import Colors
from grove.user_config import (
    dump_toml,
    iter_grove_config_paths,
    load_toml_file,
    merge_dicts,
)


def _resolve_backend(repo_root: Path) -> str:
    """Resolve backend mode for worktree lifecycle commands."""
    config = load_config(repo_root)
    mode = config.worktree.backend
    if mode == "wt":
        return "wt"
    if mode == "native":
        return "native"
    if _ensure_wt_available():
        return "wt"
    return "native"


def _run_wt_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run a `wt` command and return its exit code."""
    result = subprocess.run(command, check=False, env=env)
    return result.returncode


def _append_flag(cmd: list[str], args, attr: str, flag: str) -> None:
    if getattr(args, attr, False):
        cmd.append(flag)


def _ensure_wt_available() -> bool:
    """Return True when `wt` is available on PATH."""
    return shutil.which("wt") is not None


def _print_missing_wt_error() -> int:
    print(
        f"{Colors.red('Error')}: worktree.backend is set to 'wt' but "
        "the `wt` executable was not found on PATH."
    )
    return 1


def _load_raw_grove_config(repo_root: Path) -> dict:
    """Load and merge raw Grove config files with standard precedence."""
    raw: dict = {}
    for path in iter_grove_config_paths(repo_root):
        if not path.exists():
            continue
        raw = merge_dicts(raw, load_toml_file(path))
    return raw


def _synthesize_wt_config(raw: dict) -> dict:
    """Project Grove config onto WorkTrunk's config surface."""
    wt: dict = {}

    worktree_path = raw.get("worktree-path")
    if isinstance(worktree_path, str):
        wt["worktree-path"] = worktree_path
    elif isinstance(raw.get("worktree"), dict):
        nested = raw["worktree"].get("worktree-path")
        if isinstance(nested, str):
            wt["worktree-path"] = nested

    for key in ("list", "commit", "merge", "ci", "switch", "projects"):
        value = raw.get(key)
        if isinstance(value, dict):
            wt[key] = value

    hooks_raw = raw.get("hooks")
    if isinstance(hooks_raw, dict):
        wt["hooks"] = hooks_raw

    for hook_type in HOOK_TYPES:
        value = raw.get(hook_type)
        if isinstance(value, (str, dict)):
            wt[hook_type] = value

    return wt


@contextmanager
def _delegation_env(repo_root: Path):
    """Yield env vars for wt delegation, including synthesized config when needed."""
    raw = _load_raw_grove_config(repo_root)
    synthesized = _synthesize_wt_config(raw)
    if not synthesized:
        yield None
        return

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".toml",
        delete=False,
    ) as temp_file:
        temp_file.write(dump_toml(synthesized))
        temp_path = Path(temp_file.name)

    env = os.environ.copy()
    env["WORKTRUNK_CONFIG_PATH"] = str(temp_path)
    try:
        yield env
    finally:
        temp_path.unlink(missing_ok=True)


def _run_delegated(
    repo_root: Path,
    command: list[str],
    *,
    dry_run: bool = False,
) -> int:
    """Run delegated wt command with synthesized config context."""
    if dry_run:
        print(f"will run: {' '.join(command)}")
        return 0
    with _delegation_env(repo_root) as env:
        return _run_wt_command(command, env=env)


def maybe_delegate_switch(repo_root: Path, args) -> int | None:
    """Delegate `worktree switch` to wt when configured."""
    if _resolve_backend(repo_root) != "wt":
        return None
    if not _ensure_wt_available():
        return _print_missing_wt_error()

    cmd = ["wt", "switch"]
    if getattr(args, "branch", None):
        cmd.append(args.branch)

    _append_flag(cmd, args, "branches", "--branches")
    _append_flag(cmd, args, "remotes", "--remotes")
    _append_flag(cmd, args, "create", "--create")
    _append_flag(cmd, args, "yes", "--yes")
    _append_flag(cmd, args, "clobber", "--clobber")
    _append_flag(cmd, args, "no_cd", "--no-cd")
    _append_flag(cmd, args, "no_verify", "--no-verify")

    base = getattr(args, "base", None)
    if base:
        cmd.extend(["--base", base])

    execute = getattr(args, "execute", None)
    if execute:
        cmd.extend(["--execute", execute])

    return _run_delegated(repo_root, cmd, dry_run=bool(getattr(args, "dry_run", False)))


def maybe_delegate_list(repo_root: Path, args) -> int | None:
    """Delegate `worktree list` to wt when configured."""
    if _resolve_backend(repo_root) != "wt":
        return None
    if not _ensure_wt_available():
        return _print_missing_wt_error()

    cmd = ["wt", "list"]
    output_format = getattr(args, "format", None)
    if output_format:
        cmd.extend(["--format", output_format])
    _append_flag(cmd, args, "branches", "--branches")
    _append_flag(cmd, args, "remotes", "--remotes")
    _append_flag(cmd, args, "full", "--full")
    _append_flag(cmd, args, "progressive", "--progressive")
    return _run_delegated(repo_root, cmd, dry_run=bool(getattr(args, "dry_run", False)))


def maybe_delegate_remove(repo_root: Path, args) -> int | None:
    """Delegate `worktree remove` to wt when configured."""
    if _resolve_backend(repo_root) != "wt":
        return None
    if not _ensure_wt_available():
        return _print_missing_wt_error()

    cmd = ["wt", "remove"]
    targets = list(getattr(args, "targets", []) or [])
    legacy_path = getattr(args, "path", None)
    if legacy_path:
        targets.append(legacy_path)
    cmd.extend(targets)

    _append_flag(cmd, args, "force", "--force")
    _append_flag(cmd, args, "no_delete_branch", "--no-delete-branch")
    _append_flag(cmd, args, "force_delete", "--force-delete")
    _append_flag(cmd, args, "foreground", "--foreground")
    _append_flag(cmd, args, "no_verify", "--no-verify")
    _append_flag(cmd, args, "yes", "--yes")
    return _run_delegated(repo_root, cmd, dry_run=bool(getattr(args, "dry_run", False)))


def maybe_delegate_step(repo_root: Path, args) -> int | None:
    """Delegate `worktree step` to wt when configured."""
    if _resolve_backend(repo_root) != "wt":
        return None
    if not _ensure_wt_available():
        return _print_missing_wt_error()

    step_command = getattr(args, "step_command", None)
    if not step_command:
        print(
            f"{Colors.red('Error')}: missing step subcommand for "
            f"{Colors.blue('grove worktree step')}"
        )
        return 2

    cmd = ["wt", "step", step_command]

    if step_command in ("push", "rebase", "diff", "squash"):
        target = getattr(args, "target", None)
        if target:
            cmd.append(target)

    if step_command == "promote":
        branch = getattr(args, "branch", None)
        if branch:
            cmd.append(branch)

    if step_command == "relocate":
        cmd.extend(list(getattr(args, "branches", []) or []))

    if step_command == "for-each":
        command_args = list(getattr(args, "command_args", []) or [])
        if command_args and command_args[0] == "--":
            command_args = command_args[1:]
        cmd.extend(command_args)

    if step_command == "diff":
        extra_args = list(getattr(args, "extra_args", []) or [])
        if extra_args and extra_args[0] == "--":
            extra_args = extra_args[1:]
        if extra_args:
            cmd.append("--")
            cmd.extend(extra_args)

    if step_command in ("commit", "squash"):
        _append_flag(cmd, args, "yes", "--yes")
        _append_flag(cmd, args, "no_verify", "--no-verify")
        _append_flag(cmd, args, "show_prompt", "--show-prompt")
        stage = getattr(args, "stage", None)
        if stage:
            cmd.extend(["--stage", stage])

    if step_command == "copy-ignored":
        from_branch = getattr(args, "from_branch", None)
        to_branch = getattr(args, "to_branch", None)
        if from_branch:
            cmd.extend(["--from", from_branch])
        if to_branch:
            cmd.extend(["--to", to_branch])
        _append_flag(cmd, args, "dry_run", "--dry-run")
        _append_flag(cmd, args, "force", "--force")

    if step_command == "prune":
        _append_flag(cmd, args, "dry_run", "--dry-run")
        _append_flag(cmd, args, "yes", "--yes")
        _append_flag(cmd, args, "foreground", "--foreground")
        min_age = getattr(args, "min_age", None)
        if min_age:
            cmd.extend(["--min-age", min_age])

    if step_command == "relocate":
        _append_flag(cmd, args, "dry_run", "--dry-run")
        _append_flag(cmd, args, "commit", "--commit")
        _append_flag(cmd, args, "clobber", "--clobber")

    return _run_delegated(repo_root, cmd, dry_run=bool(getattr(args, "dry_run", False)))


def maybe_delegate_hook(repo_root: Path, args) -> int | None:
    """Delegate `worktree hook` to wt when configured."""
    if _resolve_backend(repo_root) != "wt":
        return None
    if not _ensure_wt_available():
        return _print_missing_wt_error()

    hook_type = getattr(args, "hook_type", None)
    if not hook_type:
        print(f"{Colors.red('Error')}: missing hook type for grove worktree hook")
        return 2

    cmd = ["wt", "hook", hook_type]
    name = getattr(args, "name", None)
    if name:
        cmd.append(name)

    if hook_type == "show":
        _append_flag(cmd, args, "expanded", "--expanded")
    else:
        _append_flag(cmd, args, "yes", "--yes")
        for raw_var in list(getattr(args, "var", []) or []):
            cmd.extend(["--var", raw_var])

    return _run_delegated(repo_root, cmd, dry_run=bool(getattr(args, "dry_run", False)))
