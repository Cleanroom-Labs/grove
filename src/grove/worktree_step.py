"""
grove/worktree_step.py
Native and delegated `grove worktree step` command handlers.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from grove.config import load_config
from grove.hooks import run_configured_hooks
from grove.repo_utils import Colors, find_repo_root, run_git
from grove.worktree_backend import maybe_delegate_step
from grove.worktree_common import (
    normalize_remainder_args as _normalize_remainder_args_common,
    resolve_default_branch as _resolve_default_branch_common,
    resolve_target_branch as _resolve_target_branch_common,
)
from grove.worktree_list import discover_worktrees

_MIN_AGE_RE = re.compile(r"^(\d+)([smhdw])$")
_MIN_AGE_MULTIPLIERS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


def _resolve_default_branch(repo_root: Path) -> str | None:
    """Resolve repository default branch (origin/HEAD, then current branch)."""
    return _resolve_default_branch_common(repo_root, git_runner=run_git)


def _resolve_target(repo_root: Path, explicit_target: str | None) -> str | None:
    """Resolve a step target branch/ref."""
    return _resolve_target_branch_common(
        repo_root,
        explicit_target,
        git_runner=run_git,
    )


def _normalize_extra_args(extra_args: list[str] | None) -> list[str]:
    """Normalize argparse remainder args by removing a leading '--' marker."""
    return _normalize_remainder_args_common(extra_args)


def _current_branch(repo_root: Path) -> str:
    """Return the current branch name, or HEAD when detached."""
    current = run_git(repo_root, "branch", "--show-current", check=False)
    branch = current.stdout.strip()
    return branch or "HEAD"


def _resolve_stage_mode(repo_root: Path, explicit: str | None) -> str:
    """Resolve staging mode from CLI or config defaults."""
    if explicit:
        return explicit
    return load_config(repo_root).commit.stage


def _stage_changes(repo_root: Path, stage_mode: str) -> int:
    """Apply pre-commit staging according to stage_mode."""
    if stage_mode == "none":
        return 0

    add_args = ("add", "-A") if stage_mode == "all" else ("add", "-u")
    result = run_git(repo_root, *add_args, check=False, capture=False)
    if result.returncode != 0:
        print(f"{Colors.red('Error')}: failed to stage changes ({stage_mode})")
        return 1
    return 0


def _has_staged_changes(repo_root: Path) -> bool:
    """Return True when there are staged changes."""
    result = run_git(repo_root, "diff", "--cached", "--quiet", check=False)
    return result.returncode != 0


def _build_commit_prompt(repo_root: Path) -> str:
    """Build a commit prompt compatible with the future LLM flow."""
    diffstat_result = run_git(repo_root, "diff", "--cached", "--stat", check=False)
    diff_result = run_git(repo_root, "diff", "--cached", check=False)
    recent_result = run_git(
        repo_root,
        "log",
        "-5",
        "--pretty=format:%h %s",
        check=False,
    )

    diffstat = diffstat_result.stdout.strip() if diffstat_result.returncode == 0 else ""
    diff = diff_result.stdout.strip() if diff_result.returncode == 0 else ""
    recent = recent_result.stdout.strip() if recent_result.returncode == 0 else ""

    return (
        "Write a commit message for the staged changes below.\n\n"
        "<format>\n"
        "- Subject line under 50 chars\n"
        "- For material changes, add a blank line then a body paragraph "
        "explaining the change\n"
        "- Output only the commit message, no quotes or code blocks\n"
        "</format>\n\n"
        "<style>\n"
        '- Imperative mood: "Add feature" not "Added feature"\n'
        "- Match recent commit style (conventional commits if used)\n"
        "- Describe the change, not the intent or benefit\n"
        "</style>\n\n"
        f"<diffstat>{diffstat}</diffstat>\n"
        f"<diff>{diff}</diff>\n"
        "<context>\n"
        f"Branch: {_current_branch(repo_root)}\n"
        f"<recent_commits>{recent}</recent_commits>\n"
        "</context>\n"
    )


def _build_squash_prompt(repo_root: Path, target: str, base: str) -> str:
    """Build a squash prompt compatible with the future LLM flow."""
    commits_result = run_git(
        repo_root,
        "log",
        "--reverse",
        "--format=%h %s",
        f"{base}..HEAD",
        check=False,
    )
    diffstat_result = run_git(repo_root, "diff", "--stat", base, "HEAD", check=False)
    diff_result = run_git(repo_root, "diff", base, "HEAD", check=False)

    commits = commits_result.stdout.strip() if commits_result.returncode == 0 else ""
    diffstat = diffstat_result.stdout.strip() if diffstat_result.returncode == 0 else ""
    diff = diff_result.stdout.strip() if diff_result.returncode == 0 else ""

    return (
        "Combine these commits into a single commit message.\n\n"
        "<format>\n"
        "- Subject line under 50 chars\n"
        "- For material changes, add a blank line then a body paragraph "
        "explaining the change\n"
        "- Output only the commit message, no quotes or code blocks\n"
        "</format>\n\n"
        "<style>\n"
        '- Imperative mood: "Add feature" not "Added feature"\n'
        "- Match the style of commits being squashed (conventional commits if used)\n"
        "- Describe the change, not the intent or benefit\n"
        "</style>\n\n"
        f'<commits branch="{_current_branch(repo_root)}" target="{target}">{commits}</commits>\n'
        f"<diffstat>{diffstat}</diffstat>\n"
        f"<diff>{diff}</diff>\n"
    )


def _run_commit(repo_root: Path, args) -> int:
    """Run native `worktree step commit`."""
    if getattr(args, "show_prompt", False):
        print(_build_commit_prompt(repo_root))
        return 0

    stage_mode = _resolve_stage_mode(repo_root, getattr(args, "stage", None))
    if _stage_changes(repo_root, stage_mode) != 0:
        return 1

    if not getattr(args, "no_verify", False):
        hook_vars = {
            "branch": _current_branch(repo_root),
            "worktree_path": str(repo_root),
            "default_branch": _resolve_default_branch(repo_root) or "",
        }
        hook_result = run_configured_hooks(
            repo_root,
            "pre-commit",
            variables=hook_vars,
            yes=getattr(args, "yes", False),
        )
        if hook_result != 0:
            return 1

    if not _has_staged_changes(repo_root):
        print(f"{Colors.yellow('Nothing to commit')}: no staged changes")
        return 0

    prompt = _build_commit_prompt(repo_root)
    result = _commit_with_generated_message(repo_root, prompt)
    if result.returncode != 0:
        print(f"{Colors.red('Error')}: git commit failed")
        return 1
    return 0


def _resolve_squash_base(repo_root: Path, target: str) -> str | None:
    """Resolve merge-base between target and HEAD for squash operations."""
    base_result = run_git(repo_root, "merge-base", target, "HEAD", check=False)
    if base_result.returncode != 0 or not base_result.stdout.strip():
        print(
            f"{Colors.red('Error')}: could not determine merge-base for "
            f"{Colors.green(target)} and HEAD"
        )
        return None
    return base_result.stdout.strip()


def _commit_count_since(repo_root: Path, base: str) -> int | None:
    """Return commit count on HEAD since *base*."""
    count_result = run_git(
        repo_root, "rev-list", "--count", f"{base}..HEAD", check=False
    )
    if count_result.returncode != 0:
        return None
    try:
        return int(count_result.stdout.strip())
    except ValueError:
        return None


def _run_squash(repo_root: Path, args) -> int:
    """Run native `worktree step squash`."""
    target = _resolve_target(repo_root, getattr(args, "target", None))
    if target is None:
        return 1

    base = _resolve_squash_base(repo_root, target)
    if base is None:
        return 1

    if getattr(args, "show_prompt", False):
        print(_build_squash_prompt(repo_root, target, base))
        return 0

    count = _commit_count_since(repo_root, base)
    if count is None:
        print(f"{Colors.red('Error')}: failed to inspect commits to squash")
        return 1
    if count == 0:
        print(
            f"{Colors.yellow('Nothing to squash')}: branch has no commits beyond base"
        )
        return 0

    reset_result = run_git(
        repo_root, "reset", "--soft", base, check=False, capture=False
    )
    if reset_result.returncode != 0:
        print(f"{Colors.red('Error')}: failed to reset branch to squash base")
        return 1

    stage_mode = _resolve_stage_mode(repo_root, getattr(args, "stage", None))
    if _stage_changes(repo_root, stage_mode) != 0:
        return 1

    if not getattr(args, "no_verify", False):
        hook_vars = {
            "branch": _current_branch(repo_root),
            "worktree_path": str(repo_root),
            "default_branch": _resolve_default_branch(repo_root) or "",
            "target": target,
            "base": base,
        }
        hook_result = run_configured_hooks(
            repo_root,
            "pre-commit",
            variables=hook_vars,
            yes=getattr(args, "yes", False),
        )
        if hook_result != 0:
            return 1

    if not _has_staged_changes(repo_root):
        print(f"{Colors.yellow('Nothing to commit')}: no staged changes after squash")
        return 0

    prompt = _build_squash_prompt(repo_root, target, base)
    commit_result = _commit_with_generated_message(repo_root, prompt)
    if commit_result.returncode != 0:
        print(f"{Colors.red('Error')}: git commit failed")
        return 1
    return 0


def _generate_message(repo_root: Path, prompt: str) -> str | None:
    """Resolve optional generated commit/squash message."""
    from grove.llm import generate_message

    return generate_message(repo_root, prompt)


def _commit_with_message(repo_root: Path, message: str):
    """Run git commit using an explicit message body."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="grove-commit-msg-",
        suffix=".txt",
        delete=False,
    ) as message_file:
        message_file.write(message.rstrip() + "\n")
        message_path = Path(message_file.name)

    try:
        return run_git(
            repo_root,
            "commit",
            "-F",
            str(message_path),
            check=False,
            capture=False,
        )
    finally:
        message_path.unlink(missing_ok=True)


