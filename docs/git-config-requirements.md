# Git Config Requirements

Grove depends on a small number of git configuration invariants to behave
correctly. Most are defaults — you only need to act if you've explicitly set
something that conflicts.

## `submodule.recurse` must be `false` (the default)

**Requirement:** `submodule.recurse` must be unset or explicitly `false` in
your effective git configuration.

### Why

Grove treats submodule state as explicit. Commands like `grove sync`,
`grove worktree merge`, and `grove cascade` are the intended mechanism for
advancing submodule pointers — and they do so deliberately, in topological
order, with validation at each step.

When `submodule.recurse = true`, a plain `git checkout` or `git pull` silently
does what grove is supposed to do deliberately. This creates two problems:

1. **Correctness:** Git's recursive checkout doesn't understand grove's
   invariants. It may advance a sync-group submodule to the wrong commit, or
   detach HEAD on a non-sync-group submodule that should be on a named branch.

2. **Legibility:** Using grove's commands makes the intended operation
   explicit and auditable. `submodule.recurse = true` reintroduces exactly the
   implicit, hard-to-reason-about submodule behavior that grove was designed to
   replace.

### `push.recurseSubmodules = on-demand` is fine

This setting is different and compatible with grove. It only fires during
`git push` and ensures submodule commits that are referenced by a parent are
pushed before the parent — the same ordering grove's own push path applies.
Having it in git config is harmless.

---

## FAQ — Diagnosing `submodule.recurse` Problems

### Symptoms

| Symptom | Likely cause |
|---------|-------------|
| Sync-group submodules move after `git checkout` on the parent | `submodule.recurse = true` |
| `grove check` shows sync-group divergence after a routine `git pull` | `submodule.recurse = true` |
| A submodule is on the wrong commit after switching a worktree branch | `submodule.recurse = true` advanced it to the branch's pointer |
| `grove sync` reports "already up to date" but submodule state looks wrong | Submodule may have been silently advanced past the intended commit |
| Non-sync-group submodule is in detached HEAD after a `git pull` | `submodule.recurse = true` checked out a commit rather than a branch |

### Diagnosis

Check whether the setting is active in your effective config:

```bash
git config --get submodule.recurse
```

- **Prints `true`** — the setting is active. See remediation below.
- **Prints nothing** — the setting is unset (default `false`). This is correct.
- **Prints `false`** — explicitly set to false. This is correct.

To find which config file is responsible:

```bash
git config --show-origin submodule.recurse
```

This prints the file path and value, e.g.:

```
file:~/.config/git/config    true
```

### Remediation

**Option A — Remove the setting (recommended):**

Open the config file identified above and remove or comment out the
`[submodule]` block:

```ini
# Remove or comment this out:
# [submodule]
#   recurse = true
```

**Option B — Override at the repo level:**

If you can't change the global config (e.g., a shared machine), override it
locally in the grove repo:

```bash
git config submodule.recurse false
```

This writes to `.git/config` and takes precedence over the global setting.

**After fixing:**

Run `grove check` to verify submodule state is intact. If submodules were
silently advanced to unexpected commits, use `grove sync` to restore them to
the correct sync-group target, or `git submodule update` for individual
submodules.
