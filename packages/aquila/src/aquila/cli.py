"""``aquila`` — scan, search and inspect the catalog. JSON on stdout, errors on stderr."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from aquila import service
from aquila.lineage import Direction, to_mermaid, walk
from aquila.remote import iceberg_loader
from aquila.store import CatalogStore, Dataset

app = typer.Typer(
    help="Aquila — datasets, schema history, lineage, contracts and tags in one file."
)

DbOption = typer.Option(
    service.DEFAULT_DB, "--db", help="Catalog file (default: .arsenal/aquila.db)"
)
PathsArgument = typer.Argument(None, help="Spec files or directories (default: .)")
RootOption = typer.Option(Path("."), help="Directory relative spec paths resolve against")
RemoteOption = typer.Option(
    False, "--remote", help="Also read remote Parquet footers (S3/GCS/Azure, ambient credentials)"
)
IcebergOption = typer.Option(
    None, "--iceberg-uri", help="Iceberg REST catalog URI: record every schema of iceberg:// tables"
)
IcebergTokenOption = typer.Option(
    None, "--iceberg-token-env", help="Environment variable holding the catalog token"
)


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _open(db: Path, *, must_exist: bool = True) -> CatalogStore:
    if must_exist and not db.exists():
        typer.echo(f"error: catalog not found: {db} (run `aquila scan` first)", err=True)
        raise typer.Exit(1)
    return CatalogStore(db)


def _dataset(store: CatalogStore, ref: str) -> Dataset:
    found = store.resolve(ref)
    if found is None:
        typer.echo(f"error: unknown dataset {ref!r} (try `aquila search`)", err=True)
        raise typer.Exit(1)
    return found


@app.command()
def scan(
    paths: list[Path] | None = PathsArgument,
    db: Path = DbOption,
    root: Path = RootOption,
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be recorded"),
    remote: bool = RemoteOption,
    iceberg_uri: str | None = IcebergOption,
    iceberg_token_env: str | None = IcebergTokenOption,
) -> None:
    """Record datasets, schema versions, lineage and contracts. Idempotent."""

    targets = paths or [Path(".")]
    base = root.resolve()
    try:
        iceberg = iceberg_loader(iceberg_uri, token_env=iceberg_token_env) if iceberg_uri else None
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if dry_run:
        result = service.collect(targets, root=base, remote=remote, iceberg=iceberg)
        _emit(
            {
                "dry_run": True,
                "datasets": sorted(result.datasets),
                "edges": len(result.edges),
                "schemas": len(result.schemas),
                "contracts": len(result.contracts),
                "skipped": result.skipped,
            }
        )
        return
    store = _open(db, must_exist=False)
    try:
        report = service.scan(store, targets, root=base, remote=remote, iceberg=iceberg)
        _emit(
            {
                "datasets": report.datasets,
                "new_datasets": report.new_datasets,
                "new_schema_versions": report.new_schema_versions,
                "contract_changes": report.contract_changes,
                "removed_edges": report.removed_edges,
                "removed_contracts": report.removed_contracts,
                "cycles": service.cycle_names(store),
                "warnings": service.deprecation_warnings(store)
                + service.multiple_writer_warnings(store),
                "skipped": report.skipped,
                "totals": store.counts(),
            }
        )
    finally:
        store.close()


@app.command()
def search(query: str, db: Path = DbOption) -> None:
    """Full-text search over names, descriptions, tags and column names."""

    store = _open(db)
    try:
        _emit(
            [
                {"name": d.name, "uri": d.uri, "status": d.status, "tags": d.tags}
                for d in store.search(query)
            ]
        )
    finally:
        store.close()


@app.command()
def show(
    ref: str,
    db: Path = DbOption,
    max_age_seconds: float | None = typer.Option(
        None, help="Also assess freshness against this age"
    ),
) -> None:
    """Metadata, latest schema, contracts, one hop of lineage and freshness."""

    store = _open(db)
    try:
        payload = service.show(store, _dataset(store, ref), max_age_seconds=max_age_seconds)
    finally:
        store.close()
    _emit(payload)
    if payload["freshness"].get("alert") or payload["contract_findings"]:
        raise typer.Exit(1)


@app.command()
def history(ref: str, db: Path = DbOption) -> None:
    """Schema versions over time, each with its diff from the previous one."""

    store = _open(db)
    try:
        _emit(service.history(store, _dataset(store, ref)))
    finally:
        store.close()


@app.command()
def lineage(
    ref: str,
    db: Path = DbOption,
    depth: int = typer.Option(3, min=0),
    direction: str = typer.Option("both", help="up | down | both"),
    fmt: str = typer.Option("json", "--format", help="json | mermaid"),
) -> None:
    """Upstream/downstream datasets around one dataset."""

    if direction not in {"up", "down", "both"} or fmt not in {"json", "mermaid"}:
        typer.echo("error: --direction is up|down|both and --format is json|mermaid", err=True)
        raise typer.Exit(1)
    store = _open(db)
    try:
        dataset = _dataset(store, ref)
        graph = walk(
            store.edges(), dataset.dataset_id, direction=_direction(direction), depth=depth
        )
        names = {d.dataset_id: d.name for d in store.datasets()}
        publications = store.publications(graph.nodes)
    finally:
        store.close()
    if fmt == "mermaid":
        typer.echo(to_mermaid(graph, names, publications))
        return
    _emit(
        {
            "root": names[graph.root],
            **(
                {"publications": {names[k]: v for k, v in publications.items()}}
                if publications
                else {}
            ),
            "nodes": sorted(names.get(n, n) for n in graph.nodes),
            "edges": [
                {"from": names.get(e.upstream_id), "to": names.get(e.downstream_id), "via": e.via}
                for e in graph.edges
            ],
        }
    )


def _direction(value: str) -> Direction:
    return "up" if value == "up" else "down" if value == "down" else "both"


@app.command()
def tag(ref: str, pairs: list[str], db: Path = DbOption) -> None:
    """Set governance tags: `aquila tag orders pii=true owner=data-team`."""

    parsed: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            typer.echo(f"error: expected key=value, got {pair!r}", err=True)
            raise typer.Exit(1)
        parsed[key.strip()] = value.strip()
    store = _open(db)
    try:
        dataset = _dataset(store, ref)
        store.set_tags(dataset.dataset_id, parsed)
        updated = store.get(dataset.dataset_id)
        _emit(updated.tags if updated is not None else {})
    finally:
        store.close()


@app.command()
def describe(ref: str, text: str, db: Path = DbOption) -> None:
    """Set a dataset's description (searchable)."""

    store = _open(db)
    try:
        store.set_description(_dataset(store, ref).dataset_id, text)
    finally:
        store.close()


@app.command()
def deprecate(ref: str, db: Path = DbOption, reason: str | None = typer.Option(None)) -> None:
    """Mark a dataset deprecated; scans and `show` warn every reader of it."""

    store = _open(db)
    try:
        dataset = _dataset(store, ref)
        store.deprecate(dataset.dataset_id, reason)
        readers = [w for w in service.deprecation_warnings(store) if f" {dataset.name}" in w]
    finally:
        store.close()
    _emit({"deprecated": dataset.name, "readers": readers})


if __name__ == "__main__":
    app()