def _commit_with_generated_message(repo_root: Path, prompt: str):
    """Commit using generated message when available, else fallback to editor."""
    generated = _generate_message(repo_root, prompt)
    if generated:
        return _commit_with_message(repo_root, generated)
    return run_git(repo_root, "commit", check=False, capture=False)


def _parse_min_age(raw: str | None) -> int | None:
    """Parse --min-age values like 1h, 2d, 30m."""
    if not raw:
        return None
    match = _MIN_AGE_RE.fullmatch(raw.strip())
    if not match:
        raise ValueError(
            "Invalid --min-age value. Expected formats like 30m, 1h, 2d, or 1w."
        )
    value = int(match.group(1))
    unit = match.group(2)
    return value * _MIN_AGE_MULTIPLIERS[unit]


def _branch_is_merged(repo_root: Path, branch: str, default_branch: str) -> bool:
    """Return True when branch is merged into default_branch."""
    result = run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        f"refs/heads/{branch}",
        f"refs/heads/{default_branch}",
        check=False,
    )
    return result.returncode == 0


def _collect_prune_targets(
    repo_root: Path,
    *,
    default_branch: str,
    min_age_seconds: int | None,
) -> list[dict]:
    """Collect merged secondary worktrees eligible for pruning."""
    now = int(time.time())
    candidates: list[dict] = []
    for row in discover_worktrees(repo_root):
        if row.get("is_main") or row.get("is_current"):
            continue

        branch = row.get("branch")
        path = row.get("path")
        if not branch or not path:
            continue
        if branch == default_branch:
            continue

        timestamp = row.get("timestamp")
        if min_age_seconds is not None:
            if timestamp is None:
                continue
            age_seconds = max(0, now - int(timestamp))
            if age_seconds < min_age_seconds:
                continue

        if not _branch_is_merged(repo_root, branch, default_branch):
            continue

        candidates.append(
            {
                "branch": branch,
                "path": path,
                "age": row.get("age"),
            }
        )

    return candidates


