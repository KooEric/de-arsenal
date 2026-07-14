"""로컬 파일 소스 — csv/jsonl/excel → Arrow. 분석가의 1번 고통의 입구.

파일 하나 = unit 하나 (unit_key = 글롭 루트 기준 상대 경로) → 멱등이 공짜.
units()는 유한 generator — 러너는 exhausted 없이 루프 자연 종료로 끝난다.
excel은 optional extra(pugio[excel] → fastexcel) — 미설치 시 FatalError.
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.11
"""

import glob as glob_module
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.json as pa_json

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import FileSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult

_GLOB_MAGIC = frozenset("*?[")

_SUFFIX_FORMATS = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".xlsx": "excel",
    ".xls": "excel",
}


def _glob_root(pattern: str) -> Path:
    """글롭 패턴에서 와일드카드 이전의 고정 디렉터리 — unit_key 상대 경로의 기준."""
    fixed: list[str] = []
    for part in Path(pattern).parts:
        if any(ch in part for ch in _GLOB_MAGIC):
            break
        fixed.append(part)
    return Path(*fixed) if fixed else Path()


def _detect_format(suffix: str) -> str:
    fmt = _SUFFIX_FORMATS.get(suffix.lower())
    if fmt is None:
        raise FatalError(f"cannot auto-detect format for suffix {suffix!r}")
    return fmt


def _read_excel(path: Path) -> pa.Table:
    try:
        import fastexcel  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise FatalError(
            "excel support requires the 'excel' extra: pip install pugio[excel]"
        ) from e
    table = fastexcel.read_excel(str(path)).load_sheet(0).to_arrow()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]  # pragma: no cover
    return table  # pyright: ignore[reportUnknownVariableType]  # pragma: no cover


class FileSource:
    def __init__(self, spec: FileSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline

    def units(self) -> Iterator[UnitSpec]:
        """글롭 매칭 파일을 정렬 순서로 열거 — 결정적 순서."""
        root = _glob_root(self._spec.path)
        for p in sorted(glob_module.glob(self._spec.path, recursive=True)):
            rel = str(Path(p).relative_to(root)) if root.parts else p
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.path,
                unit_key=rel,
                payload={"path": p},
            )

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """format(auto=확장자 판별)에 따라 csv/jsonl/excel을 Arrow로.

        파싱 불가 파일은 FatalError. excel은 optional extra(pugio[excel] → fastexcel).
        """
        path = Path(unit.payload["path"])
        fmt = self._spec.format if self._spec.format != "auto" else _detect_format(path.suffix)
        table: pa.Table
        try:
            if fmt == "csv":
                read_opts = pa_csv.ReadOptions(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
                    encoding=self._spec.encoding
                )
                table = pa_csv.read_csv(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
                    path, read_options=read_opts
                )
            elif fmt == "jsonl":
                table = pa_json.read_json(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
                    path
                )
            else:
                table = _read_excel(path)
        except FatalError:
            raise
        except Exception as e:
            raise FatalError(f"cannot parse {path}: {e}") from e
        batches = table.combine_chunks().to_batches()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return FetchResult(
            batch=batches[0] if batches else None,  # pyright: ignore[reportUnknownArgumentType]
            exhausted=False,
        )
