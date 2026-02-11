# Push Filtering Design

`grove push` supports optional filters to target specific repos instead of pushing everything.

## Motivation

After a focused workflow (cascading a submodule, syncing a group), you often want to push only the affected repos. Without filtering, `grove push` pushes everything — including repos unrelated to the current change.

## Filter Modes

### Positional paths

Push specific repos by their relative path:

```bash
grove push libs/common services/api
```

Exact match on `repo.rel_path`. Unknown paths produce an error listing available repos.

### `--sync-group`

Push parent repos of a sync group:

```bash
grove push --sync-group common
```

Discovers all instances of the sync group, then finds their parent repos. The submodule instances themselves are included in the filter set.

### `--cascade`

Push repos in a cascade chain:

```bash
grove push --cascade libs/common
```

Builds the cascade chain from the given path (leaf through intermediates to root) and pushes all repos in that chain. When the path is a sync-group submodule, includes all instances and their full chains (DAG).

## Union Semantics

Filters compose with union semantics — a repo matching **any** filter is included:

```bash
grove push --cascade libs/common --sync-group database
```

This pushes all repos in the `libs/common` cascade chain **plus** all parent repos of the `database` sync group.

## Sync-Group Exclusion

When **no filters** are active, `grove push` excludes sync-group submodules from the push set (matching the original behavior — sync-group submodules are managed by `grove sync`, not `grove push`).

When **any filter** is active, this exclusion is disabled. Filters discover all repos including sync-group submodules, and the filter set determines what gets pushed. This is necessary because `--cascade` and `--sync-group` explicitly target these repos.

## Dry Run

All filters work with `--dry-run`:

```bash
grove push --cascade libs/common --dry-run
```

Reports how many repos are discovered, how many are targeted by filters, and which have pending changes.

## Comparison with `grove ship`

| | `grove push` | `grove ship` |
|---|---|---|
| Health check | No | Yes (`grove check` first) |
| Filtering | Yes (paths, sync-group, cascade) | No (always pushes all) |
| Use case | Targeted push after specific workflow | Ship everything after full verification |