def _run_prune(repo_root: Path, args) -> int:
    """Run native `worktree step prune`."""
    default_branch = _resolve_default_branch(repo_root)
    if not default_branch:
        print(f"{Colors.red('Error')}: could not infer the default branch for prune")
        return 1

    try:
        min_age_seconds = _parse_min_age(getattr(args, "min_age", None))
    except ValueError as exc:
        print(f"{Colors.red('Error')}: {exc}")
        return 1

    candidates = _collect_prune_targets(
        repo_root,
        default_branch=default_branch,
        min_age_seconds=min_age_seconds,
    )
    if not candidates:
        print("No prune candidates found.")
        return 0

    if getattr(args, "dry_run", False):
        print(f"Would prune {len(candidates)} worktree(s):")
        for candidate in candidates:
            age = candidate["age"] or "unknown age"
            print(f"  - {candidate['branch']} ({candidate['path']}, {age})")
        return 0

    from grove.worktree import remove_worktree

    remove_args = SimpleNamespace(
        targets=[candidate["branch"] for candidate in candidates],
        path=None,
        force=False,
        no_delete_branch=False,
        force_delete=False,
        foreground=getattr(args, "foreground", False),
        no_verify=False,
        yes=getattr(args, "yes", False),
    )
    return remove_worktree(remove_args)


