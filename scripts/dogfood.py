#!/usr/bin/env python3
"""Run the DE Arsenal products against one coherent dataset.

The default dummy mode is completely offline.  It installs an in-process HTTP
mock that supports cursor pagination and deliberately returns one transient 503
so the runner's retry path is exercised.  Live mode uses the read-only GitHub
Issues API and is opt-in.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pyarrow as pa
import respx
from arsenal.cli import app as arsenal_app
from onager.cli import app as onager_app
from onager.compact import compact_dataset
from scorpio import assess_freshness
from scutum import ContractColumn, DataContract
from spatha import TaskSpec, Workflow
from typer.testing import CliRunner

from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore
from gladius.cli import app as gladius_app
from gladius.engine import query, run_transform
from gladius.loader import load_transform
from pugio.cli import app as pugio_app
from pugio.runner import run_pipeline


class SimulatedCrash(RuntimeError):
    """Deterministic process interruption used by the dummy reliability check."""


@contextmanager
def dummy_api(records: list[dict[str, Any]]) -> Generator[str, None, None]:
    """Expose a deterministic HTTP-shaped API without binding a local port."""
    source_url = "https://dogfood.invalid/records"
    failed_once = False

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal failed_once
        parsed = urlparse(str(request.url))
        if parsed.path != "/records":
            return httpx.Response(404, request=request)
        params = parse_qs(parsed.query)
        cursor = params.get("cursor", [None])[0]
        if cursor == "cursor-1" and not failed_once:
            failed_once = True
            return httpx.Response(503, request=request)
        pages: dict[str | None, tuple[list[dict[str, Any]], str | None]] = {
            None: (records[:3], "cursor-1"),
            "cursor-1": (records[3:6], "cursor-2"),
            "cursor-2": (records[6:], None),
        }
        rows, next_cursor = pages.get(cursor, ([], None))
        return httpx.Response(
            200,
            json={"data": rows, "next_cursor": next_cursor},
            request=request,
        )

    with respx.mock(assert_all_called=False) as router:
        router.get(source_url).mock(side_effect=respond)
        yield source_url


@contextmanager
def working_directory(path: Path) -> Generator[None, None, None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    """Write JSON, which is also valid YAML, without adding a new dependency."""
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def invoke(app: Any, args: list[str]) -> str:
    result = CliRunner().invoke(app, args)
    if result.exit_code != 0:
        raise RuntimeError(
            f"command failed: {' '.join(args)}\n{result.stdout}"
        ) from result.exception
    return result.stdout.strip()


def dummy_records() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "customer": "Alice",
            "amount": 42.50,
            "status": "paid",
            "updated_at": "2026-08-14",
        },
        {
            "id": 2,
            "customer": "Bob",
            "amount": 17.00,
            "status": "pending",
            "updated_at": "2026-08-14",
        },
        {
            "id": 3,
            "customer": "Carol",
            "amount": 99.99,
            "status": "paid",
            "updated_at": "2026-08-14",
        },
        {
            "id": 4,
            "customer": "Dave",
            "amount": 5.25,
            "status": "refunded",
            "updated_at": "2026-08-14",
        },
        {
            "id": 5,
            "customer": "Erin",
            "amount": 120.00,
            "status": "paid",
            "updated_at": "2026-08-14",
        },
        {
            "id": 6,
            "customer": "Frank",
            "amount": 8.00,
            "status": "cancelled",
            "updated_at": "2026-08-14",
        },
        {
            "id": 7,
            "customer": "Grace",
            "amount": 50.00,
            "status": "paid",
            "updated_at": "2026-08-14",
        },
        {
            "id": 2,
            "customer": "Bob",
            "amount": 18.00,
            "status": "pending",
            "updated_at": "2026-08-14",
        },
    ]


def create_project(root: Path, *, source_url: str, live: bool) -> tuple[Path, Path, Path]:
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)

    source: dict[str, Any] = {
        "type": "rest",
        "url": source_url,
        "pagination": {
            "mode": "link" if live else "cursor",
            "size_param": "per_page" if live else "limit",
            "size": 30 if live else 3,
            **(
                {}
                if live
                else {"cursor_param": "cursor", "cursor_path": "next_cursor", "record_path": "data"}
            ),
        },
    }
    if live and os.getenv("GITHUB_TOKEN"):
        source["auth"] = {"type": "static_token", "token_env": "GITHUB_TOKEN"}

    collect: dict[str, Any] = {
        "name": "dogfood-api",
        "state_dir": ".arsenal",
        "source": source,
        "sink": {"type": "parquet", "path": "data/raw"},
    }
    if not live:
        collect["contract"] = {
            "columns": [
                {"name": "id", "type": "int64", "nullable": False},
                {"name": "customer", "type": "string", "nullable": False},
                {"name": "amount", "type": "double", "nullable": False},
                {"name": "status", "type": "string", "nullable": False},
                {"name": "updated_at", "type": "string", "nullable": False},
            ],
            "allow_extra": False,
            "on_violation": "block",
        }

    if live:
        transform = {
            "name": "dogfood-clean",
            "input": "data/raw",
            "steps": [
                {"filter": "state IS NOT NULL"},
                {"derive": {"title_length": "length(title)"}},
                {"dedup": ["number"]},
                {
                    "select": [
                        "number",
                        "title",
                        "state",
                        "created_at",
                        "updated_at",
                        "title_length",
                    ]
                },
            ],
            "output": "data/clean",
        }
    else:
        transform = {
            "name": "dogfood-clean",
            "input": "data/raw",
            "steps": [
                {"filter": "status IN ('paid', 'pending')"},
                {"derive": {"amount_with_tax": "round(amount * 1.1, 2)"}},
                {"dedup": ["id"]},
                {"select": ["id", "customer", "amount", "status", "amount_with_tax"]},
            ],
            "output": "data/clean",
        }

    collect_path = project / "collect.yaml"
    transform_path = project / "transform.yaml"
    write_yaml(collect_path, collect)
    write_yaml(transform_path, transform)
    write_yaml(
        project / "arsenal.yaml",
        {"name": "dogfood", "pipelines": ["collect.yaml"], "transforms": ["transform.yaml"]},
    )
    return project, collect_path, transform_path


def check_contract(records: list[dict[str, Any]]) -> tuple[bool, bool]:
    contract = DataContract(
        name="dogfood-orders",
        columns=[
            ContractColumn(name="id", type="int64", nullable=False),
            ContractColumn(name="customer", type="string", nullable=False),
        ],
    )
    valid_record = {"id": records[0]["id"], "customer": records[0]["customer"]}
    valid = contract.check(pa.RecordBatch.from_pylist([valid_record]))
    invalid_record = {**valid_record, "unexpected": "schema drift"}
    invalid = contract.check(pa.RecordBatch.from_pylist([invalid_record]))
    return valid.ok, not invalid.ok


def run_products(
    root: Path, *, live: bool, source_url: str, records: list[dict[str, Any]] | None
) -> None:
    project, collect_path, transform_path = create_project(root, source_url=source_url, live=live)
    raw = project / "data" / "raw"
    clean = project / "data" / "clean"

    print(f"workdir: {project}")
    print("[1/8] Arsenal — scaffold and umbrella manifest")
    scaffold = root / "arsenal-scaffold"
    print(invoke(arsenal_app, ["init", "csv-cleanup", "--dest", str(scaffold)]))

    with working_directory(project):
        print("[2/8] Pugio — collect, retry, resume, and no-op rerun")
        spec = load_pipeline(collect_path)
        if live:
            first = run_pipeline(spec)
        else:

            def crash_after_first(done_count: int) -> None:
                if done_count == 1:
                    raise SimulatedCrash("dogfood crash after first completed cursor page")

            try:
                run_pipeline(spec, on_unit_complete=crash_after_first)
            except SimulatedCrash as error:
                print(f"  injected crash: {error}")
            first = run_pipeline(spec)
        no_op = run_pipeline(spec)
        if first.written == 0 and not live:
            raise AssertionError(f"dummy resume wrote nothing: {first}")
        if no_op.fetched != 0 or no_op.written != 0:
            raise AssertionError(f"completed rerun was not a no-op: {no_op}")
        print(f"  resumed: fetched={first.fetched} written={first.written} skipped={first.skipped}")
        print(f"  rerun:   fetched={no_op.fetched} written={no_op.written} skipped={no_op.skipped}")

        print("[3/8] arsenal-core — persistent state, metrics, and lineage")
        store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
        try:
            counts = store.counts(spec.name)
            metrics = store.pipeline_metrics(spec.name)
            lineage = store.lineage(spec.name)
        finally:
            store.close()
        if not live and counts.get("done") != 3:
            raise AssertionError(f"unexpected dummy state counts: {counts}")
        source_type = lineage.source_type if lineage else "none"
        sink_type = lineage.sink_type if lineage else "none"
        print(f"  states={counts} rows={metrics.row_count} lineage={source_type}→{sink_type}")

        print("[4/8] Scutum — valid and invalid Arrow contract checks")
        if records is not None:
            valid, invalid = check_contract(records)
            if not (valid and invalid):
                raise AssertionError("Scutum contract checks did not produce expected results")
            print("  valid batch=pass invalid batch=blocked")
        else:
            print("  live mode: contract is intentionally omitted; inspect schema with Gladius")

        print("[5/8] Scorpio — freshness from the persisted completion timestamp")
        freshness = assess_freshness(
            metrics.last_completed_at,
            max_age_seconds=3600,
            now=datetime.now(UTC),
        )
        if freshness.status != "fresh":
            raise AssertionError(f"expected fresh data, got {freshness}")
        print(f"  status={freshness.status} age_seconds={freshness.age_seconds:.3f}")

        print("[6/8] Onager — inspect then compact the small Parquet files")
        print(
            invoke(
                onager_app,
                [
                    "compact",
                    str(raw),
                    "--target-bytes",
                    "1000000",
                    "--min-files",
                    "2",
                    "--dry-run",
                ],
            )
        )
        compacted = compact_dataset(raw, target_bytes=1_000_000, min_files=2)
        file_count = len(list(raw.glob("*.parquet")))
        print(
            f"  applied: selected={compacted.selected_files} "
            f"compacted={compacted.compacted} files={file_count}"
        )

        print("[7/8] Spatha — execute collect → compact → transform by dependency")
        transform_spec = load_transform(transform_path)
        executed: list[str] = []

        def execute(task: TaskSpec) -> None:
            if task.name == "collect":
                run_pipeline(spec)
            elif task.name == "compact":
                compact_dataset(raw, target_bytes=1_000_000, min_files=2)
            elif task.name == "transform":
                run_transform(transform_spec)
            executed.append(task.name)

        workflow = Workflow(
            tasks=[
                TaskSpec(name="collect", signals=["source-ready"]),
                TaskSpec(name="compact", depends_on=["collect"]),
                TaskSpec(name="transform", depends_on=["compact"]),
            ]
        )
        workflow.run_ready(execute, signals={"source-ready"})
        if executed != ["collect", "compact", "transform"]:
            raise AssertionError(f"unexpected workflow order: {executed}")
        print(f"  executed={executed}")

        print("[8/8] Gladius + Arsenal — compile, transform, query, and status")
        compiled = invoke(gladius_app, ["compile", str(transform_path)])
        if "SELECT" not in compiled.upper():
            raise AssertionError("Gladius did not emit SQL")
        query_sql = (
            f"SELECT count(*) AS rows, count(DISTINCT id) AS unique_ids, "
            f"round(sum(amount_with_tax), 2) AS metric FROM read_parquet('{clean}/*.parquet')"
            if not live
            else f"SELECT count(*) AS rows, count(DISTINCT number) AS unique_ids, "
            f"sum(title_length) AS metric FROM read_parquet('{clean}/*.parquet')"
        )
        result = query(query_sql)
        if result.num_rows != 1:
            raise AssertionError(f"unexpected query result: {result.to_pylist()}")
        print(f"  compiled_sql_lines={len(compiled.splitlines())} result={result.to_pylist()}")
        print(invoke(arsenal_app, ["run", "--project", str(project)]))
        print(invoke(arsenal_app, ["query", query_sql, "--format", "jsonl"]))
        print(
            invoke(pugio_app, ["status", str(collect_path), "--cost", "--max-age-seconds", "3600"])
        )

    print("dogfood: PASS")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dummy", "live"), default="dummy")
    parser.add_argument("--workdir", type=Path, default=Path(".dogfood"))
    parser.add_argument("--repo", default="octocat/Hello-World", help="GitHub repo for --mode live")
    parser.add_argument(
        "--reset", action="store_true", help="remove the exact --workdir before starting"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.mode == "live" and not os.getenv("GITHUB_TOKEN"):
        raise SystemExit(
            "--mode live requires GITHUB_TOKEN to avoid exhausting the shared "
            "unauthenticated GitHub rate limit"
        )
    if args.workdir.exists() and any(args.workdir.iterdir()):
        if not args.reset:
            raise SystemExit(
                f"workdir is not empty: {args.workdir} (use --reset or another --workdir)"
            )
        shutil.rmtree(args.workdir)
    args.workdir.mkdir(parents=True, exist_ok=True)

    if args.mode == "dummy":
        records = dummy_records()
        with dummy_api(records) as source_url:
            run_products(args.workdir, live=False, source_url=source_url, records=records)
    else:
        since = (datetime.now(UTC) - timedelta(days=7)).replace(microsecond=0)
        source_url = (
            "https://api.github.com/repos/"
            + args.repo
            + "/issues?"
            + urlencode({"state": "open", "since": since.isoformat().replace("+00:00", "Z")})
        )
        run_products(args.workdir, live=True, source_url=source_url, records=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
