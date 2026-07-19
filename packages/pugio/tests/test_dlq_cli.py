from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from pugio.cli import app

runner = CliRunner()

VALIDATE_YAML = """
name: dlq-cli-test
state_dir: {state_dir}
source:
  type: file
  path: {glob}
sink:
  type: parquet
  path: {out}
validate:
  rules:
    - field: v
      not_null: true
  on_violation: quarantine
"""


def write_validate_spec(tmp_path: Path) -> Path:
    (tmp_path / "a.csv").write_text("id,v\n1,x\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,v\n2,\n", encoding="utf-8")  # not_null 위반 → quarantine
    p = tmp_path / "pipe.yaml"
    p.write_text(
        VALIDATE_YAML.format(
            state_dir=tmp_path / ".arsenal",
            glob=str(tmp_path / "*.csv"),
            out=tmp_path / "out",
        ),
        encoding="utf-8",
    )
    return p


def test_dlq_list_shows_quarantined(tmp_path: Path) -> None:
    spec = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["dlq", "list", str(spec)])
    assert result.exit_code == 0
    assert "b.csv" in result.output


def test_dlq_list_no_quarantine(tmp_path: Path) -> None:
    (tmp_path / "clean.csv").write_text("id,v\n1,x\n", encoding="utf-8")
    p = tmp_path / "clean.yaml"
    p.write_text(
        """
name: clean-t
state_dir: {state_dir}
source:
  type: file
  path: {glob}
sink:
  type: parquet
  path: {out}
""".format(
            state_dir=tmp_path / ".arsenal",
            glob=str(tmp_path / "clean.csv"),
            out=tmp_path / "out",
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", str(p)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["dlq", "list", str(p)])
    assert result.exit_code == 0
    assert "no quarantined units" in result.output


def test_dlq_retry_requeues_and_removes_files(tmp_path: Path) -> None:
    from arsenal_core.spec import load_pipeline
    from arsenal_core.state import StateStore, UnitSpec

    spec_path = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0

    spec = load_pipeline(spec_path)
    assert spec.source.type == "file"
    unit_id = UnitSpec.create(
        pipeline=spec.name, source=spec.source.path, unit_key="b.csv", payload={}
    ).unit_id

    result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", unit_id])
    assert result.exit_code == 0

    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        rec = store.get(unit_id)
    finally:
        store.close()
    assert rec.status == "pending"

    dlq_dir = tmp_path / ".arsenal" / "dlq" / spec.name
    assert not (dlq_dir / f"{unit_id}.parquet").exists()
    assert not (dlq_dir / f"{unit_id}.json").exists()


@respx.mock
def test_dlq_retry_invalid_spec_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    result = runner.invoke(app, ["dlq", "retry", str(bad), "--unit", "whatever"])
    assert result.exit_code == 1
    assert "error" in result.output


def test_dlq_list_then_retry_by_unit_id(tmp_path: Path) -> None:
    """M2-E FIX 1 (CRITICAL): 실제 흐름을 그대로 태운다 — quarantine → `dlq list` →
    그 출력에서 unit_id(첫 컬럼)를 뽑아 → `dlq retry --unit <unit_id>`. 예전엔 `dlq
    list`가 unit_key를 찍었고 `dlq retry --unit`은 unit_id(sha256)로 매칭해서,
    복사-붙여넣기한 unit_key가 0행을 조용히 갱신하고도 성공 메시지를 냈다."""
    from arsenal_core.spec import load_pipeline
    from arsenal_core.state import StateStore

    spec_path = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0

    list_result = runner.invoke(app, ["dlq", "list", str(spec_path)])
    assert list_result.exit_code == 0
    line = next(ln for ln in list_result.output.splitlines() if ln.strip())
    unit_id, unit_key, *_rest = line.split()
    assert unit_key == "b.csv"  # 읽기용 컬럼은 여전히 unit_key

    retry_result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", unit_id])
    assert retry_result.exit_code == 0

    spec = load_pipeline(spec_path)
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        rec = store.get(unit_id)
    finally:
        store.close()
    assert rec.status == "pending"

    d = tmp_path / ".arsenal" / "dlq" / spec.name
    assert not (d / f"{unit_id}.parquet").exists()
    assert not (d / f"{unit_id}.json").exists()


def test_dlq_retry_unknown_id_errors(tmp_path: Path) -> None:
    """존재하지 않는(또는 unit_key를 잘못 넘긴) id는 0행 갱신 → exit 1, 거짓 성공 금지
    (M2-E FIX 1)."""
    spec_path = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", "bogus-unit-id"])
    assert result.exit_code == 1
    assert "no quarantined unit with id bogus-unit-id" in result.output


@respx.mock
def test_dlq_retry_rejected_for_cursor_mode(tmp_path: Path) -> None:
    """M2-E FIX 2 (CRITICAL): cursor/link 페이지네이션은 프론티어 커서가 앞으로만
    전진해서 격리된 과거 페이지의 unit_key를 재실행이 다시 생성할 수 없다 — retry는
    거부해야 하고, requeue도 evidence 삭제도 일어나면 안 된다."""
    from arsenal_core.spec import load_pipeline
    from arsenal_core.state import StateStore

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"v": None}], "meta": {"next": None}})

    respx.get("https://api.test/cursor-dlq").mock(side_effect=responder)

    spec_path = tmp_path / "pipe.yaml"
    spec_path.write_text(
        f"""
name: cursor-dlq-test
state_dir: {tmp_path / ".arsenal"}
source:
  type: rest
  url: https://api.test/cursor-dlq
  pagination:
    mode: cursor
    cursor_param: after
    cursor_path: meta.next
    record_path: data
    size: 1
sink:
  type: parquet
  path: {tmp_path / "out"}
validate:
  rules:
    - field: v
      not_null: true
  on_violation: quarantine
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0

    list_result = runner.invoke(app, ["dlq", "list", str(spec_path)])
    assert list_result.exit_code == 0
    line = next(ln for ln in list_result.output.splitlines() if ln.strip())
    unit_id = line.split()[0]

    retry_result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", unit_id])
    assert retry_result.exit_code == 1
    assert "cursor/link" in retry_result.output

    spec = load_pipeline(spec_path)
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        rec = store.get(unit_id)
    finally:
        store.close()
    assert rec.status == "quarantined"  # requeue되지 않았다

    d = tmp_path / ".arsenal" / "dlq" / spec.name
    assert (d / f"{unit_id}.parquet").exists()  # evidence 그대로
    assert (d / f"{unit_id}.json").exists()
