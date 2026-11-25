from pathlib import Path

from _projects import COLLECT_BARE, bare_project

from censor.checks.guards import check_guards
from censor.context import load_context
from censor.model import Status

GUARDED = (
    COLLECT_BARE
    + """\
validate:
  rules:
    - { field: id, not_null: true, unique: true }
  on_violation: split
schema_drift: block
contract:
  columns:
    - { name: id, type: int64, nullable: false }
"""
)


def _statuses(tmp_path: Path, collect: str) -> dict[str, Status]:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(collect, encoding="utf-8")
    return {r.id.split(":")[0]: r.status for r in check_guards(load_context(tmp_path))}


def test_bare_pipeline(tmp_path: Path) -> None:
    assert _statuses(tmp_path, COLLECT_BARE) == {
        "guards.validate": Status.FAIL,
        "guards.on_violation": Status.NA,
        "guards.key_rules": Status.NA,
        "guards.schema_drift": Status.WARN,
        "guards.contract": Status.WARN,
    }


def test_fully_guarded_pipeline_passes(tmp_path: Path) -> None:
    assert set(_statuses(tmp_path, GUARDED).values()) == {Status.PASS}


def test_omitted_on_violation_warns_about_quarantine_default(tmp_path: Path) -> None:
    collect = COLLECT_BARE + "validate:\n  rules:\n    - { field: amount, min: 0 }\n"
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(collect, encoding="utf-8")
    results = {r.id.split(":")[0]: r for r in check_guards(load_context(tmp_path))}
    assert results["guards.on_violation"].status == Status.WARN
    assert "quarantine" in results["guards.on_violation"].evidence
    assert results["guards.key_rules"].status == Status.WARN


def test_unparseable_spec_is_skipped(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(": : :\n", encoding="utf-8")
    assert check_guards(load_context(tmp_path)) == []


def test_empty_validate_mapping_counts_as_no_validate_block(tmp_path: Path) -> None:
    assert _statuses(tmp_path, COLLECT_BARE + "validate: {}\n") == _statuses(tmp_path, COLLECT_BARE)
