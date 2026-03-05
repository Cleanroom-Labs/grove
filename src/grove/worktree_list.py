"""
grove/worktree_list.py
Native worktree discovery and `grove worktree list`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from grove.config import load_config
from grove.repo_utils import Colors, find_repo_root, run_git
from grove.worktree_backend import maybe_delegate_list

_FIELD_SEP = "\x1f"
_REF_FIELD_SEP = "\t"


def _relative_age(timestamp: int | None) -> str | None:
    """Return a compact relative age string for a unix timestamp."""
    if timestamp is None:
        return None

    seconds = max(0, int(time.time()) - timestamp)
    if seconds < 60:
        return "just now"

    units = (
        ("y", 60 * 60 * 24 * 365),
        ("mo", 60 * 60 * 24 * 30),
        ("w", 60 * 60 * 24 * 7),
        ("d", 60 * 60 * 24),
        ("h", 60 * 60),
        ("m", 60),
    )
    for suffix, size in units:
        if seconds >= size:
            return f"{seconds // size}{suffix} ago"
    return "just now"


def _parse_porcelain_worktrees(output: str, repo_root: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` output into row dictionaries."""
    rows: list[dict] = []
    current: dict = {}

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            ref = line[7:]
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line == "bare":
            current["is_bare"] = True
        elif line == "detached":
            current["is_detached"] = True
        elif line.startswith("locked "):
            current["locked_reason"] = line[7:]
        elif line == "locked":
            current["locked_reason"] = ""
        elif line.startswith("prunable "):
            current["prunable_reason"] = line[9:]
        elif line == "prunable":
            current["prunable_reason"] = ""

    if current:
        rows.append(current)

    resolved_root = str(repo_root.resolve())
    for index, row in enumerate(rows):
        path_str = row.get("path", "")
        row.setdefault("path", path_str)
        row.setdefault("branch", None)
        row.setdefault("head", "")
        row.setdefault("is_bare", False)
        row.setdefault("is_detached", row["branch"] is None)
        row["kind"] = "worktree"
        row["is_main"] = index == 0
        row["exists"] = bool(path_str) and Path(path_str).exists()
        row["is_current"] = (
            row["exists"] and str(Path(path_str).resolve()) == resolved_root
        )
    return rows


