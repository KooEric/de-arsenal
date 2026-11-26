"""Is the data itself sound: rows, key uniqueness, NULLs and the period it covers?"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from censor.model import CheckResult, Dimension, Status
from censor.reading import DataReading, Profile
from censor.result import make_result
from censor.target import AuditContext

NULL_WARN_RATIO: Final = 0.5  # half of a column missing deserves a look
NULL_FAIL_RATIO: Final = 1.0  # a column with no value at all carries no data
FUTURE_TOLERANCE: Final = timedelta(days=1)
_DIM: Final = Dimension.DATA_HEALTH
_WORST_SHOWN: Final = 3


def _r(
    kind: str, reading: DataReading, weight: int, status: Status, evidence: str, fix: str = ""
) -> CheckResult:
    check_id = f"data_health.{kind}:{reading.label}"
    return make_result(check_id, _DIM, weight, status, reading.label, evidence, fix)


def _rows(reading: DataReading, profile: Profile) -> CheckResult:
    sampled = " in the sample" if reading.sample else ""
    if profile.rows == 0 and reading.sample:
        evidence = f"the sample is empty ({reading.sample}): nothing to measure"
        return _r("rows", reading, 2, Status.NA, evidence)
    if profile.rows == 0:
        fix = "check that the pipeline wrote output and that the path or table is the right one"
        return _r("rows", reading, 2, Status.FAIL, f"0 rows{sampled}", fix)
    return _r("rows", reading, 2, Status.PASS, f"{profile.rows:,} rows{sampled}")


def _key_checks(reading: DataReading, profile: Profile) -> list[CheckResult]:
    if not reading.key:
        reason = "no key given: pass --key to check uniqueness"
        return [
            _r("key_unique", reading, 3, Status.NA, reason),
            _r("key_not_null", reading, 2, Status.NA, reason),
        ]
    key = ", ".join(reading.key)
    duplicates = profile.duplicate_keys or 0
    if duplicates:
        where = " in the sample" if reading.sample else ""
        unique = _r(
            "key_unique",
            reading,
            3,
            Status.FAIL,
            f"{duplicates:,} values of ({key}) occur more than once{where}",
            "deduplicate on the key (a `dedup` step, or a sink that upserts on `merge_key`)",
        )
    elif reading.sample:
        unique = _r(
            "key_unique",
            reading,
            3,
            Status.WARN,
            f"no duplicate ({key}) found in the sample: passes on the sample only",
            "run with --full, or --since on a recent period, to prove uniqueness",
        )
    else:
        unique = _r("key_unique", reading, 3, Status.PASS, f"({key}) is unique")
    null_rows = profile.key_null_rows or 0
    not_null = _r(
        "key_not_null",
        reading,
        2,
        Status.FAIL if null_rows else Status.PASS,
        f"{null_rows:,} rows have a NULL in ({key})" if null_rows else f"({key}) is never NULL",
        "add `not_null: true` for the key to `validate.rules`, and fix the source rows",
    )
    return [unique, not_null]


def _null_ratio(reading: DataReading, profile: Profile) -> CheckResult:
    ratios = sorted(
        (
            (count / profile.rows, column)
            for column, count in profile.null_counts.items()
            if column not in reading.key
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    flagged = [(ratio, column) for ratio, column in ratios if ratio >= NULL_WARN_RATIO]
    if not flagged:
        worst = f"highest: {ratios[0][1]} {ratios[0][0]:.1%}" if ratios else "no other column"
        evidence = f"no column is {NULL_WARN_RATIO:.0%} NULL or more ({worst})"
        return _r("null_ratio", reading, 2, Status.PASS, evidence)
    status = Status.FAIL if flagged[0][0] >= NULL_FAIL_RATIO else Status.WARN
    shown = ", ".join(f"{column} {ratio:.1%}" for ratio, column in flagged[:_WORST_SHOWN])
    more = f" and {len(flagged) - _WORST_SHOWN} more" if len(flagged) > _WORST_SHOWN else ""
    return _r(
        "null_ratio",
        reading,
        2,
        status,
        f"NULL ratio: {shown}{more}",
        "drop the empty column or fix its mapping; add a `not_null` rule where a value is required",
    )


def _period(ctx: AuditContext, reading: DataReading, profile: Profile) -> CheckResult:
    column = reading.time_column
    if column is None:
        return _r("period", reading, 1, Status.NA, "no time column given: pass --time-column")
    if profile.time_min is None or profile.time_max is None:
        fix = "check the column name and that the source fills it"
        return _r("period", reading, 1, Status.FAIL, f"{column} is NULL in every row", fix)
    span = f"{column} from {profile.time_min} to {profile.time_max}"
    if profile.time_max_at is not None and profile.time_max_at > ctx.now + FUTURE_TOLERANCE:
        fix = "check the time zone and unit of the column (seconds vs milliseconds)"
        return _r(
            "period", reading, 1, Status.WARN, f"{span}: the newest value is in the future", fix
        )
    return _r("period", reading, 1, Status.PASS, span)


def _reading(ctx: AuditContext, reading: DataReading) -> list[CheckResult]:
    if reading.failure is not None:
        fix = "check the column types; report this with the evidence if the data looks right"
        return [_r("profile", reading, 1, Status.ERROR, reading.failure, fix)]
    profile = reading.profile
    if profile is None:
        return [_r("skipped", reading, 1, Status.NA, reading.skipped or "not profiled")]
    results = [_rows(reading, profile)]
    if profile.rows == 0:
        return results
    return [
        *results,
        *_key_checks(reading, profile),
        _null_ratio(reading, profile),
        _period(ctx, reading, profile),
    ]


def check_data_health(ctx: AuditContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for reading in ctx.readings:
        results.extend(_reading(ctx, reading))
    return results
