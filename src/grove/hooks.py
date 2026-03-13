"""
grove/hooks.py
Hook execution for worktree lifecycle commands.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from grove.config import HOOK_TYPES, load_config
from grove.repo_utils import Colors, find_repo_root, run_git
from grove.worktree_backend import maybe_delegate_hook

_TEMPLATE_RE = re.compile(r"{{\s*(.*?)\s*}}")
_SANITIZE_RE = re.compile(r"[\\/]+")
_FAIL_FAST_HOOKS = frozenset({"pre-commit", "pre-merge", "pre-remove"})
_SHELL_ONLY_HOOKS = frozenset({"pre-switch", "post-switch"})
_BACKGROUND_HOOKS = frozenset({"post-start", "post-remove"})


def _apply_filter(value: str, filter_name: str) -> str:
    """Apply a template filter to *value*."""
    if filter_name == "sanitize":
        return _SANITIZE_RE.sub("-", value)
    return value


def _render_template(command: str, variables: dict[str, str]) -> str:
    """Expand `{{ var }}` placeholders in *command* using *variables*."""

    def _replace(match: re.Match[str]) -> str:
        expression = match.group(1)
        parts = [part.strip() for part in expression.split("|")]
        key = parts[0]
        value = variables.get(key, "")
        for filter_name in parts[1:]:
            value = _apply_filter(value, filter_name)
        return value

    return _TEMPLATE_RE.sub(_replace, command)


def _iter_hook_commands(repo_root: Path, hook_type: str):
    """Yield (name, command) pairs for a configured hook type."""
    config = load_config(repo_root)
    section = config.hooks.get(hook_type)
    if section is None:
        return []
    return sorted(section.commands.items())


def _resolve_head_commit(repo_root: Path) -> tuple[str, str]:
    """Return (full, short) HEAD commit SHAs, or empty strings when unavailable."""
    result = run_git(repo_root, "rev-parse", "HEAD", check=False)
    if result.returncode != 0:
        return ("", "")

    commit = result.stdout.strip()
    if not commit:
        return ("", "")
    return (commit, commit[:12])


def _baseline_hook_variables(repo_root: Path) -> dict[str, str]:
    """Return baseline template variables available to every hook."""
    commit, short_commit = _resolve_head_commit(repo_root)
    return {
        "repo_path": str(repo_root),
        "repo": repo_root.name,
        "worktree_name": repo_root.name,
        "primary_worktree_path": str(repo_root),
        "commit": commit,
        "short_commit": short_commit,
    }


def has_configured_hooks(repo_root: Path, hook_type: str) -> bool:
    """Return True when *hook_type* has configured commands."""
    return bool(_iter_hook_commands(repo_root, hook_type))


def warn_shell_only_hook_native(hook_type: str) -> None:
    """Warn that shell-integrated hooks are skipped in native mode."""
    if hook_type not in _SHELL_ONLY_HOOKS:
        return
    print(
        f"{Colors.yellow('Warning')}: pre-switch/post-switch hooks require the "
        "worktrunk backend. Install wt for full hook support."
    )


def warn_background_hook_native(hook_type: str) -> None:
    """Warn that background hooks run in the foreground in native mode."""
    if hook_type not in _BACKGROUND_HOOKS:
        return
    print(
        f"{Colors.yellow('Warning')}: {hook_type} hooks run in the foreground "
        "in native mode."
    )


def run_configured_hooks(
    repo_root: Path,
    hook_type: str,
    *,
    name: str | None = None,
    variables: dict[str, str] | None = None,
    yes: bool = False,
) -> int:
    """Run hooks of *hook_type* configured for *repo_root*."""
    if hook_type not in HOOK_TYPES:
        print(f"{Colors.red('Error')}: unknown hook type: {hook_type}")
        return 1

    all_commands = _iter_hook_commands(repo_root, hook_type)
    if not all_commands:
        return 0

    commands = [
        (hook_name, command)
        for hook_name, command in all_commands
        if name is None or hook_name == name
    ]
    if name is not None and not commands:
        print(f"{Colors.red('Error')}: hook '{name}' not found in {hook_type}")
        return 1

    resolved_vars = _baseline_hook_variables(repo_root)
    if variables:
        resolved_vars.update(variables)

    for hook_name, command in commands:
        rendered = _render_template(command, resolved_vars)
        hook_id = f"{hook_type}.{hook_name}"
        if _should_prompt_for_hooks(yes):
            if not _confirm_hook_execution(hook_id, rendered):
                print(f"{Colors.yellow('Skipped hook')}: {hook_id}")
                return 1
        print(f"{Colors.blue('Running hook')} {hook_type}.{hook_name}: {rendered}")
        result = subprocess.run(
            rendered,
            shell=True,  # nosemgrep: subprocess-shell-true
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            print(f"{Colors.red('Hook failed')}: {hook_type}.{hook_name}")
            return 1

    return 0


def _show_hooks(
    repo_root: Path,
    hook_type: str | None,
    *,
    expanded: bool,
    variables: dict[str, str] | None,
) -> int:
    """Show configured hooks."""
    config = load_config(repo_root)
    hook_types = [hook_type] if hook_type else list(HOOK_TYPES)
    resolved_vars = _baseline_hook_variables(repo_root)
    if variables:
        resolved_vars.update(variables)

    printed_any = False
    for current_type in hook_types:
        section = config.hooks.get(current_type)
        if section is None or not section.commands:
            continue
        printed_any = True
        print(f"{Colors.blue(current_type)}:")
        for name, command in sorted(section.commands.items()):
            if expanded:
                command = _render_template(command, resolved_vars)
            print(f"  {name}: {command}")

    if not printed_any:
        print("No hooks configured.")
    return 0


def _parse_vars(raw_vars: list[str] | None) -> dict[str, str]:
    """Parse --var KEY=VALUE pairs."""
    parsed: dict[str, str] = {}
    for raw in raw_vars or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --var value: {raw!r}; expected KEY=VALUE")
        key, value = raw.split("=", 1)
        parsed[key.strip()] = value
    return parsed


def _should_prompt_for_hooks(yes: bool) -> bool:
    """Return True when hooks should ask for interactive approval."""
    return not yes and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def _confirm_hook_execution(hook_id: str, command: str) -> bool:
    """Ask the user to approve a hook command."""
    response = input(f"Run hook {hook_id}? {command} [y/N]: ").strip().lower()
    return response in ("y", "yes")


def run(args) -> int:
    """CLI entry point for `grove worktree hook`."""
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as exc:
        print(Colors.red(str(exc)))
        return 1

    delegated = maybe_delegate_hook(repo_root, args)
    if delegated is not None:
        return delegated

    try:
        variables = _parse_vars(getattr(args, "var", None))
    except ValueError as exc:
        print(Colors.red(str(exc)))
        return 1

    hook_type = args.hook_type
    if hook_type == "show":
        return _show_hooks(
            repo_root,
            getattr(args, "name", None),
            expanded=getattr(args, "expanded", False),
            variables=variables,
        )

    return run_configured_hooks(
        repo_root,
        hook_type,
        name=getattr(args, "name", None),
        variables=variables,
        yes=getattr(args, "yes", False),
    )
