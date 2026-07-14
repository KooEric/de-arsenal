"""E2E: 각 원클릭 레시피가 실제로 끝까지 실행되는지 검증한다 (M4 Task 4.1).

깨진 레시피는 원클릭이 아니라 원클릭 사기다 — 그래서 각 레시피는 여기서
`arsenal init` → (fixture로 url/경로 치환) → `arsenal run` 전 과정을 거친다.
"""

import csv
from pathlib import Path

import duckdb
import pytest
import respx
from arsenal.cli import app
from httpx import Response
from typer.testing import CliRunner

runner = CliRunner()


def test_github_issues_recipe_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-token-for-tests")
    dest = tmp_path / "gh-proj"

    init_result = runner.invoke(app, ["init", "github-issues", "--dest", str(dest)])
    assert init_result.exit_code == 0, init_result.output

    issues = [
        {"number": 1, "title": "first bug", "state": "open", "created_at": "2020-01-01"},
        {"number": 2, "title": "second bug", "state": "closed", "created_at": "2020-02-01"},
        {"number": 3, "title": "no state", "state": None, "created_at": "2020-03-01"},
    ]

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(
            "https://api.github.com/repos/duckdb/duckdb/issues",
            params={"page": 0, "per_page": 100},
        ).mock(return_value=Response(200, json=issues))

        run_result = runner.invoke(app, ["run", "--project", str(dest)])

    assert run_result.exit_code == 0, run_result.output

    out_dir = dest / "data" / "issues_clean"
    assert out_dir.exists()
    parquet_files = list(out_dir.glob("*.parquet"))
    assert parquet_files

    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT number, title, state FROM read_parquet('{out_dir}/*.parquet') ORDER BY number"
        ).fetchall()
    finally:
        con.close()

    # state IS NOT NULL 필터를 통과한 것은 issue 1, 2 뿐 — issue 3은 걸러진다.
    assert rows == [(1, "first bug", "open"), (2, "second bug", "closed")]


def test_csv_cleanup_recipe_runs_end_to_end(tmp_path: Path) -> None:
    dest = tmp_path / "csv-proj"

    init_result = runner.invoke(app, ["init", "csv-cleanup", "--dest", str(dest)])
    assert init_result.exit_code == 0, init_result.output

    input_dir = dest / "input"
    input_dir.mkdir(parents=True)
    with (input_dir / "orders.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "amount"])
        writer.writerow([1, "Alice", "10.5"])
        writer.writerow([2, "Bob", "20.0"])
        writer.writerow([1, "Alice Duplicate", "99.9"])  # 중복 id — dedup 대상
        writer.writerow([3, "Carol", "5.5"])

    run_result = runner.invoke(app, ["run", "--project", str(dest)])
    assert run_result.exit_code == 0, run_result.output

    out_dir = dest / "data" / "clean"
    assert out_dir.exists()
    parquet_files = list(out_dir.glob("*.parquet"))
    assert parquet_files

    con = duckdb.connect()
    try:
        (count,) = con.execute(
            f"SELECT count(DISTINCT id) FROM read_parquet('{out_dir}/*.parquet')"
        ).fetchone()  # pyright: ignore[reportGeneralTypeIssues]
        (total,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{out_dir}/*.parquet')"
        ).fetchone()  # pyright: ignore[reportGeneralTypeIssues]
    finally:
        con.close()

    # dedup: [id] — 4개 입력 행 중 id=1이 중복이므로 3개 고유 id만 남는다.
    assert total == 3
    assert count == 3


def test_broken_recipe_fails_the_test(tmp_path: Path) -> None:
    """대조군: 실제로 깨진 레시피는 이 스위트가 잡아낸다는 확인.

    존재하지 않는 레시피 이름은 init 단계에서 즉시 실패해야 한다 — 조용히
    부분 스캐폴드가 만들어지면 안 된다.
    """
    dest = tmp_path / "broken-proj"
    result = runner.invoke(app, ["init", "not-a-real-recipe", "--dest", str(dest)])
    assert result.exit_code == 1
    assert not dest.exists() or not any(dest.iterdir())