def _current_branch_strict(repo_root: Path) -> str | None:
    """Return current branch name, or None if detached."""
    current = run_git(repo_root, "branch", "--show-current", check=False)
    branch = current.stdout.strip()
    return branch or None


def _worktree_by_branch(repo_root: Path) -> dict[str, Path]:
    """Map branch names to worktree paths for currently checked-out branches."""
    mapping: dict[str, Path] = {}
    for row in discover_worktrees(repo_root):
        branch = row.get("branch")
        path = row.get("path")
        if not branch or not path:
            continue
        mapping[branch] = Path(path).resolve()
    return mapping


def _list_ignored_files(worktree_path: Path) -> list[Path]:
    """Return ignored, untracked file paths relative to worktree root."""
    result = run_git(
        worktree_path,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        check=False,
    )
    if result.returncode != 0:
        return []
    files: list[Path] = []
    for line in result.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        files.append(Path(rel))
    return files


def _load_worktreeinclude_patterns(worktree_path: Path) -> list[str] | None:
    """Load .worktreeinclude patterns, returning None when file is absent."""
    include_path = worktree_path / ".worktreeinclude"
    if not include_path.exists():
        return None

    patterns: list[str] = []
    for line in include_path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("./"):
            candidate = candidate[2:]
        patterns.append(candidate)
    return patterns


def _path_matches_worktreeinclude(rel_path: Path, pattern: str) -> bool:
    """Best-effort gitignore-style matching for .worktreeinclude patterns."""
    rel = rel_path.as_posix()
    normalized = pattern.lstrip("/")

    if normalized.endswith("/"):
        prefix = normalized.rstrip("/")
        return rel == prefix or rel.startswith(f"{prefix}/")

    if fnmatch.fnmatch(rel, normalized):
        return True

    if not any(ch in normalized for ch in "*?[]"):
        return rel == normalized or rel.startswith(f"{normalized}/")
    return False


def _filter_ignored_files_by_worktreeinclude(
    worktree_path: Path,
    ignored_files: list[Path],
) -> list[Path]:
    """Apply .worktreeinclude filtering when configured.

    WorkTrunk semantics: when .worktreeinclude is present, files must be both
    gitignored and matched by include patterns.
    """
    patterns = _load_worktreeinclude_patterns(worktree_path)
    if patterns is None:
        return ignored_files
    if not patterns:
        return []

    filtered: list[Path] = []
    for rel_path in ignored_files:
        if any(
            _path_matches_worktreeinclude(rel_path, pattern) for pattern in patterns
        ):
            filtered.append(rel_path)
    return filtered


