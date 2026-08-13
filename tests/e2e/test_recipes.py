"""E2E: 각 원클릭 레시피가 실제로 끝까지 실행되는지 검증한다 (M4 Task 4.1).

깨진 레시피는 원클릭이 아니라 원클릭 사기다 — 그래서 각 레시피는 여기서
`arsenal init` → (fixture로 url/경로 치환) → `arsenal run` 전 과정을 거친다.
"""

import csv
from collections.abc import Iterator
from pathlib import Path

import duckdb
import psycopg
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
    with (input_dir / "orders.csv").open("w", newline="", encoding="utf-8") as f:
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


@pytest.fixture(scope="module")
def api_to_postgres_pg_url(require_docker: None) -> Iterator[str]:
    """api-to-postgres 레시피 E2E 전용 Postgres 컨테이너.

    testcontainers 모듈이 없거나(미설치) Docker 데몬이 미가용인 환경에서는
    이 fixture를 쓰는 테스트만 스킵된다 — 나머지 레시피 E2E(github-issues/
    csv-cleanup)는 영향받지 않는다.
    """
    pgtc = pytest.importorskip("testcontainers.postgres")
    with pgtc.PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url(driver=None)


def test_api_to_postgres_recipe_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_to_postgres_pg_url: str
) -> None:
    """REST → validate 게이트 → Postgres upsert, 3종 레시피의 마지막 하나.

    페이지네이션(mode=page)은 무한 generator라 종료 신호가 오직 마지막 fetch의
    `exhausted`뿐이다 — 이 테스트는 바로 그 마지막 페이지를 검증 위반으로 만들어
    quarantine 경로와 종료 경로가 동시에 걸리는 조합을 실제로 태운다.
    """
    monkeypatch.setenv("PG_DSN", api_to_postgres_pg_url)
    dest = tmp_path / "api-pg-proj"

    init_result = runner.invoke(app, ["init", "api-to-postgres", "--dest", str(dest)])
    assert init_result.exit_code == 0, init_result.output

    # 실제 레시피 기본값(size: 100)은 그대로 두되, 테스트에서만 페이지 크기를
    # 줄여 "몇 페이지"를 실제로 오가게 한다.
    collect_path = dest / "collect.yaml"
    collect_path.write_text(
        collect_path.read_text(encoding="utf-8").replace("size: 100", "size: 3"), encoding="utf-8"
    )

    page1 = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}, {"id": 3, "name": "Carol"}]
    # 마지막 페이지(len=2 < size=3 → exhausted) 이면서 동시에 id가 null인 위반
    # 레코드를 담아, quarantine과 exhausted 종료가 같은 fetch에서 겹치게 한다.
    page2 = [{"id": None, "name": "Bad"}, {"id": 4, "name": "Dana"}]

    def run_once() -> None:
        with respx.mock(assert_all_called=False) as respx_mock:
            respx_mock.get(
                "https://api.example.com/records", params={"page": 1, "per_page": 3}
            ).mock(return_value=Response(200, json=page1))
            respx_mock.get(
                "https://api.example.com/records", params={"page": 2, "per_page": 3}
            ).mock(return_value=Response(200, json=page2))
            result = runner.invoke(app, ["run", "--project", str(dest)])
        assert result.exit_code == 0, result.output

    run_once()

    with psycopg.connect(api_to_postgres_pg_url) as con, con.cursor() as cur:
        cur.execute("SELECT id, name FROM records ORDER BY id")
        rows = cur.fetchall()
    # id=1,2,3만 적재된다 — page2(마지막 페이지)는 not_null/unique 위반으로
    # 통째로 격리되어 id=4(Dana)도 함께 적재되지 않는다 (페이지 단위 격리).
    assert rows == [(1, "Alice"), (2, "Bob"), (3, "Carol")]

    dlq_dir = dest / ".arsenal" / "dlq" / "api-to-postgres"
    dlq_files = list(dlq_dir.glob("*.parquet"))
    assert len(dlq_files) == 1  # page2 하나만 격리

    # 멱등성: 재실행해도 (이미 done인 page1은 skip, 여전히 quarantined인 page2만
    # 재시도되어 다시 격리) Postgres 행 수는 그대로여야 한다 — 중복 upsert 없음.
    run_once()

    with psycopg.connect(api_to_postgres_pg_url) as con, con.cursor() as cur:
        cur.execute("SELECT count(*) FROM records")
        (count,) = cur.fetchone()  # pyright: ignore[reportGeneralTypeIssues]
    assert count == 3

    dlq_files_after = list(dlq_dir.glob("*.parquet"))
    assert len(dlq_files_after) == 1  # 여전히 격리 1건, 중복 생성 없음


def test_broken_recipe_fails_the_test(tmp_path: Path) -> None:
    """대조군: 실제로 깨진 레시피는 이 스위트가 잡아낸다는 확인.

    존재하지 않는 레시피 이름은 init 단계에서 즉시 실패해야 한다 — 조용히
    부분 스캐폴드가 만들어지면 안 된다.
    """
    dest = tmp_path / "broken-proj"
    result = runner.invoke(app, ["init", "not-a-real-recipe", "--dest", str(dest)])
    assert result.exit_code == 1
    assert not dest.exists() or not any(dest.iterdir())
