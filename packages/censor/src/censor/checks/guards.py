"""Does each pipeline keep bad data out? Reads raw YAML so unset env vars do not matter."""

from __future__ import annotations

from typing import Any, Final, cast

from censor.context import ProjectContext, SpecFile
from censor.model import CheckResult, Dimension, Status
from censor.result import make_result

_DIM: Final = Dimension.GUARDS


def _r(
    kind: str, spec: SpecFile, weight: int, status: Status, evidence: str, fix: str
) -> CheckResult:
    check_id = f"guards.{kind}:{spec.name}"
    return make_result(check_id, _DIM, weight, status, spec.name, evidence, fix)


def _rules(validate: dict[str, Any]) -> list[dict[str, Any]]:
    rules: object = validate.get("rules", [])
    if not isinstance(rules, list):
        return []
    return [cast(dict[str, Any], r) for r in cast(list[object], rules) if isinstance(r, dict)]


def _pipeline_guards(spec: SpecFile, raw: dict[str, Any]) -> list[CheckResult]:
    validate_raw: object = raw.get("validate")
    validate = cast(dict[str, Any], validate_raw) if isinstance(validate_raw, dict) else None
    if not validate:  # an empty or non-mapping block guards nothing
        validate = None
    results = [
        _r(
            "validate",
            spec,
            3,
            Status.PASS if validate else Status.FAIL,
            "validate block present"
            if validate
            else "no validate block: bad values load unchecked",
            "add `validate.rules` from what the first clean run showed (trust skill)",
        ),
    ]
    if validate is None:
        results.append(_r("on_violation", spec, 2, Status.NA, "no validate block", ""))
        results.append(_r("key_rules", spec, 2, Status.NA, "no validate block", ""))
    else:
        explicit = "on_violation" in validate
        results.append(
            _r(
                "on_violation",
                spec,
                2,
                Status.PASS if explicit else Status.WARN,
                f"on_violation: {validate['on_violation']}"
                if explicit
                else "on_violation omitted: defaults to quarantine, which sets whole files aside",
                "write `on_violation: split` unless one bad row must reject the file",
            )
        )
        keyed = any(r.get("not_null") or r.get("unique") for r in _rules(validate))
        results.append(
            _r(
                "key_rules",
                spec,
                2,
                Status.PASS if keyed else Status.WARN,
                "a rule checks not_null or unique"
                if keyed
                else "no not_null/unique rule on any field",
                "add `{ field: <key>, not_null: true, unique: true }` for the row key",
            )
        )
    drift = raw.get("schema_drift", "allow")
    results.append(
        _r(
            "schema_drift",
            spec,
            2,
            Status.PASS if drift in ("warn", "block") else Status.WARN,
            f"schema_drift: {drift}",
            "set `schema_drift: block` (or `warn`) so changed source columns are noticed",
        )
    )
    has_contract = isinstance(raw.get("contract"), dict)
    results.append(
        _r(
            "contract",
            spec,
            1,
            Status.PASS if has_contract else Status.WARN,
            "contract declared" if has_contract else "no contract on the output shape",
            "declare `contract.columns` when someone downstream depends on exact columns",
        )
    )
    return results


def check_guards(ctx: ProjectContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in ctx.pipelines:
        if spec.raw is not None:
            results.extend(_pipeline_guards(spec, spec.raw))
    return results
