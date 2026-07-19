"""M4 Task 4.3 — `scripts/gen_schema_docs.py`의 생성 산출물과 `--check` 드리프트
가드를 검증한다.

스크립트는 경로를 CWD 기준 상대경로로 해석하므로(레포 루트에서 실행하는 것이
정상 사용법), 여기서는 서브프로세스의 cwd를 tmp_path로 지정해 실제로 커밋된
schemas/, docs/reference/ 아티팩트를 건드리지 않고 격리 테스트한다.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "gen_schema_docs.py"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_write_mode_generates_expected_markdown_and_json(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr

    pipeline_md = (tmp_path / "docs" / "reference" / "pipeline-schema.md").read_text(
        encoding="utf-8"
    )
    assert "source" in pipeline_md
    assert "sink" in pipeline_md
    assert "pagination" in pipeline_md
    assert "AUTO-GENERATED" in pipeline_md

    transform_md = (tmp_path / "docs" / "reference" / "transform-schema.md").read_text(
        encoding="utf-8"
    )
    assert "steps" in transform_md
    assert "input" in transform_md
    assert "output" in transform_md
    assert "AUTO-GENERATED" in transform_md

    assert (tmp_path / "schemas" / "pipeline.json").exists()
    assert (tmp_path / "schemas" / "transform.json").exists()


def test_check_mode_passes_on_freshly_generated_files(tmp_path: Path) -> None:
    write_result = _run(tmp_path)
    assert write_result.returncode == 0, write_result.stderr

    check_result = _run(tmp_path, "--check")
    assert check_result.returncode == 0, check_result.stdout + check_result.stderr


def test_check_mode_fails_when_markdown_is_hand_edited(tmp_path: Path) -> None:
    _run(tmp_path)
    drifted = tmp_path / "docs" / "reference" / "pipeline-schema.md"
    drifted.write_text(
        drifted.read_text(encoding="utf-8") + "\nhand-edited drift\n", encoding="utf-8"
    )

    result = _run(tmp_path, "--check")

    assert result.returncode == 1
    assert "pipeline-schema.md" in result.stdout


def test_check_mode_fails_when_json_schema_is_missing(tmp_path: Path) -> None:
    _run(tmp_path)
    (tmp_path / "schemas" / "transform.json").unlink()

    result = _run(tmp_path, "--check")

    assert result.returncode == 1
    assert "transform.json" in result.stdout
