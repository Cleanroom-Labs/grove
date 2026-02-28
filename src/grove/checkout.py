"""
grove/checkout.py
Check out a ref on a submodule and recursively update sub-submodules.

Replaces the manual dance of ``git checkout <ref>`` followed by
``git submodule update --init --recursive`` with a single command.

Usage (via entry point):
    grove checkout <path> <ref>
    grove checkout <path> <ref> --no-recurse
    grove checkout <path> <ref> --no-fetch
"""

from __future__ import annotations

from pathlib import Path

from grove.repo_utils import Colors, RepoInfo, find_repo_root, run_git


def run(args) -> int:
    try:
        repo_root = find_repo_root()
    except FileNotFoundError as e:
        print(Colors.red(str(e)))
        return 1

    # Resolve target path relative to repo root
    target = (repo_root / args.path).resolve()
    if not target.exists():
        print(Colors.red(f"Path does not exist: {args.path}"))
        return 1
    if not (target / ".git").exists():
        print(Colors.red(f"Not a git repository: {args.path}"))
        return 1

    repo = RepoInfo(path=target, repo_root=repo_root)
    ref = args.ref

    # Step 1: Fetch (unless --no-fetch)
    if not args.no_fetch:
        print(f"  {Colors.blue('Fetching')} origin in {repo.rel_path}...")
        result = repo.git("fetch", "origin", check=False)
        if result.returncode != 0:
            print(Colors.yellow(f"  Warning: fetch failed — continuing with local refs"))

    # Step 2: Checkout the requested ref
    print(f"  {Colors.blue('Checking out')} {ref} in {repo.rel_path}...")
    result = repo.git("checkout", ref, check=False, capture=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(Colors.red(f"  Checkout failed: {stderr}"))
        return 1

    sha = repo.get_commit_sha(short=True)
    print(f"  {Colors.green('✓')} {repo.rel_path} now at {sha}")

    # Step 3: Recursively update sub-submodules (unless --no-recurse)
    if not args.no_recurse:
        print(f"  {Colors.blue('Updating')} submodules recursively...")
        result = run_git(
            target,
            "submodule", "update", "--init", "--recursive",
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            print(Colors.red(f"  Submodule update failed: {stderr}"))
            return 1

        # Count how many submodules were updated
        sub_result = run_git(target, "submodule", "status", "--recursive", check=False)
        sub_count = 0
        if sub_result.returncode == 0 and sub_result.stdout.strip():
            sub_count = len(sub_result.stdout.strip().splitlines())

        if sub_count > 0:
            print(f"  {Colors.green('✓')} {sub_count} submodule(s) initialized/updated")
        else:
            print(f"  {Colors.green('✓')} No nested submodules")

    print()
    print(Colors.green(f"Done. {repo.rel_path} checked out at {ref} ({sha})"))
    return 0
