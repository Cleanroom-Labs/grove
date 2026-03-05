"""
grove.config_import
Import WorkTrunk config into Grove's canonical config locations.
"""

from __future__ import annotations

from pathlib import Path

from grove.repo_utils import Colors, find_repo_root
from grove.user_config import (
    dump_toml,
    get_project_config_path,
    get_user_config_path,
    get_wt_project_config_path,
    get_wt_user_config_path,
    load_toml_file,
    merge_dicts,
)


def _translate_wt_to_grove(raw: dict) -> dict:
    """Translate WorkTrunk config into Grove config.

    For now the lifecycle-related schema is compatible, so this is an
    identity mapping. Keeping the function separate makes later field
    normalization cheaper.
    """
    return dict(raw)


def _report_conflicts(
    existing: dict, incoming: dict, *, _prefix: str = ""
) -> list[str]:
    """Return dotted-path conflicts where incoming values differ from existing."""
    conflicts: list[str] = []
    for key, incoming_value in incoming.items():
        path = f"{_prefix}.{key}" if _prefix else key
        if key not in existing:
            continue

        existing_value = existing[key]
        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            conflicts.extend(
                _report_conflicts(existing_value, incoming_value, _prefix=path)
            )
            continue

        if existing_value != incoming_value:
            conflicts.append(
                f"{path}: existing={existing_value!r}, incoming={incoming_value!r}"
            )
    return conflicts


def _import_one(source: Path, target: Path, *, dry_run: bool, force: bool) -> int:
    imported = _translate_wt_to_grove(load_toml_file(source))

    existing: dict = {}
    if target.exists():
        existing = load_toml_file(target)

    if target.exists() and not force:
        conflicts = _report_conflicts(existing, imported)
        if conflicts:
            print(Colors.red(f"Import conflicts for {target}:"))
            for conflict in conflicts:
                print(f"  - {conflict}")
            print("Re-run with --force to overwrite conflicting values.")
            return 1
        merged = merge_dicts(existing, imported)
    else:
        merged = imported

    rendered = dump_toml(merged)

    if dry_run:
        if target.exists() and force:
            action = "replace"
        elif target.exists():
            action = "merge"
        else:
            action = "write"
        print(f"Would {action} {target} from {source}:")
        print()
        print(rendered, end="")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"Imported {source} -> {Colors.blue(str(target))}")
    return 0


def run(args) -> int:
    """Import WorkTrunk config into Grove config files."""
    import_user = args.user or not args.project
    import_project = args.project or not args.user

    repo_root: Path | None = None
    if import_project:
        try:
            repo_root = find_repo_root()
        except FileNotFoundError:
            if args.project:
                print(
                    Colors.red(
                        "Could not find git repository root for project config import."
                    )
                )
                return 1
            import_project = False

    operations: list[tuple[Path, Path]] = []

    if import_user:
        source = get_wt_user_config_path()
        if source.exists():
            operations.append((source, get_user_config_path()))
        elif args.user:
            print(Colors.red(f"WorkTrunk user config not found: {source}"))
            return 1

    if import_project and repo_root is not None:
        source = get_wt_project_config_path(repo_root)
        if source.exists():
            operations.append((source, get_project_config_path(repo_root)))
        elif args.project:
            print(Colors.red(f"WorkTrunk project config not found: {source}"))
            return 1

    if not operations:
        print(Colors.yellow("No WorkTrunk config files found to import."))
        return 1

    for source, target in operations:
        rc = _import_one(source, target, dry_run=args.dry_run, force=args.force)
        if rc != 0:
            return rc

    return 0
