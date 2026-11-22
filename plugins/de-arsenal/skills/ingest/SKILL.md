---
name: ingest
description: Get data into a DE Arsenal project from CSV, Excel or JSONL files, a REST API, or a database, by writing a Pugio collect YAML. Use for broken Korean text or other encoding trouble, leading zeros disappearing, "NA" or "-" cells, a new file arriving every month, an API with pagination or a token, copying a database table, or "이 파일 읽어줘", "한글이 깨져", "API에서 데이터 받아와줘", "DB 테이블 가져와줘".
---

# Ingest

The person points at the data in words ("the Excel exports in this folder",
"our orders API"). You inspect it, write the spec, run it, and tell them what
came in. They do not need to read the YAML.

Look at the data before writing the spec. Most collection problems are visible
in the first hundred rows, and guessing costs a rerun.

## Files

### 1. Profile

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/ingest/scripts/profile_file.py path/to/file.csv
```

It reports encoding, delimiter, header line, columns that must stay text,
null-like tokens and key candidates, and prints a `source:` block to paste.
It exits 1 and says why when Pugio cannot read the file as it is.

### 2. Write the spec

```yaml
name: employees                 # also the name of the state file under .arsenal/
source:
  type: file
  path: ./input/*.csv           # a glob; each file is one unit of work
  format: csv                   # auto | csv | jsonl | excel
  encoding: cp949               # default utf-8; Korean Excel exports are usually cp949
  null_values: ["", "NA"]       # default is only the empty cell
  column_types: { 사번: string } # CSV only: string|int64|float64|bool|date|timestamp
sink:
  type: parquet
  path: ./data/raw
```

### 3. What goes wrong, and the setting that fixes it

| Seen in the profile | Do this |
|---|---|
| Encoding is not utf-8 | `encoding:` with the codec the profiler names |
| Leading zeros (zip codes, employee ids, phone numbers) | Already kept as text. Pin with `column_types` so every file agrees |
| `NA`, `-`, `N/A` in a numeric or date column | List them in `null_values` |
| The same tokens in a text column | They stay as text: `null_values` does not apply to text columns. Clean in a transform: `nullif(col, '-')` |
| Numbers written `1,200,000` | Arrive as text. In a transform: `TRY_CAST(replace(col, ',', '') AS BIGINT)` |
| Tab, semicolon or pipe delimiter | Not supported by the file source. Rewrite with `--normalize` |
| Title lines above the header, duplicate or blank column names | Same: `--normalize` |
| Excel with several sheets | Only the first sheet is read. Save each sheet as its own file |
| Excel or JSONL with wrong types | No type pinning for these. Cast in a transform |

`--normalize` writes a UTF-8, comma-delimited copy and leaves the original
alone. Keep originals outside the folder the glob reads:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/ingest/scripts/profile_file.py original/sales.tsv \
  --normalize input/sales.csv
```

### 4. Files that keep arriving

- New files in the folder: nothing to do. A file already collected is skipped
  by its path; only new ones are read.
- A file that gets overwritten with new content under the same name: set
  `detect_changes: true` and use an upsert sink (`duckdb` or `postgres`), or
  the old and new versions both stay in the output.
- A drop folder that is sometimes empty: `allow_empty: true`. Without it, a
  path that matches nothing is an error, which is what you want for a typo.

## REST API

Make one request by hand first (`curl`) and read the response: where the list
of records is, and how the next page is named.

```yaml
name: orders-api
source:
  type: rest
  url: https://api.example.com/v1/orders
  headers: { Accept: application/json }
  auth: { type: static_token, token_env: API_TOKEN }   # sends Authorization: Bearer
  pagination:
    mode: page            # offset | page | cursor | link
    param: page
    size_param: per_page
    size: 100
    record_path: data     # where the array is in the response; omit if the body is the array
    stop_on: empty_page
  rate_limit: { rps: 5 }
sink:
  type: parquet
  path: ./data/orders
```

| The API does this | `mode` | Also set |
|---|---|---|
| `?offset=200&limit=100` | `offset` | `param`, `size_param`, `size` |
| `?page=3&per_page=100` | `page` | `param`, `size_param`, `start_page` |
| Returns a token for the next page in the body | `cursor` | `cursor_param`, `cursor_path` |
| Returns a `Link: <...>; rel="next"` header (GitHub, GitLab) | `link` | `size_param`, `size` |

Things that silently lose data if skipped:

- **Use `stop_on: empty_page` for `offset` and `page`** unless the server's
  maximum page size is known. The default stops at the first page shorter than
  `size`; if the server caps pages below `size`, that is page one, and the run
  reports success with one page.
- **A key in the query string** is written as `${ENV_VAR}` inside `url`. It
  will then appear in the state file and logs; say so to the person.
- **A response that is not UTF-8** (many Korean public-data APIs): `encoding: euc-kr`.
- After the first run, compare the row count with the total the API reports.

## Database table

```yaml
name: customers
source:
  type: database
  dialect: postgres        # postgres | mysql | sqlite
  dsn_env: SOURCE_DSN      # the variable's name; the value stays out of the file
  table: public.customers
  split: { key: id, chunk: 100000 }   # a numeric, indexed column
sink:
  type: parquet
  path: ./data/customers
```

The table is read in chunks of `split.key`, each chunk a unit that is skipped
on rerun. A wrong password can be retried several times before the run fails;
that is a known misclassification, not a network problem.

## Where it lands

| Want | `sink` |
|---|---|
| Files to query locally, cheapest | `{ type: parquet, path: ./data/x }` |
| One local database file | `{ type: duckdb, path: ./warehouse.duckdb, table: x, merge_key: [id] }` |
| A Postgres table others read | `{ type: postgres, dsn_env: PG_DSN, table: x, merge_key: [id] }` |

`merge_key` is the column or columns that identify a row. A row arriving again
with the same key replaces the old one, so reruns cannot create duplicates.
The profiler marks candidates; confirm with the person when it is not obvious.

## Run and check

```bash
pugio run collect.yaml
pugio status collect.yaml --cost       # units done, rows, bytes
pugio run collect.yaml                 # again: must report skipped, not written
gladius query "SELECT count(*) FROM './data/raw/*.parquet'"
gladius query "DESCRIBE SELECT * FROM './data/raw/*.parquet'"
```

Report the row count next to the source's row count, and the column types.
If they differ, go to `doctor` before anything is built on top.
