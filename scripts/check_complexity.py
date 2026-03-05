#!/usr/bin/env python3
"""Fail when Python functions exceed an allowed line count."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FUNCTION_LINES = 180
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "grove"


@dataclass(frozen=True)
class FunctionSize:
    file: Path
    name: str
    line_count: int
    start_line: int
    end_line: int


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _function_sizes(path: Path) -> list[FunctionSize]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    sizes: list[FunctionSize] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_line = getattr(node, "end_lineno", None)
        if end_line is None:
            continue
        start_line = node.lineno
        sizes.append(
            FunctionSize(
                file=path,
                name=node.name,
                line_count=(end_line - start_line + 1),
                start_line=start_line,
                end_line=end_line,
            )
        )
    return sizes


def _violations() -> list[FunctionSize]:
    violations: list[FunctionSize] = []
    for path in _iter_python_files(SOURCE_ROOT):
        for size in _function_sizes(path):
            if size.line_count > MAX_FUNCTION_LINES:
                violations.append(size)
    return sorted(violations, key=lambda item: (str(item.file), item.start_line))


def main() -> int:
    if not SOURCE_ROOT.exists():
        print(f"Source root not found: {SOURCE_ROOT}", file=sys.stderr)
        return 2

    violations = _violations()
    if not violations:
        print(
            f"Complexity check passed: no functions over {MAX_FUNCTION_LINES} lines in {SOURCE_ROOT}."
        )
        return 0

    print(
        f"Complexity check failed: {len(violations)} function(s) exceed {MAX_FUNCTION_LINES} lines:"
    )
    for violation in violations:
        rel = violation.file.relative_to(PROJECT_ROOT)
        print(
            f"- {rel}:{violation.start_line}-{violation.end_line} "
            f"{violation.name} ({violation.line_count} lines)"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
