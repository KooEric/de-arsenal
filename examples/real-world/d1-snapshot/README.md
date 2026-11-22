# Cloudflare D1 table → reviewed publication

`arsenal dataset` publishes files. This example gets a D1 table into that flow:
`d1_snapshot.py` writes the whole table as one snapshot file, and
`arsenal dataset` compares it with the last publication by key before replacing it.

```bash
export CLOUDFLARE_ACCOUNT_ID=...      # the account that owns the database
export CLOUDFLARE_API_TOKEN=...       # a token with D1 read permission
mkdir cards && cd cards               # one working directory per dataset

python ../d1_snapshot.py <database-id> card_catalog code input/card_catalog.jsonl
arsenal dataset preview --files 'input/*.jsonl' --key code --time-column updated_at
arsenal dataset apply --expect <version from the preview>
```

Every later run is the same three commands without the options: the rules from
the first publication are reused.

```bash
python ../d1_snapshot.py <database-id> card_catalog code input/card_catalog.jsonl
arsenal dataset preview               # added / removed / updated since last publication
arsenal dataset apply --expect <version>
aquila scan && aquila lineage published --format mermaid      # needs de-arsenal[catalog]
```

The script only sends `SELECT`. It pages on the key column, orders rows by key
and writes fields in a fixed order, so an unchanged table gives a byte-identical
file.

## How changes at the source show up

Each run is a full snapshot, so nothing has to be tracked at the source. The
preview classifies every difference by key:

| At the source | In the preview |
|---|---|
| Row inserted | `added` |
| Any column changed, including a soft delete (`active` 1 → 0) | `updated`, with before and after |
| Row deleted (`DELETE`) | `removed` |
| Nothing changed | file `unchanged`, all counts 0 |

A row removed at the source is removed from the publication on `apply`. If
consumers must keep seeing deleted rows, delete softly at the source.

## What was run (2026-11-22, de-arsenal 0.6.7 installed from PyPI)

Against a production D1 catalog table: 3,365 rows, 20 columns, text key. Two
real states of the table were used, an older copy (2,572 rows) and the current
one. Nothing was written to D1.

| Step | Result |
|---|---|
| Publish the older state | 2,572 rows published |
| Preview the current state | 793 added, 0 removed, 2,559 updated |
| Of the 2,559 updates | 384 soft-deleted (`active` 1 → 0), 43 renamed, 86 changed only in `updated_at` |
| Apply with `--expect`, then `aquila scan` and `lineage` | 3,365 rows; graph shows `input → published` with the row count and data period |
| Export again with no change at the source | byte-identical file, preview reports `unchanged` and 0 changes |
| Remove 2 rows and edit 2 rows in the local file | `removed` 2 and `updated` 2, naming the keys and the changed fields |
| Apply with the token of an outdated preview | refused, exit 1; the publication kept its 3,365 rows |
| Same old → current comparison through a `.csv` snapshot | same counts: 793 / 0 / 2,559 |

The real data had no hard deletes between the two states, so `removed` was
exercised only by editing the local file.

## Found on this run

- **Fixed in 0.6.8: a column that is NULL in every row broke a JSONL snapshot.**
  JSON types are inferred per file, so an all-NULL column read as a different
  type from the published one and 0.6.7 refused the apply as a schema change.
  Seen by nulling `image_url` in all 3,365 rows. From 0.6.8 the column keeps its
  published type and the same change is reported as 2,458 updates; putting the
  values back is another 2,458 updates. On 0.6.7, write a `.csv` snapshot
  instead, at the cost of every published column being text.
- **A refused apply leaves `arsenal dataset status` in `failed`** (exit 1) until
  the next successful apply. This is intended: the publication is unchanged, but
  the new input was not published, and a scheduled status check should say so.
  Preview again and apply to clear it.
- **Timestamps are compared as written.** The older state stored
  `2026-11-16 14:26:16` and the current one `2026-11-17T06:09:55.264Z`; both
  parse for the data period, but a format change alone counts as an update.
- **Whole-table reads.** Every run reads every row through the D1 API, 1,000 per
  request. Measured below on a 105,484-row table: one row read at D1 per row.
- **Not run:** reading the same table through an application's own REST API
  with a Pugio `rest` source, a schedule, and tables with a composite key (the
  script pages on one column).

## Cost of a large table (2026-11-22, de-arsenal 0.6.8 from PyPI)

Two tables from the same production database, on a 4 vCPU Linux machine. Nothing was
written to D1.

| | Narrow table | Wide table |
|---|---:|---:|
| Rows × columns | 105,484 × 3 | 127,331 × 13 |
| Export: requests | 106 | 128 |
| Export: rows read at D1 | 105,484 | 127,331 |
| Export: time | 57 s | 103 s |
| Export: JSONL size | 13.6 MB | 64.8 MB |
| Export: peak memory | 26 MB | 28 MB |
| First preview (includes a cold start) | 7.4 s | 7.6 s |
| Preview with nothing changed | 1.0 s | 1.8 s |
| Preview after removing 2 rows and editing about 100 locally | 1.0 s, counts exact | 1.9 s, counts exact |
| Apply | 1.1 s | 2.0 to 2.4 s |
| Preview and apply: peak memory | about 240 to 270 MB | about 415 to 460 MB |
| Store after the first apply | 4.5 MB | 12.8 MB |

- **The export is the slow part and it is network time**: 0.5 to 0.8 s per request of
  1,000 rows. Its memory does not grow with the table.
- **Row reads at D1 equal the row count**, on every run. A daily run of both tables reads
  about 7 million rows a month. Check that against your plan's allowance.
- **Preview and apply load the whole snapshot into memory**: roughly 7 times the JSONL
  size for the wide table. A table ten times this size would need several gigabytes; that
  was not run.
- **The store does not keep growing.** Over seven applies of the wide table it moved
  between 12.8 MB and 25.8 MB: each apply writes the new table before the old one's blocks
  are released, and the freed blocks are reused by the next. Expect up to twice the size
  of one published table.

**The exporter read far more than this before it was fixed.** Its first version paged with
`WHERE (?1 IS NULL OR key > ?1)`, which SQLite cannot answer with an index seek, so every
page scanned from the first row: page 1 read 1,000 rows at D1 and page 2 read 2,000.
Extrapolated to this table that is about 5.8 million row reads for one export instead of
105,484; that full run was not made. The 3,365-row table above cost about 12,700 reads
instead of 3,365 and went unnoticed. The script now uses a plain `key > ?` seek and
prints the request and row-read counts D1 reports, so a regression shows up in its output.

