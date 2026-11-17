"""LogicalPlan v1 and portable-executor preflight tests."""

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gladius.compile import compile_sql, render_duckdb
from gladius.plan import (
    Cast,
    Deduplicate,
    Derive,
    Filter,
    LogicalPlan,
    NonPortablePlanError,
    Portability,
    Project,
    PythonUdf,
    RawSql,
    ReadParquet,
    Rename,
    Select,
    WriteParquet,
    build_logical_plan,
    portability_report,
    preflight_portable,
)
from gladius.spec import TransformSpec


def make(steps: list[dict[str, object]], map_: dict[str, str] | None = None) -> TransformSpec:
    return TransformSpec.model_validate(
        {
            "name": "portable",
            "input": "./in",
            "output": "./out",
            **({"map": map_} if map_ else {}),
            "steps": steps,
        }
    )


def test_logical_plan_v1_has_typed_nodes_for_every_current_transform_step() -> None:
    spec = make(
        map_={"issue_no": "number"},
        steps=[
            {"rename": {"issue_no": "id"}},
            {"cast": {"id": "bigint"}},
            {"filter": "id > 0"},
            {"derive": {"is_open": "state = 'open'"}},
            {"dedup": ["id"]},
            {"select": ["id", "is_open"]},
            {"sql": "SELECT * FROM {input}"},
            {"python": "transforms:slugify", "args": ["id"], "output": "slug"},
        ],
    )

    plan = build_logical_plan(spec)

    assert isinstance(plan, LogicalPlan)
    assert isinstance(plan.nodes[0], ReadParquet)
    assert [type(node) for node in plan.operations] == [
        Project,
        Rename,
        Cast,
        Filter,
        Derive,
        Deduplicate,
        Select,
        RawSql,
        PythonUdf,
    ]
    assert isinstance(plan.nodes[-1], WriteParquet)
    assert plan.read.source == "./in"
    assert plan.write.destination == "./out"


def test_portable_subset_preflights_and_uses_the_logical_plan_renderer() -> None:
    spec = make(
        map_={"issue_no": "number"},
        steps=[
            {"rename": {"issue_no": "id"}},
            {"cast": {"id": "bigint"}},
            {"filter": "id > 0 AND state = 'open'"},
            {"derive": {"title_lower": "lower(title)"}},
            {"dedup": ["id"]},
            {"select": ["id", "title_lower"]},
        ],
    )

    plan = build_logical_plan(spec)
    report = portability_report(plan)

    assert report.is_portable
    assert report.portability is Portability.PORTABLE
    assert preflight_portable(spec) == plan
    # Golden compatibility contract: compile_sql is now a wrapper, not a second compiler.
    assert render_duckdb(plan) == compile_sql(spec)


def test_ansi_expression_forms_and_string_literals_stay_portable() -> None:
    spec = make(
        steps=[
            {"filter": "EXTRACT(YEAR FROM created_at) >= 2026"},
            {"derive": {"literal": "'list_contains('"}},
        ]
    )

    assert portability_report(spec).is_portable


def test_portable_plan_matches_expected_local_duckdb_result(tmp_path: Path) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table(
            {
                "number": [1, 1, 2],
                "state": ["open", "open", "closed"],
                "title": ["First", "Duplicate", "Skipped"],
            }
        ),
        tmp_path / "input.parquet",
    )
    spec = TransformSpec.model_validate(
        {
            "name": "result-equality",
            "input": str(tmp_path),
            "output": str(tmp_path / "out"),
            "steps": [
                {"filter": "state = 'open'"},
                {"rename": {"number": "id"}},
                {"cast": {"id": "bigint"}},
                {"derive": {"title_lower": "lower(title)"}},
                {"dedup": ["id"]},
                {"select": ["id", "title_lower"]},
            ],
        }
    )
    plan = preflight_portable(spec)
    con = duckdb.connect()
    try:
        assert con.execute(render_duckdb(plan)).fetchall() == [(1, "first")]
    finally:
        con.close()


def test_duckdb_specific_expression_is_rejected_before_portable_execution() -> None:
    spec = make(steps=[{"filter": "list_contains(tags, 'priority')"}])

    report = portability_report(spec)

    assert report.portability is Portability.DUCKDB_ONLY
    assert report.issues[0].location == "step 0 (filter)"
    assert "list_contains" in report.issues[0].message
    with pytest.raises(NonPortablePlanError, match="not portable"):
        preflight_portable(spec)
    # Local mode stays deliberately permissive for current DuckDB-only expressions.
    assert "list_contains(tags, 'priority')" in compile_sql(spec)


def test_raw_sql_and_python_udf_are_rejected_by_portable_preflight() -> None:
    spec = make(
        steps=[
            {"sql": "SELECT * FROM {input} LIMIT 10"},
            {"python": "transforms:slugify", "args": ["title"], "output": "slug"},
        ]
    )

    report = portability_report(spec)

    assert report.portability is Portability.ENGINE_LOCAL
    assert [(issue.portability, issue.location) for issue in report.issues] == [
        (Portability.ENGINE_LOCAL, "step 0 (sql)"),
        (Portability.ENGINE_LOCAL, "step 1 (python)"),
    ]
    with pytest.raises(NonPortablePlanError, match="raw sql"):
        preflight_portable(spec)
    assert "LIMIT 10" in compile_sql(spec)
    assert '"__gladius_python_udf_1"("title")' in compile_sql(spec)
