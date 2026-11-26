from datetime import UTC, datetime
from pathlib import Path

from _data import DUPLICATE, ROWS, write_duckdb, write_parquet
from _projects import GUARDED_COLLECT, MANIFEST, TRANSFORM, bare_project, write_project

from censor.audit import AuditRequest, run_audit
from censor.context import load_context
from censor.derive import derive_targets
from censor.model import CheckResult, Dimension, Status
from censor.target import DuckDBTarget, FilesTarget

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)


def _standard(root: Path) -> dict[str, CheckResult]:
    report = run_audit(AuditRequest(root=root, tier="standard", now=NOW)).report
    return {c.id: c for c in report.checks if c.dimension == Dimension.DATA_HEALTH}


def test_targets_come_from_the_sink_and_the_transform_output(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {"arsenal.yaml": MANIFEST, "collect.yaml": GUARDED_COLLECT, "transform.yaml": TRANSFORM},
    )
    sink, output = derive_targets(load_context(root))
    assert sink.label == "collect.yaml" and sink.key == ("id",)
    assert sink.target == FilesTarget(str(root.absolute() / "data/raw" / "**" / "*.parquet"))
    assert output.label == "transform.yaml" and output.key == ("id",)
    assert output.target == FilesTarget(str(root.absolute() / "data/clean" / "**" / "*.parquet"))


def test_duckdb_sink_uses_the_merge_key_and_other_sinks_have_no_target(tmp_path: Path) -> None:
    duck = GUARDED_COLLECT.replace(
        "type: parquet\n  path: ./data/raw",
        "type: duckdb\n  path: ./out.duckdb\n  table: rows\n  merge_key: [id, v]",
    )
    root = write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST.replace("transforms:\n  - transform.yaml\n", ""),
            "collect.yaml": duck,
        },
    )
    (only,) = derive_targets(load_context(root))
    assert only.target == DuckDBTarget(root.absolute() / "out.duckdb", "rows")
    assert only.key == ("id", "v")
    remote = GUARDED_COLLECT.replace("./data/raw", "s3://bucket/raw")
    write_project(root, {"collect.yaml": remote})
    (only,) = derive_targets(load_context(root))
    assert only.target is None and "not a local" in only.reason


def test_project_without_output_yet_is_na(tmp_path: Path) -> None:
    checks = _standard(bare_project(tmp_path))
    assert set(checks) == {"data_health.skipped:collect.yaml", "data_health.skipped:transform.yaml"}
    assert all(c.status == Status.NA for c in checks.values())
    assert "no file matches" in checks["data_health.skipped:collect.yaml"].evidence


def test_project_output_is_profiled_with_the_derived_key(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_parquet(root / "data" / "raw" / "u1" / "part.parquet", [*ROWS, DUPLICATE])
    write_parquet(root / "data" / "clean" / "part.parquet", [*ROWS, DUPLICATE])
    checks = _standard(root)
    assert checks["data_health.rows:collect.yaml"].evidence == "5 rows"
    assert checks["data_health.key_unique:collect.yaml"].status == Status.NA  # no unique rule
    assert checks["data_health.key_unique:transform.yaml"].status == Status.FAIL  # dedup: [id]


def test_a_derived_key_missing_from_the_output_does_not_abort_the_audit(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_parquet(root / "data" / "clean" / "part.parquet", [{"other": 1}])
    check = _standard(root)["data_health.skipped:transform.yaml"]
    assert check.status == Status.NA and "no column id" in check.evidence


def test_duckdb_sink_output_is_profiled(tmp_path: Path) -> None:
    duck = GUARDED_COLLECT.replace(
        "type: parquet\n  path: ./data/raw",
        "type: duckdb\n  path: ./out.duckdb\n  table: rows\n  merge_key: [id]",
    )
    root = write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST.replace("transforms:\n  - transform.yaml\n", ""),
            "collect.yaml": duck,
        },
    )
    write_duckdb(root / "out.duckdb", "rows", 50)
    checks = _standard(root)
    assert checks["data_health.key_unique:collect.yaml"].status == Status.PASS
