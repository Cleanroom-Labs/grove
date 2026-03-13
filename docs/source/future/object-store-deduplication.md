# Object Store Deduplication for Sync-Group Submodules

**Status:** Investigated and deferred. Accepting duplication for now.

## Question

When a submodule appears multiple times in a tree (sync group), each instance has its own git object store. Can we deduplicate this — either by using worktrees-as-submodules or by sharing object stores?

## Finding 1: Worktree-as-submodule doesn't work

Git submodule commands expect `.git` files in format `gitdir: ../../.git/modules/<path>`. Git worktree `.git` files use `gitdir: ../../.git/worktrees/<name>`. These formats are **incompatible** — `git submodule sync`, `git submodule update`, and other commands would fail. Grove's repo discovery would still work (it just checks `.git` exists), but the underlying git operations would break.

**Verdict: Not feasible.**

## Finding 2: Git alternates can share object stores

Git has a built-in mechanism (`objects/info/alternates`) where one repo can reference another's object store for reads. A centralized reference repo holds the objects; each sync-group instance's `.git/objects/info/alternates` file points to it.

```
.grove-objects/libs-common/objects/       # one copy of all objects

frontend/libs/common/.git/objects/info/alternates
  → ../../../.grove-objects/libs-common/objects

backend/libs/common/.git/objects/info/alternates
  → ../../../.grove-objects/libs-common/objects
```

This maintains full git submodule compatibility. Space savings of 70-90% for sync-group submodules.

**But alternates have serious safety issues.**

## Finding 3: GC in the source repo can corrupt dependents

This is the critical risk. When `git gc` runs in a **dependent** repo, it only touches local objects (the `-l` flag in `git repack -a -d -l`). This is safe.

But when `git gc` runs in the **alternates source** repo, it can prune unreachable objects. If a dependent repo still references those objects via alternates, the dependent repo **becomes corrupt with missing objects**.

GitLab uses alternates extensively (they call them "object pools") and explicitly documents: *"Do not run git prune or git gc in object pool repositories. This can cause data loss in the regular repositories that depend on the object pool."* They treat source repos as append-only — no objects ever get deleted.

There is **no built-in git safety mechanism** to prevent this. No lock, no reference counting, no gc-safe alternates mode. You must enforce it through operational discipline.

## Finding 4: Concurrent operations are mostly safe

- **Concurrent reads from shared alternates:** Safe. Git's object database read path is process-safe.
- **Concurrent writes to dependent repos:** Safe. New objects from `git fetch` always go to the **local** repo's `.git/objects`, never to the alternates store. Alternates are read-only from the dependent repo's perspective.
- **Concurrent modification of the source repo:** Unsafe. The alternates mechanism can race with concurrent modification, potentially producing inconsistent state.

## Finding 5: Worktree + submodule + alternates is experimental

Git's own documentation notes that "multiple checkout in general is still experimental, and the support for submodules is incomplete." When you create a linked worktree, its submodules get their own copies — they don't inherit alternates from the main worktree. The interaction between all three features is not fully mature.

## Finding 6: Dissociation is possible but defeats the purpose

You can safely remove alternates by running `git repack -a -d` (copies all referenced objects locally) and then deleting the `alternates` file. `git clone --dissociate` does this automatically. But this recreates all the objects locally, eliminating the space savings.

## Finding 7: `git submodule update --reference` exists

Git supports `--reference` for submodule operations, and recent versions added `submodule.alternateLocation = superproject` config. However, the `--reference` flag is not persisted in `.gitmodules` or `.git/config` — it's a one-time option. Setting up alternates for submodules requires manual configuration after init.

## Safety Assessment

| Scenario | Safe? |
|----------|-------|
| Multiple repos reading from shared alternates | Yes |
| Concurrent writes to dependent repos | Yes (objects go locally) |
| GC in dependent repos | Yes (local-only repack) |
| **GC in alternates source repo** | **NO — data loss risk** |
| Worktrees + submodules + alternates combined | Experimental |
| Fetch into dependent repos | Yes |
| Dissociation (removing alternates later) | Yes (but loses savings) |

## Decision

Accept the duplication for now. The disk cost is real but manageable, and Grove already mitigates the worst costs (no network round-trips during worktree init via local references). The alternates approach is too operationally risky (GC data loss) and the git worktree + submodule + alternates story is still experimental.

## Revisiting This Decision

This decision should be revisited if:

- Git adds GC-safe alternates (reference counting or lock-based protection)
- Git's worktree + submodule interaction matures and stabilizes
- Disk usage becomes a measurable bottleneck for real-world Grove users
- A reliable wrapper around alternates emerges (e.g., disabling GC in source repos via `gc.auto=0` combined with manual, coordinated repacking)
