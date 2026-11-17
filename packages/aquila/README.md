# Aquila (`de-aquila`)

A file-backed data catalog for a DE Arsenal repo. `aquila scan` reads the repo's
Pugio pipelines, Gladius transforms (Ballista ones included), Hasta streams, the
tools' state databases and local Parquet footers, then records in one SQLite file
(`.arsenal/aquila.db`):

- **datasets** — one per location, whichever tool names it (`dataset_id` = hash of
  the normalized URI; `out/`, `out/*.parquet` and `./out` are the same dataset)
- **schema versions** — every snapshot Pugio recorded, plus each Parquet footer,
  with a diff between consecutive versions
- **lineage** — dataset-level edges across tools (`pugio:` / `gladius:` /
  `ballista:` / `hasta:`), cycle detection, Mermaid output
- **contracts** — inline `contract:` blocks, hash-tracked, checked against the
  latest observed schema
- **governance** — tags (`pii=true`, `owner=…`), descriptions, deprecation with
  warnings for every reader, full-text search (SQLite FTS5)

```bash
aquila scan                                   # idempotent; --dry-run writes nothing
aquila search "orders pii"
aquila show orders --max-age-seconds 3600     # exits 1 on contract findings or stale data
aquila history orders
aquila lineage orders_daily --direction up --format mermaid
aquila tag orders pii=true owner=data-team
aquila deprecate orders --reason "replaced by orders_v2"
```

## Boundaries

- **Pull model.** Nothing depends on Aquila and no tool writes to it; delete
  `aquila.db` and every pipeline still runs. Scans open state databases
  read-only, so they never migrate, lock or back them up.
- **Declaration-based lineage.** Edges come from specs, not from observed reads.
  A transform that reads a path its spec does not name is invisible.
- **No enforcement.** Tags and deprecation are metadata; access control needs a
  server and is out of scope. Contracts are *checked*, not enforced — Scutum
  enforces them at write time.
- Relative paths resolve against `--root` (default: the current directory),
  like `arsenal run`. Query strings and URL credentials are never stored.
- Augur reads `aquila.db` when present (`augur index --aquila`), read-only.
- `arsenal status` adds a `catalog` block when `de-arsenal[catalog]` is
  installed and `.arsenal/aquila.db` exists.
