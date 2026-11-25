"""Do the manifest and every spec load and compile? Same rules as `arsenal check`."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Final

from arsenal.project import DbtProjectRef

from arsenal_core.errors import ArsenalError
from arsenal_core.spec import load_pipeline
from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status
from gladius.compile import compile_sql
from gladius.loader import load_transform

_DIM: Final = Dimension.CORRECTNESS
_ENV_NOT_SET: Final = "environment variable not set"


def _result(
    check_id: str, weight: int, status: Status, subject: str, evidence: str, fix: str = ""
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=_DIM,
        weight=weight,
        status=status,
        blocker=status == Status.FAIL,
        subject=subject,
        evidence=evidence,
        fix=fix,
    )


def _name(ctx: ProjectContext, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.root))
    except ValueError:
        return str(path)


def _pipeline(ctx: ProjectContext, path: Path) -> CheckResult:
    name = _name(ctx, path)
    check_id = f"correctness.pipeline:{name}"
    try:
        load_pipeline(path)
    except ArsenalError as e:
        message = str(e)
        if message.startswith(_ENV_NOT_SET):
            return _result(
                check_id,
                2,
                Status.WARN,
                name,
                f"could not fully validate: {message}",
                "set the variable in this shell to validate the whole spec",
            )
        return _result(check_id, 2, Status.FAIL, name, message, "fix the field the message names")
    return _result(check_id, 2, Status.PASS, name, "spec is valid")


def _transform(ctx: ProjectContext, ref: Path | DbtProjectRef) -> CheckResult:
    if isinstance(ref, DbtProjectRef):
        name = _name(ctx, ref.dbt)
        return _result(
            f"correctness.transform:{name}", 2, Status.NA, name, "dbt projects are not checked"
        )
    name = _name(ctx, ref)
    check_id = f"correctness.transform:{name}"
    try:
        compile_sql(load_transform(ref))
    except ArsenalError as e:
        return _result(check_id, 2, Status.FAIL, name, str(e), "fix the step the message names")
    return _result(check_id, 2, Status.PASS, name, "compiles")


def check_correctness(ctx: ProjectContext) -> list[CheckResult]:
    if ctx.manifest is None:
        return [
            _result(
                "correctness.manifest",
                3,
                Status.FAIL,
                "arsenal.yaml",
                ctx.manifest_error or "invalid manifest",
                "fix arsenal.yaml, then run `arsenal check`",
            )
        ]
    results = [_result("correctness.manifest", 3, Status.PASS, "arsenal.yaml", "manifest is valid")]
    with contextlib.chdir(
        ctx.root
    ):  # specs use paths relative to the project, like `arsenal check`
        results.extend(_pipeline(ctx, p.path) for p in ctx.pipelines)
        results.extend(_transform(ctx, t) for t in ctx.transforms)
    return results