def _augment_worktree_row(row: dict) -> dict:
    """Add status and commit metadata to a worktree row."""
    if row.get("is_bare") or not row.get("exists"):
        row.setdefault("dirty", False)
        row.setdefault("upstream", None)
        row.setdefault("ahead", None)
        row.setdefault("behind", None)
        row.setdefault("subject", None)
        row.setdefault("timestamp", None)
        row.setdefault("age", None)
        row["head_short"] = row["head"][:12] if row.get("head") else None
        return row

    worktree_path = Path(row["path"])

    status_result = run_git(worktree_path, "status", "--porcelain", check=False)
    row["dirty"] = bool(status_result.stdout.strip())

    upstream_result = run_git(
        worktree_path,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    if upstream_result.returncode == 0:
        row["upstream"] = upstream_result.stdout.strip() or None
        counts_result = run_git(
            worktree_path,
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...@{upstream}",
            check=False,
        )
        if counts_result.returncode == 0:
            behind, ahead = counts_result.stdout.strip().split()
            row["ahead"] = int(ahead)
            row["behind"] = int(behind)
        else:
            row["ahead"] = None
            row["behind"] = None
    else:
        row["upstream"] = None
        row["ahead"] = None
        row["behind"] = None

    log_result = run_git(
        worktree_path,
        "log",
        "-1",
        f"--format=%ct{_FIELD_SEP}%s",
        check=False,
    )
    if log_result.returncode == 0 and log_result.stdout.strip():
        raw_timestamp, subject = log_result.stdout.strip().split(_FIELD_SEP, 1)
        timestamp = int(raw_timestamp)
        row["timestamp"] = timestamp
        row["subject"] = subject
        row["age"] = _relative_age(timestamp)
    else:
        row["timestamp"] = None
        row["subject"] = None
        row["age"] = None

    row["head_short"] = row["head"][:12] if row.get("head") else None
    return row


def discover_worktrees(repo_root: Path) -> list[dict]:
    """Discover actual git worktrees for *repo_root* with native metadata."""
    result = run_git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return []

    rows = _parse_porcelain_worktrees(result.stdout, repo_root)
    return [_augment_worktree_row(row) for row in rows]


def _build_local_branch_row(
    branch: str,
    head: str,
    upstream: str,
    raw_timestamp: str,
    subject: str,
    ahead: int | None,
    behind: int | None,
) -> dict:
    """Build a synthetic row for a local branch."""
    timestamp = int(raw_timestamp) if raw_timestamp else None
    return {
        "kind": "branch",
        "path": None,
        "branch": branch,
        "head": head,
        "head_short": head[:12] if head else None,
        "is_bare": False,
        "is_detached": False,
        "is_current": False,
        "is_main": False,
        "exists": False,
        "dirty": False,
        "upstream": upstream or None,
        "ahead": ahead,
        "behind": behind,
        "subject": subject or None,
        "timestamp": timestamp,
        "age": _relative_age(timestamp),
    }


def _build_remote_branch_row(
    branch: str,
    head: str,
    raw_timestamp: str,
    subject: str,
) -> dict:
    """Build a synthetic row for a remote branch."""
    timestamp = int(raw_timestamp) if raw_timestamp else None
    return {
        "kind": "remote",
        "path": None,
        "branch": branch,
        "head": head,
        "head_short": head[:12] if head else None,
        "is_bare": False,
        "is_detached": False,
        "is_current": False,
        "is_main": False,
        "exists": False,
        "dirty": False,
        "upstream": None,
        "ahead": None,
        "behind": None,
        "subject": subject or None,
        "timestamp": timestamp,
        "age": _relative_age(timestamp),
    }


def _discover_local_branch_rows(
    repo_root: Path,
    checked_out_branches: set[str],
) -> list[dict]:
    """Discover local branch rows that are not already checked out."""
    rows: list[dict] = []
    local_result = run_git(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)\t%(objectname)\t%(upstream:short)\t%(committerdate:unix)\t%(subject)",
        "refs/heads",
        check=False,
    )
    if local_result.returncode != 0:
        return rows

    for line in local_result.stdout.splitlines():
        if not line:
            continue
        branch, head, upstream, raw_timestamp, subject = line.split(_REF_FIELD_SEP, 4)
        if branch in checked_out_branches:
            continue

        ahead = None
        behind = None
        if upstream:
            counts_result = run_git(
                repo_root,
                "rev-list",
                "--left-right",
                "--count",
                f"{branch}...{upstream}",
                check=False,
            )
            if counts_result.returncode == 0:
                behind_str, ahead_str = counts_result.stdout.strip().split()
                ahead = int(ahead_str)
                behind = int(behind_str)

        rows.append(
            _build_local_branch_row(
                branch,
                head,
                upstream,
                raw_timestamp,
                subject,
                ahead,
                behind,
            )
        )

    return rows


def _discover_remote_branch_rows(repo_root: Path) -> list[dict]:
    """Discover remote branch rows."""
    rows: list[dict] = []
    remote_result = run_git(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)\t%(objectname)\t%(committerdate:unix)\t%(subject)",
        "refs/remotes",
        check=False,
    )
    if remote_result.returncode != 0:
        return rows

    for line in remote_result.stdout.splitlines():
        if not line:
            continue
        branch, head, raw_timestamp, subject = line.split(_REF_FIELD_SEP, 3)
        if branch.endswith("/HEAD"):
            continue
        rows.append(_build_remote_branch_row(branch, head, raw_timestamp, subject))

    return rows


