"""
grove/worktree_common.py
Shared helpers for worktree lifecycle modules.
"""

from __future__ import annotations

from pathlib import Path

from grove.repo_utils import Colors, run_git


def resolve_default_branch(
    repo_root: Path,
    rows: list[dict] | None = None,
    *,
    git_runner=run_git,
) -> str | None:
    """Resolve repository default branch (origin/HEAD, then main row/current branch)."""
    origin_head = git_runner(
        repo_root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "refs/remotes/origin/HEAD",
        check=False,
    )
    if origin_head.returncode == 0 and origin_head.stdout.strip():
        return origin_head.stdout.strip().removeprefix("origin/")

    for row in rows or []:
        if row.get("is_main") and row.get("branch"):
            return row["branch"]

    current = git_runner(repo_root, "branch", "--show-current", check=False)
    branch = current.stdout.strip()
    return branch or None


def resolve_target_branch(
    repo_root: Path,
    explicit_target: str | None,
    *,
    git_runner=run_git,
) -> str | None:
    """Resolve an explicit or inferred target branch for step operations."""
    if explicit_target:
        return explicit_target

    target = resolve_default_branch(repo_root, git_runner=git_runner)
    if target:
        return target

    print(
        f"{Colors.red('Error')}: could not infer a target branch. Pass one explicitly."
    )
    return None


def normalize_remainder_args(extra_args: list[str] | None) -> list[str]:
    """Normalize argparse remainder args by removing a leading '--' marker."""
    normalized = list(extra_args or [])
    if normalized and normalized[0] == "--":
        normalized = normalized[1:]
    return normalized


def emit_switch_target(args, target_path: Path) -> None:
    """Emit switch target to stdout and optional directive file."""
    if not getattr(args, "no_cd", False):
        directive_file = getattr(args, "directive_file", None)
        if directive_file:
            try:
                Path(directive_file).write_text(str(target_path))
            except OSError as exc:
                print(
                    f"{Colors.yellow('Warning')}: could not write directive file "
                    f"{directive_file}: {exc}"
                )
    print(target_path)
