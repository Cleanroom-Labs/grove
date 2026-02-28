---
name: grove-checkout
description: Check out a ref on a submodule with recursive submodule init/update
---

# grove-checkout: Submodule Checkout

Check out a branch, tag, or commit SHA on a submodule and recursively initialize/update all nested sub-submodules.

`$ARGUMENTS` should contain `<path> <ref>` and optional flags.

Example usages:
- `/grove-checkout technical-docs/transfer origin/main` -- checkout latest remote main
- `/grove-checkout docs/spec-docs abc1234` -- checkout a specific commit
- `/grove-checkout technical-docs/transfer v1.0.0 --no-fetch` -- checkout a tag without fetching first
- `/grove-checkout technical-docs/transfer origin/main --no-recurse` -- checkout only, skip submodule update

## When to Use

Use `grove checkout` when you need to update a submodule to a different ref and ensure all its nested sub-submodules are also updated. This is common when:

- A submodule's remote has been updated and you want to pull in the latest
- You need to move a submodule to a specific tag or commit
- After a `grove sync`, you need to update a non-sync-group submodule that has new commits

**Key advantage over plain `git checkout`:** A plain `git checkout` does NOT update nested sub-submodules. `grove checkout` handles the full `git fetch` + `git checkout` + `git submodule update --init --recursive` sequence in one command.

## Workflow

### Step 1: Preview

Run `grove checkout $ARGUMENTS` and report the result:
- Which ref was checked out
- The resulting commit SHA
- How many nested submodules were initialized/updated

### Step 2: Verify parent state

After checkout, the parent repo will show a dirty submodule pointer. If the user wants to commit and propagate this change:
- Use `grove cascade <path> --push` to commit the pointer update and push bottom-up
- Or manually commit in the parent and push

## Flags

| Flag | Effect |
|------|--------|
| `--no-recurse` | Only checkout the ref, skip `git submodule update --init --recursive` |
| `--no-fetch` | Skip `git fetch origin` before checkout (use when refs are already local) |

## Error Handling

- **Path does not exist**: verify the submodule path relative to repo root
- **Not a git repository**: the path exists but isn't a git repo (submodule not initialized?)
- **Checkout failed**: the ref doesn't exist — check spelling, or fetch first if using `--no-fetch`
- **Submodule update failed**: network issues or corrupted submodule state — try `git submodule sync --recursive` first
