# Quickstart — collect → transform → query in under 10 minutes

No account, no Docker, no network — this example ships its own sample data
(`orders.csv`) so every command below works offline, copy-paste, top to bottom.

## 1. Install

Pick one:

```bash
uv tool install de-arsenal
# or, if you don't use uv:
pipx install de-arsenal
```

This installs three commands: `arsenal`, `pugio`, `gladius`.

## 2. Collect

```bash
cd examples/quickstart
pugio run collect.yaml
```

Reads `orders.csv` and writes Parquet to `./data/orders`.

## 3. Transform

```bash
gladius run transform.yaml
```

Keeps only `paid` orders and casts `amount` to a double. Writes Parquet to
`./data/orders_clean`.

## 4. Query

```bash
arsenal query "SELECT status, count(*), sum(amount) FROM './data/orders_clean/*.parquet' GROUP BY 1"
```

You should see one row: `paid | 3 | 262.49`.
