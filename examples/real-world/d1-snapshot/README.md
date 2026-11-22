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

A 105,484-row, 3-column table from the same production database, on a 4 vCPU Linux
machine. Nothing was written to D1.

| Step | Result |
|---|---|
| Export | 106 requests, 105,484 rows read at D1, 57 s, 13.6 MB of JSONL, 26 MB peak memory |
| First preview, then apply | 7.4 s and 1.1 s, about 240 MB peak memory; store 4.5 MB |
| Preview with nothing changed | 1.0 s, file `unchanged`, 0 changes |
| Preview after removing 2 rows and editing 101 in the local file | 1.0 s: `removed` 2, `updated` 101 |
| Apply that change | 1.1 s, 270 MB peak memory; store 8.7 MB |

The export is the slow part and it is network time: about half a second per request.
The row reads count against the D1 plan's allowance on every run, so a daily run of
this table reads about 3.2 million rows a month. Check that figure against your plan.

The store grew from 4.5 MB to 8.7 MB after the second apply. Whether it keeps growing
with every apply was not measured.

**The exporter read far more than this before it was fixed.** Its first version paged with
`WHERE (?1 IS NULL OR key > ?1)`, which SQLite cannot answer with an index seek, so every
page scanned from the first row: page 1 read 1,000 rows at D1 and page 2 read 2,000.
Extrapolated to this table that is about 5.8 million row reads for one export instead of
105,484; that full run was not made. The 3,365-row table above cost about 12,700 reads
instead of 3,365 and went unnoticed. The script now uses a plain `key > ?` seek and
prints the request and row-read counts D1 reports, so a regression shows up in its output.