def _discover_branch_rows(
    repo_root: Path,
    *,
    include_remotes: bool,
    checked_out_branches: set[str],
) -> list[dict]:
    """Discover local and optional remote branch rows not backed by worktrees."""
    rows = _discover_local_branch_rows(repo_root, checked_out_branches)

    if not include_remotes:
        return sorted(rows, key=lambda row: row["branch"] or "")

    rows.extend(_discover_remote_branch_rows(repo_root))

    return sorted(rows, key=lambda row: (row["kind"], row["branch"] or ""))


def collect_worktree_rows(
    repo_root: Path,
    *,
    include_branches: bool = False,
    include_remotes: bool = False,
) -> list[dict]:
    """Collect worktree rows, optionally including local and remote branches."""
    worktree_rows = discover_worktrees(repo_root)
    if not include_branches and not include_remotes:
        return worktree_rows

    checked_out_branches = {
        row["branch"]
        for row in worktree_rows
        if row["kind"] == "worktree" and row.get("branch")
    }
    branch_rows = _discover_branch_rows(
        repo_root,
        include_remotes=include_remotes,
        checked_out_branches=checked_out_branches,
    )
    return worktree_rows + branch_rows


def _format_state(row: dict) -> str:
    """Return the display state for a worktree list row."""
    if row["kind"] == "remote":
        return "remote"
    if row["kind"] == "branch":
        return "branch"
    if row.get("is_bare"):
        return "bare"
    if not row.get("exists"):
        return "missing"
    if row.get("is_detached"):
        return "detached"
    return "dirty" if row.get("dirty") else "clean"


def _format_ahead_behind(row: dict) -> str:
    """Render ahead/behind counts for table output."""
    ahead = row.get("ahead")
    behind = row.get("behind")
    if ahead is None and behind is None:
        return "-"
    return f"+{ahead or 0}/-{behind or 0}"


def _display_branch(row: dict) -> str:
    """Return the row's branch label."""
    if row.get("branch"):
        return row["branch"]
    if row["kind"] == "worktree" and row.get("head_short"):
        return f"(detached {row['head_short']})"
    return "(unknown)"


def _display_path(row: dict) -> str:
    """Return the display path for a row."""
    return row["path"] or "-"


def _render_table(rows: list[dict], *, full: bool) -> str:
    """Render worktree rows as a simple aligned table."""
    if not rows:
        return "No worktrees found."

    headers = ["", "Branch", "Path", "State", "Ahead/Behind", "Age", "Commit"]
    if full:
        headers.extend(["HEAD", "Upstream"])

    table_rows: list[list[str]] = []
    for row in rows:
        rendered = [
            "*" if row.get("is_current") else " ",
            _display_branch(row),
            _display_path(row),
            _format_state(row),
            _format_ahead_behind(row),
            row.get("age") or "-",
            row.get("subject") or "-",
        ]
        if full:
            rendered.extend(
                [
                    row.get("head_short") or "-",
                    row.get("upstream") or "-",
                ]
            )
        table_rows.append(rendered)

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in table_rows))
        for index in range(len(headers))
    ]

    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * widths[index] for index in range(len(headers))),
    ]
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in table_rows
    )
    return "\n".join(lines)


def list_worktrees(args) -> int:
    """Entry point for `grove worktree list`."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    delegated = maybe_delegate_list(repo_root, args)
    if delegated is not None:
        return delegated

    config = load_config(repo_root)
    output_format = args.format or "table"
    include_branches = (
        args.branches if args.branches is not None else config.list.branches
    )
    include_remotes = args.remotes if args.remotes is not None else config.list.remotes
    full = args.full if args.full is not None else config.list.full

    rows = collect_worktree_rows(
        repo_root,
        include_branches=include_branches,
        include_remotes=include_remotes,
    )

    if output_format == "json":
        print(json.dumps({"worktrees": rows}, indent=2))
    else:
        print(_render_table(rows, full=full))

    return 0


def run(args) -> int:
    """Module entry point."""
    return list_worktrees(args)
