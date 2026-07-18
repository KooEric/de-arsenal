"""벡터화 검증 게이트 — 적재 직전, 행 단위 루프 없이 Arrow 컴퓨트로 검사한다.

M2-E: docs/01-scope.md M2 Task 2.7~2.8. check()가 반환한 GateReport를 러너가
spec.validation.on_violation(block/quarantine/warn)에 따라 처리한다.
"""

from dataclasses import dataclass

import pyarrow as pa
import pyarrow.compute as pc

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import ValidateRule


@dataclass(frozen=True)
class Violation:
    rule: str
    field: str
    count: int


@dataclass(frozen=True)
class GateReport:
    violations: list[Violation]

    @property
    def ok(self) -> bool:
        return not self.violations


def check(batch: pa.RecordBatch, rules: list[ValidateRule]) -> GateReport:
    """규칙마다 pyarrow.compute만으로 위반 건수를 센다 — Python 행 루프 없음."""
    out: list[Violation] = []
    for r in rules:
        try:
            col = batch.column(r.field)
        except (KeyError, pa.lib.ArrowInvalid) as e:
            raise FatalError(f"validate: field {r.field!r} not in batch schema") from e
        if r.not_null:
            n = col.null_count
            if n:
                out.append(Violation("not_null", r.field, n))
        if r.unique:
            distinct_count = pc.count_distinct(col).as_py()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
            dupes = len(col) - int(distinct_count)  # pyright: ignore[reportUnknownArgumentType]
            if dupes:
                out.append(Violation("unique", r.field, dupes))
        # min/max는 null을 무시한다: pc.less/pc.greater는 null 입력에 null을 내고,
        # pc.sum은 null을 건너뛰므로 null 값은 위반으로도 통과로도 세지 않는다
        # (M2-E FIX 7). null을 거부하려면 별도로 not_null 규칙을 추가해야 한다.
        if r.min is not None:
            try:
                below_min = pc.sum(pc.less(col, r.min)).as_py()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
            except (pa.lib.ArrowNotImplementedError, pa.lib.ArrowInvalid) as e:
                raise FatalError(
                    f"validate: min/max requires a numeric field, got {r.field!r}: {e}"
                ) from e
            n = int(below_min or 0)  # pyright: ignore[reportUnknownArgumentType]
            if n:
                out.append(Violation("min", r.field, n))
        if r.max is not None:
            try:
                above_max = pc.sum(pc.greater(col, r.max)).as_py()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
            except (pa.lib.ArrowNotImplementedError, pa.lib.ArrowInvalid) as e:
                raise FatalError(
                    f"validate: min/max requires a numeric field, got {r.field!r}: {e}"
                ) from e
            n = int(above_max or 0)  # pyright: ignore[reportUnknownArgumentType]
            if n:
                out.append(Violation("max", r.field, n))
    return GateReport(out)