def _run_copy_ignored(repo_root: Path, args) -> int:
    """Run native `worktree step copy-ignored`."""
    current_branch = _current_branch_strict(repo_root)
    from_branch = getattr(args, "from_branch", None) or current_branch
    to_branch = getattr(args, "to_branch", None) or _resolve_default_branch(repo_root)

    if not from_branch:
        print(
            f"{Colors.red('Error')}: could not infer source branch. "
            "Pass --from explicitly."
        )
        return 1
    if not to_branch:
        print(
            f"{Colors.red('Error')}: could not infer destination branch. "
            "Pass --to explicitly."
        )
        return 1

    worktrees = _worktree_by_branch(repo_root)
    source_path = worktrees.get(from_branch)
    dest_path = worktrees.get(to_branch)
    if source_path is None:
        print(
            f"{Colors.red('Error')}: no worktree is checked out for source branch "
            f"{Colors.green(from_branch)}"
        )
        return 1
    if dest_path is None:
        print(
            f"{Colors.red('Error')}: no worktree is checked out for destination branch "
            f"{Colors.green(to_branch)}"
        )
        return 1
    if source_path == dest_path:
        print(f"{Colors.red('Error')}: source and destination worktrees are the same")
        return 1

    ignored_files = _list_ignored_files(source_path)
    ignored_files = _filter_ignored_files_by_worktreeinclude(source_path, ignored_files)
    if not ignored_files:
        print("No ignored files to copy.")
        return 0

    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)
    copied = 0
    skipped = 0

    for rel_path in ignored_files:
        src = source_path / rel_path
        dst = dest_path / rel_path

        if not src.exists() or src.is_dir():
            continue
        if dst.exists() and not force:
            skipped += 1
            print(f"Skip existing: {rel_path}")
            continue

        if dry_run:
            copied += 1
            print(f"Would copy: {rel_path}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f"Copied: {rel_path}")

    if dry_run:
        print(f"Dry-run complete. {copied} file(s) would be copied, {skipped} skipped.")
    else:
        print(f"Copy complete. {copied} file(s) copied, {skipped} skipped.")
    return 0


def _run_push(repo_root: Path, target: str) -> int:
    result = run_git(
        repo_root,
        "push",
        ".",
        f"HEAD:{target}",
        check=False,
        capture=False,
    )
    if result.returncode != 0:
        print(f"{Colors.red('Error')}: failed to push HEAD to {Colors.green(target)}")
        return 1
    return 0


def _run_rebase(repo_root: Path, target: str) -> int:
    result = run_git(repo_root, "rebase", target, check=False, capture=False)
    if result.returncode != 0:
        print(f"{Colors.red('Error')}: failed to rebase onto {Colors.green(target)}")
        return 1
    return 0


def _run_diff(repo_root: Path, target: str, extra_args: list[str]) -> int:
    git_args = ["diff", f"{target}...HEAD"]
    if extra_args:
        git_args.append("--")
        git_args.extend(extra_args)

    result = run_git(repo_root, *git_args, check=False, capture=False)
    if result.returncode != 0:
        print(
            f"{Colors.red('Error')}: failed to diff against target "
            f"{Colors.green(target)}"
        )
        return 1
    return 0


def _unsupported_without_wt(command_name: str) -> int:
    """Print a consistent native unsupported error."""
    print(
        f"{Colors.red('Error')}: grove worktree step {command_name} requires "
        "the worktrunk backend (wt)."
    )
    print('Set [worktree].backend = "wt" to delegate this command.')
    return 1


def run(args) -> int:
    """Entry point for `grove worktree step`."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as exc:
        print(Colors.red(str(exc)))
        return 1

    delegated = maybe_delegate_step(repo_root, args)
    if delegated is not None:
        return delegated

    step_command = getattr(args, "step_command", None)
    if not step_command:
        print(
            f"{Colors.red('Error')}: missing step subcommand. "
            "Run `grove worktree step --help`."
        )
        return 2

    if step_command == "push":
        target = _resolve_target(repo_root, getattr(args, "target", None))
        if target is None:
            return 1
        return _run_push(repo_root, target)

    if step_command == "rebase":
        target = _resolve_target(repo_root, getattr(args, "target", None))
        if target is None:
            return 1
        return _run_rebase(repo_root, target)

    if step_command == "diff":
        target = _resolve_target(repo_root, getattr(args, "target", None))
        if target is None:
            return 1
        return _run_diff(
            repo_root,
            target,
            _normalize_extra_args(getattr(args, "extra_args", None)),
        )

    if step_command == "commit":
        return _run_commit(repo_root, args)

    if step_command == "squash":
        return _run_squash(repo_root, args)

    if step_command == "prune":
        return _run_prune(repo_root, args)

    if step_command == "copy-ignored":
        return _run_copy_ignored(repo_root, args)

    if step_command in (
        "for-each",
        "promote",
        "relocate",
    ):
        return _unsupported_without_wt(step_command)

    print(f"{Colors.red('Error')}: unknown step subcommand: {step_command}")
    return 2
