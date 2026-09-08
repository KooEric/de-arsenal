"""augur CLI — index / ask / eval / report. 러너 층: 여기서만 예외를 잡는다."""

import json
import sys
import time
import typing as t
from pathlib import Path
from typing import Annotated

import duckdb
import typer
import yaml

from arsenal_core.errors import ArsenalError
from augur.catalog import Catalog, build_catalog
from augur.evaluate import load_cases, run_eval, summarize
from augur.generate import append_trace, ask
from augur.llm import provider_from_name

app = typer.Typer(help="Augur — 자연어 → SQL (schema RAG) + eval")
DEFAULT_CATALOG = Path("./data/augur/catalog.json")
DEFAULT_TRACES = Path("./data/augur/traces.jsonl")
DEFAULT_EVAL_OUT = Path("./data/augur/eval")


def _fail(e: Exception) -> t.NoReturn:
    typer.echo(f"error: {e}", err=True)
    raise typer.Exit(1) from e


@app.command()
def index(
    sources: Annotated[Path, typer.Argument(help="YAML: {tables: {name: './data/x/*.parquet'}}")],
    catalog: Annotated[Path, typer.Option("--catalog")] = DEFAULT_CATALOG,
) -> None:
    """Parquet 소스를 스캔해 스키마 카탈로그를 만든다."""
    try:
        data = t.cast(dict[str, t.Any], yaml.safe_load(sources.read_text(encoding="utf-8")))
        cat = build_catalog({str(k): str(v) for k, v in data["tables"].items()})
        cat.save(catalog)
    except (ArsenalError, OSError, KeyError) as e:
        _fail(e)
    typer.echo(f"indexed {len(cat.tables)} tables → {catalog}")


@app.command(name="ask")
def ask_cmd(
    question: str,
    catalog: Annotated[Path, typer.Option("--catalog")] = DEFAULT_CATALOG,
    provider: str = typer.Option("anthropic", "--provider", help="anthropic | openai"),
    model: Annotated[str | None, typer.Option("--model")] = None,
    top_k: int = typer.Option(3, "--top-k"),
    traces: Annotated[Path, typer.Option("--traces")] = DEFAULT_TRACES,
    show_sql: bool = typer.Option(True, "--show-sql/--no-show-sql"),
) -> None:
    """질문 → SQL → 실행. 모든 호출은 traces.jsonl에 남는다."""
    try:
        cat = Catalog.load(catalog)
        prov = provider_from_name(provider, model)
        trace, rows = ask(question, cat, prov, top_k=top_k)
        append_trace(traces, trace)
    except ArsenalError as e:
        _fail(e)
    if show_sql:
        typer.echo(f"-- retrieved: {trace.retrieved_tables}\n{trace.sql}\n", err=True)
    if trace.error:
        typer.echo(f"error: {trace.error}", err=True)
        raise typer.Exit(1)
    for row in rows:
        typer.echo(json.dumps([str(v) for v in row], ensure_ascii=False))


@app.command(name="eval")
def eval_cmd(
    cases: Path,
    catalog: Annotated[Path, typer.Option("--catalog")] = DEFAULT_CATALOG,
    provider: str = typer.Option("anthropic", "--provider"),
    model: Annotated[str | None, typer.Option("--model")] = None,
    top_k: int = typer.Option(3, "--top-k"),
    out: Annotated[Path, typer.Option("--out")] = DEFAULT_EVAL_OUT,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """케이스 파일 전체를 실행하고 실패 모드별 집계를 출력한다."""
    rid = run_id or time.strftime("%Y%m%dT%H%M%S")
    try:
        cat = Catalog.load(catalog)
        prov = provider_from_name(provider, model)
        results = run_eval(load_cases(cases), cat, prov, top_k=top_k, run_id=rid, out_dir=out)
    except ArsenalError as e:
        _fail(e)
    summary = summarize(results)
    total = len(results)
    typer.echo(f"run_id={rid} provider={provider} top_k={top_k} n={total}")
    for mode, n in summary.items():
        typer.echo(f"  {mode:<20} {n:>4}  {n / total:6.1%}")
    typer.echo(f"→ {out / (rid + '.parquet')}")


@app.command()
def report(out: Annotated[Path, typer.Option("--out")] = DEFAULT_EVAL_OUT) -> None:
    """모든 run의 실패 모드 추이 — 개선 전후 비교용."""
    pattern = str(out / "*.parquet")
    try:
        duckdb.sql(  # pyright: ignore[reportUnknownMemberType]
            f"SELECT run_id, provider, failure_mode, count(*) AS n FROM '{pattern}' "
            "GROUP BY 1,2,3 ORDER BY 1,3"
        ).show()
    except duckdb.Error as e:
        _fail(e)


if __name__ == "__main__":
    sys.exit(app())
