# AGENTS.md

## Overview

- `grove` is a Python 3.11+ CLI for managing nested git submodules.
- The package is stdlib-only on the Python side. Keep new runtime dependencies out unless there is a strong reason.
- Main package code lives in `src/grove/`.
- Tests live in `tests/`.
- The repo also contains a checked-out `common/` git submodule used for shared docs/theme assets.

## Setup And Verification

- Install for development: `pip install -e ".[dev]"`
- Run the full test suite: `pytest`
- Run lint: `ruff check src tests`
- Entry point: `grove --help`

## Project Structure

- `src/grove/cli.py`: argparse entry point and command dispatch.
- `src/grove/repo_utils.py`: shared git/repository helpers used across commands.
- `src/grove/worktree.py`: worktree add/remove and branch checkout helpers.
- `src/grove/worktree_merge.py`: bottom-up merge workflow with persisted state.
- `src/grove/sync.py`: sync-group discovery, target resolution, propagation, and push.
- `src/grove/cascade.py`: cascade workflow for propagating child changes upward with tiered tests.
- `src/grove/visualizer/`: browser-based visualizer server and frontend assets.
- `src/grove/claude_skills/`: packaged skill markdown files installed by `grove claude install`.
- `docs/`: design notes and workflow documentation.

## Workflow Invariants

- Treat the main checkout as the merge hub. Development is expected to happen in worktrees.
- Run merge and push flows from the main checkout, not from a feature worktree.
- Sync-group submodules are expected to be detached; non-sync-group submodules should usually be on named branches.
- The actual `worktree add` CLI syntax is `grove worktree add <path> <branch>`.
- For repo-wide commands, prefer running from the top-level repository root. Running from inside `common/` or another submodule will scope git discovery to that submodule.

## Coding Guidance

- Match the existing style: straightforward stdlib code, small helpers, minimal abstraction.
- Keep command behavior explicit and CLI-facing output readable.
- Add or update pytest coverage with code changes, especially for git edge cases and failure paths.
- When touching parser behavior in `src/grove/cli.py`, check completion behavior in `src/grove/completion.py` and parser-focused tests in `tests/test_cli.py` and `tests/test_completion.py`.
- When changing repo discovery, sync, push, or merge logic, review parent/child relationships and topological ordering carefully. Many commands depend on `RepoInfo.parent`.

## High-Risk Areas

- `worktree remove` and merge/sync resume paths can affect user work; preserve safety checks.
- Repo-root detection is sensitive because this repo contains a real submodule (`common/`).
- Sync-group behavior depends on nested `.gitmodules` traversal and on correct propagation of parent repo commits.
- The visualizer ships static web assets as package data; preserve packaging entries in `pyproject.toml`.

## Before Finishing

- Run targeted tests for changed modules at minimum.
- Prefer the full `pytest` suite before closing out meaningful changes.
- Call out any behavior/docs mismatches you notice, especially around command syntax or working-directory assumptions.
