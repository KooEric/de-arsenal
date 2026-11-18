"""로컬 파일 소스 — csv/jsonl/excel → Arrow. 분석가의 1번 고통의 입구.

파일 하나 = unit 하나 (unit_key = 글롭 루트 기준 상대 경로) → 멱등이 공짜.
units()는 유한 generator — 러너는 exhausted 없이 루프 자연 종료로 끝난다.
excel은 optional extra(pugio[excel] → fastexcel) — 미설치 시 FatalError.
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.11
"""

import glob as glob_module
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
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


_COLUMN_TYPES: dict[str, pa.DataType] = {
    "string": pa.string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
}

# "01234", "-007": 숫자로 읽으면 앞자리 0이 사라지는 값 (단독 "0"은 해당 없음).
_LEADING_ZERO = r"^-?0[0-9]+$"


def _read_csv(
    path: Path,
    read_opts: Any,
    null_values: list[str],
    types: dict[str, Any],
    only: list[str] | None = None,
) -> Any:
    convert_opts = pa_csv.ConvertOptions(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
        null_values=null_values, column_types=types, include_columns=only
    )
    return pa_csv.read_csv(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
        path, read_options=read_opts, convert_options=convert_opts
    )


def _identifier_columns(path: Path, read_opts: Any, table: pa.Table, pinned: set[str]) -> list[str]:
    """정수로 추론됐지만 원문에 앞자리 0이 있는 컬럼 (우편번호·사번·계좌번호).

    정수 컬럼만 문자열로 한 번 더 읽어 확인한다. 숫자로 두면 "01234"가 1234가 되는
    조용한 손실이므로 이런 컬럼은 문자열로 남긴다.
    """
    candidates = [
        field.name
        for field in table.schema
        if pa.types.is_integer(field.type) and field.name not in pinned
    ]
    if not candidates:
        return []
    as_text = dict.fromkeys(candidates, pa.string())
    raw: pa.Table = _read_csv(path, read_opts, [""], as_text, only=candidates)
    found: list[str] = []
    for name in candidates:
        hits = pc.match_substring_regex(raw.column(name), _LEADING_ZERO)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAttributeAccessIssue]
        if pc.any(hits).as_py():  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
            found.append(name)
    return found


class FileSource:
    def __init__(self, spec: FileSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline

    def units(self) -> Iterator[UnitSpec]:
        """글롭 매칭 파일을 정렬 순서로 열거 — 결정적 순서."""
        root = _glob_root(self._spec.path)
        matches = sorted(glob_module.glob(self._spec.path, recursive=True))
        if not matches and not self._spec.allow_empty:
            raise FatalError(
                f"source.path '{self._spec.path}' matches no files (looked from "
                f"{Path.cwd()}). Put the input files there, fix the path, or set "
                "`allow_empty: true` if an empty folder is normal"
            )
        for p in matches:
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
                # pyarrow의 기본 null 토큰("NA", "N/A", "null", …)을 쓰면 컬럼 값이
                # 전부 "NA"인 파일에서 타입이 null로 추론돼 값이 통째로 사라진다 —
                # 같은 데이터가 파일 분할 방식에 따라 다르게 적재되는 조용한 손상.
                nulls = list(self._spec.null_values)
                pinned: dict[str, Any] = {
                    name: _COLUMN_TYPES[kind] for name, kind in self._spec.column_types.items()
                }
                table = _read_csv(path, read_opts, nulls, pinned)
                missing = sorted(set(pinned) - set(table.column_names))
                if missing:
                    raise FatalError(
                        f"source.column_types names columns that {path} does not have: "
                        f"{missing} (columns: {table.column_names})"
                    )
                keep_text = _identifier_columns(path, read_opts, table, set(pinned))
                if keep_text:
                    pinned.update(dict.fromkeys(keep_text, pa.string()))
                    table = _read_csv(path, read_opts, nulls, pinned)
            elif fmt == "jsonl":
                table = pa_json.read_json(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportPrivateImportUsage]
                    path
                )
            else:
                table = _read_excel(path)
            if fmt != "csv" and self._spec.column_types:
                raise FatalError("source.column_types applies to csv files only")
        except FatalError:
            raise
        except Exception as e:
            raise FatalError(f"cannot parse {path}: {e}") from e
        batches = table.combine_chunks().to_batches()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return FetchResult(
            batch=batches[0] if batches else None,  # pyright: ignore[reportUnknownArgumentType]
            exhausted=False,
        )
