# CLAUDE.md

## Quick Reference

**Install:** `pip install -e ".[dev]"`
**Test:** `pytest`
**Run:** `grove --help`

## Project Structure

- `src/grove/` — Package source (src layout)
- `src/grove/cli.py` — CLI entry point (`grove.cli:main`)
- `src/grove/claude_skills/` — Claude Code skill markdown files (package data)
- `tests/` — Test suite (pytest)
- `docs/` — Internal documentation

## Key Concepts

- Zero external dependencies — stdlib only, requires Python 3.11+
- Operates on any git repo with submodules via `.grove.toml` config
- CLI entry point: `grove = "grove.cli:main"` (console_scripts)
- Skills are bundled as package data and installed via `grove claude install`
