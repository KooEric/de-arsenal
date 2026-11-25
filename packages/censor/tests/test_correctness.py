from pathlib import Path

import pytest
from _projects import MANIFEST, TRANSFORM, bare_project, write_project

from censor.checks.correctness import check_correctness
from censor.context import load_context
from censor.model import CheckResult, Status


def _by_id(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.id: r for r in results}


def test_valid_project_passes_every_correctness_check(tmp_path: Path) -> None:
    results = _by_id(check_correctness(load_context(bare_project(tmp_path))))
    assert results["correctness.manifest"].status == Status.PASS
    assert results["correctness.pipeline:collect.yaml"].status == Status.PASS
    assert results["correctness.transform:transform.yaml"].status == Status.PASS


def test_invalid_manifest_fails_as_blocker_and_stops(tmp_path: Path) -> None:
    write_project(tmp_path, {"arsenal.yaml": "pipelines: [a.yaml]\n"})
    results = check_correctness(load_context(tmp_path))
    assert [r.id for r in results] == ["correctness.manifest"]
    assert results[0].status == Status.FAIL and results[0].blocker


def test_misspelled_pipeline_field_fails_as_blocker(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(
        "name: x\nsource:\n  type: file\n  path: a.csv\n  format: csv\n"
        "sink:\n  type: parquett\n  path: out\n",
        encoding="utf-8",
    )
    result = _by_id(check_correctness(load_context(tmp_path)))["correctness.pipeline:collect.yaml"]
    assert result.status == Status.FAIL
    assert result.blocker
    assert "sink" in result.evidence


def test_unset_env_var_is_a_warning_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CENSOR_TEST_DSN", raising=False)
    write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST,
            "collect.yaml": (
                "name: x\nsource:\n  type: file\n  path: ${CENSOR_TEST_DSN}\n  format: csv\n"
                "sink:\n  type: parquet\n  path: out\n"
            ),
            "transform.yaml": TRANSFORM,
        },
    )
    result = _by_id(check_correctness(load_context(tmp_path)))["correctness.pipeline:collect.yaml"]
    assert result.status == Status.WARN
    assert not result.blocker
    assert "CENSOR_TEST_DSN" in result.evidence


def test_transform_that_does_not_compile_fails(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "transform.yaml").write_text(
        "name: t\ninput: ./data/raw\nsteps:\n  - dedupe: [id]\noutput: ./clean\n",
        encoding="utf-8",
    )
    result = _by_id(check_correctness(load_context(tmp_path)))[
        "correctness.transform:transform.yaml"
    ]
    assert result.status == Status.FAIL and result.blocker


def test_dbt_transform_is_not_applicable(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {"arsenal.yaml": "name: d\ntransforms:\n  - dbt: ./dbt\n"},
    )
    results = check_correctness(load_context(tmp_path))
    dbt = [r for r in results if r.id.startswith("correctness.transform:")][0]
    assert dbt.status == Status.NA
