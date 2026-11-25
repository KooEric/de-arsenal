# Censor Plan 1: Foundation and Quick Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working `censor audit --tier quick` that grades a DE Arsenal project on five static dimensions, saves history, writes a Markdown report, and is also reachable as `arsenal eval`.

**Architecture:** A new workspace package `packages/censor` (PyPI `de-censor`). Pure functions turn a loaded `ProjectContext` into frozen `CheckResult` models; `scoring.py` and `grade.py` turn checks into an `AuditReport`; `history.py` and `report.py` persist it; `cli.py` wires Typer. `arsenal eval` lazily imports `censor.cli.run_cli`.

**Tech Stack:** Python 3.11, Pydantic v2 (frozen models), Typer, PyYAML, PyArrow (Parquet history), pytest + `typer.testing.CliRunner`.

Spec: [2026-11-25-censor-design.md](2026-11-25-censor-design.md). Later plans: Plan 2 = Standard tier (reliability, freshness, data health, scale guard, file/DuckDB/Postgres targets). Plan 3 = Augur link, `evaluate` skill and `attach-findings`, v0.7.0 release.

## Global Constraints

- Python `>=3.11`; ruff line length 100; pyright `strict` covers `packages/`.
- Coverage gate `--cov-fail-under=80` (root `pyproject.toml`), tests run with `--import-mode=importlib`.
- All workspace packages share one version. Until Plan 3 bumps everything to 0.7.0, `de-censor` is `0.6.16` with cross pins `>=0.6,<0.7`.
- `de-censor` depends on `de-arsenal` (for `load_project`). `de-arsenal` must **not** declare a dependency on `de-censor` (no `[eval]` extra); `arsenal eval` imports it lazily and prints an install hint when it is missing.
- Censor never writes outside `<target>/.arsenal/censor/`. It never prints a secret value; evidence names the line and the kind of secret only.
- Exit codes: `0` = no `fail`/`error` checks, `1` = at least one `fail` or `error`, `2` = tool error (no target, unreadable target, history write failure).
- stdout carries the JSON report only; human text goes to stderr.
- Grade thresholds live only in `censor/grade.py`: A+ ≥ 95, A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F < 60. A failed blocker caps the grade at C.
- Dimension weights (spec §3): correctness 20, guards 15, security 15, operability 5, agent_readiness 5, reliability 15, freshness 10, data_health 15. Only the first five have checks in this plan.
- `warn` earns half of a check's weight. `na` and `error` leave the denominator.
- Run `uv run ruff format packages/censor && uv run ruff check packages/censor` before each commit. ruff enforces E501 at 100 columns and the formatter does not split string literals: break long messages into adjacent string literals.
- Commit messages: `<type>: <description>` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

```
packages/censor/
  pyproject.toml, README.md, LICENSE
  src/censor/
    __init__.py, py.typed
    model.py        Status, Dimension, DIMENSION_WEIGHTS, CheckResult, DimensionScore, AuditReport
    grade.py        GRADE_THRESHOLDS, BLOCKER_CAP, score_to_grade, cap_grade
    scoring.py      WARN_CREDIT, dimension_score, Scored, score_checks
    context.py      SpecFile, ProjectContext, load_context
    runner.py       CheckFn, CheckGroup, run_checks
    checks/__init__.py      QUICK_CHECKS
    checks/correctness.py   manifest, pipeline load, transform compile
    checks/guards.py        validate, on_violation, key rules, schema_drift, contract
    checks/security.py      literal secrets in spec text
    checks/operability.py   README, schedule, lineage
    checks/agent.py         CLAUDE.md, plugin, deny rule, secret hook
    audit.py        new_run_id, audit_project
    history.py      censor_dir, save_report, load_report, list_reports, diff_reports
    report.py       render_markdown
    cli.py          app (audit, report, compare), run_cli
  tests/
    _projects.py    write_project + maturity builders (minimal/functional/robust/production)
    test_model_grade_scoring.py, test_context.py, test_runner.py
    test_correctness.py, test_guards.py, test_security.py, test_operability.py, test_agent.py
    test_audit_maturity.py, test_history.py, test_report.py, test_cli.py
packages/arsenal/src/arsenal/cli.py        + `eval` command
packages/arsenal/tests/test_eval_alias.py
pyproject.toml (root)                     + de-censor member/source, ruff first-party
scripts/gen_schema_docs.py                + schemas/censor-report.json
schemas/censor-report.json                generated
tests/e2e/test_recipes.py                 + csv-cleanup quick audit
```

---

### Task 1: Package skeleton and workspace registration

**Files:**
- Create: `packages/censor/pyproject.toml`, `packages/censor/README.md`, `packages/censor/LICENSE` (copy of `packages/aquila/LICENSE`), `packages/censor/src/censor/__init__.py`, `packages/censor/src/censor/py.typed`, `packages/censor/tests/test_import.py`
- Modify: `pyproject.toml` (root)

**Interfaces:**
- Produces: importable package `censor` with `censor.__version__: str`.

- [ ] **Step 1: Write the failing test** `packages/censor/tests/test_import.py`

```python
from importlib.metadata import version

import censor


def test_version_matches_distribution_metadata() -> None:
    assert censor.__version__ == version("de-censor")
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest packages/censor/tests/test_import.py -v --no-cov`
Expected: FAIL / collection error, `ModuleNotFoundError: No module named 'censor'`.

- [ ] **Step 3: Create the package**

`packages/censor/pyproject.toml`:

```toml
[project]
name = "de-censor"
version = "0.6.16"
description = "Censor — audit a DE Arsenal project or dataset and grade it, with evidence and fixes"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
license-files = ["LICENSE"]
dependencies = [
    "arsenal-core>=0.6,<0.7",
    "de-arsenal>=0.6,<0.7",
    "de-gladius>=0.6,<0.7",
    "pyarrow>=17",
    "pydantic>=2",
    "pyyaml>=6",
    "typer>=0.12",
]
classifiers = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Intended Audience :: Developers",
    "Topic :: Database",
]

[project.urls]
Homepage = "https://github.com/KooEric/de-arsenal"
Repository = "https://github.com/KooEric/de-arsenal"

[project.scripts]
censor = "censor.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/censor"]
```

`packages/censor/src/censor/__init__.py`:

```python
"""Censor — audits a DE Arsenal project and grades it."""

from importlib.metadata import version

__version__ = version("de-censor")
```

`packages/censor/src/censor/py.typed`: empty file.

`packages/censor/README.md`:

````markdown
# Censor (`de-censor`)

> 감찰관. 파이프라인을 조사해 등급을 매기고, 고칠 곳을 근거와 함께 남긴다.

```bash
censor audit .                 # grade the project in the current directory (quick tier)
censor report                  # grade history
censor compare <run_a> <run_b> # checks whose status changed
arsenal eval .                 # same as `censor audit .`
```

Results go to stdout as JSON, a summary to stderr, and history and a Markdown report to
`.arsenal/censor/`. Exit code 0 means no failed check, 1 means at least one, 2 means
Censor could not run. Design: `docs/plans/2026-11-25-censor-design.md`.
````

Copy the license: `cp packages/aquila/LICENSE packages/censor/LICENSE`

Root `pyproject.toml`: add `"de-censor",` to the end of `[project].dependencies`, add
`de-censor = { workspace = true }` to `[tool.uv.sources]`, and add `"censor"` to
`known-first-party` so it reads
`known-first-party = ["arsenal_core", "pugio", "gladius", "augur", "aquila", "pilum", "censor"]`.

- [ ] **Step 4: Sync and run the test**

Run: `uv lock && uv sync && uv run pytest packages/censor/tests/test_import.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor pyproject.toml uv.lock
git commit -m "feat(censor): add de-censor package skeleton

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Result models, grading and scoring

**Files:**
- Create: `packages/censor/src/censor/model.py`, `packages/censor/src/censor/grade.py`, `packages/censor/src/censor/scoring.py`
- Test: `packages/censor/tests/test_model_grade_scoring.py`

**Interfaces:**
- Produces:
  - `Status` (StrEnum: `PASS="pass"`, `WARN="warn"`, `FAIL="fail"`, `NA="na"`, `ERROR="error"`)
  - `Dimension` (StrEnum: `correctness`, `guards`, `security`, `operability`, `agent_readiness`, `reliability`, `freshness`, `data_health`)
  - `DIMENSION_WEIGHTS: dict[Dimension, int]`
  - `CheckResult(id: str, dimension: Dimension, weight: int, status: Status, blocker: bool = False, subject: str, evidence: str, fix: str = "")`
  - `DimensionScore(dimension: Dimension, weight: int, score: float | None)`
  - `AuditReport(schema_version: Literal[1], run_id, tier: Literal["quick"], target, censor_version, score: float | None, grade: str | None, capped_by_blocker: bool, dimensions: list[DimensionScore], checks: list[CheckResult])` with property `has_failures: bool`
  - `score_to_grade(score: float) -> str`, `cap_grade(grade: str, cap: str) -> str`, `BLOCKER_CAP = "C"`
  - `dimension_score(checks: Sequence[CheckResult], dimension: Dimension) -> float | None`
  - `Scored(dimensions: list[DimensionScore], score: float | None, grade: str | None, capped_by_blocker: bool)` and `score_checks(checks: Sequence[CheckResult]) -> Scored`

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_model_grade_scoring.py`

```python
import pytest
from pydantic import ValidationError

from censor.grade import BLOCKER_CAP, cap_grade, score_to_grade
from censor.model import CheckResult, Dimension, Status
from censor.scoring import dimension_score, score_checks


def _check(
    status: Status,
    *,
    dimension: Dimension = Dimension.GUARDS,
    weight: int = 2,
    blocker: bool = False,
    check_id: str = "x",
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=dimension,
        weight=weight,
        status=status,
        blocker=blocker,
        subject="collect.yaml",
        evidence="e",
    )


@pytest.mark.parametrize(
    ("score", "grade"),
    [(100, "A+"), (95, "A+"), (94.9, "A"), (90, "A"), (80, "B"), (70, "C"), (60, "D"), (59.9, "F")],
)
def test_score_to_grade_boundaries(score: float, grade: str) -> None:
    assert score_to_grade(score) == grade


def test_cap_grade_only_lowers() -> None:
    assert cap_grade("A+", BLOCKER_CAP) == "C"
    assert cap_grade("D", BLOCKER_CAP) == "D"


def test_check_result_is_frozen_and_rejects_zero_weight() -> None:
    check = _check(Status.PASS)
    with pytest.raises(ValidationError):
        CheckResult.model_validate({**check.model_dump(), "weight": 0})
    with pytest.raises(ValidationError):
        check.status = Status.FAIL  # type: ignore[misc]


def test_warn_earns_half_and_na_error_leave_denominator() -> None:
    checks = [
        _check(Status.PASS, weight=2),
        _check(Status.WARN, weight=2),
        _check(Status.NA, weight=5),
        _check(Status.ERROR, weight=5),
    ]
    assert dimension_score(checks, Dimension.GUARDS) == pytest.approx(7.5)


def test_dimension_without_applicable_checks_is_none() -> None:
    assert dimension_score([_check(Status.NA)], Dimension.GUARDS) is None
    assert dimension_score([], Dimension.SECURITY) is None


def test_overall_score_weights_dimensions_and_skips_empty_ones() -> None:
    checks = [
        _check(Status.PASS, dimension=Dimension.CORRECTNESS),  # 10 x 20
        _check(Status.FAIL, dimension=Dimension.GUARDS),  # 0 x 15
    ]
    scored = score_checks(checks)
    assert scored.score == pytest.approx(100 * 20 / 35)
    assert scored.grade == "F"
    assert {d.dimension for d in scored.dimensions if d.score is None} >= {Dimension.FRESHNESS}


def test_failed_blocker_caps_grade_at_c() -> None:
    checks = [
        _check(Status.PASS, dimension=Dimension.CORRECTNESS, weight=50),
        _check(Status.FAIL, dimension=Dimension.SECURITY, weight=1, blocker=True),
        _check(Status.PASS, dimension=Dimension.SECURITY, weight=50),
    ]
    scored = score_checks(checks)
    assert scored.score is not None and scored.score > 95
    assert scored.grade == "C"
    assert scored.capped_by_blocker is True


def test_no_applicable_checks_gives_no_grade() -> None:
    scored = score_checks([_check(Status.NA)])
    assert scored.score is None
    assert scored.grade is None
```

- [ ] **Step 2: Run and see it fail**

Run: `uv run pytest packages/censor/tests/test_model_grade_scoring.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.grade'`.

- [ ] **Step 3: Implement**

`packages/censor/src/censor/model.py`:

```python
"""Frozen result models. `AuditReport` is the stdout JSON contract (schemas/censor-report.json)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NA = "na"
    ERROR = "error"


class Dimension(StrEnum):
    CORRECTNESS = "correctness"
    GUARDS = "guards"
    SECURITY = "security"
    OPERABILITY = "operability"
    AGENT_READINESS = "agent_readiness"
    RELIABILITY = "reliability"
    FRESHNESS = "freshness"
    DATA_HEALTH = "data_health"


DIMENSION_WEIGHTS: dict[Dimension, int] = {
    Dimension.CORRECTNESS: 20,
    Dimension.GUARDS: 15,
    Dimension.SECURITY: 15,
    Dimension.OPERABILITY: 5,
    Dimension.AGENT_READINESS: 5,
    Dimension.RELIABILITY: 15,
    Dimension.FRESHNESS: 10,
    Dimension.DATA_HEALTH: 15,
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckResult(_Frozen):
    id: str = Field(description="Stable check id, e.g. `guards.validate:collect.yaml`")
    dimension: Dimension
    weight: int = Field(ge=1)
    status: Status
    blocker: bool = Field(default=False, description="A failed blocker caps the grade at C")
    subject: str = Field(description="File, table or directory the check looked at")
    evidence: str = Field(description="What was found; never contains a secret value")
    fix: str = Field(default="", description="One line on how to fix it")


class DimensionScore(_Frozen):
    dimension: Dimension
    weight: int
    score: float | None = Field(description="0-10, or null when no check applied")


class AuditReport(_Frozen):
    schema_version: Literal[1] = 1
    run_id: str
    tier: Literal["quick"]
    target: str
    censor_version: str
    score: float | None = Field(description="0-100, or null when nothing applied")
    grade: str | None
    capped_by_blocker: bool
    dimensions: list[DimensionScore]
    checks: list[CheckResult]

    @property
    def has_failures(self) -> bool:
        return any(c.status in (Status.FAIL, Status.ERROR) for c in self.checks)
```

`packages/censor/src/censor/grade.py`:

```python
"""The only definition of grade thresholds. Change them here and nowhere else."""

from __future__ import annotations

from typing import Final

GRADE_THRESHOLDS: Final[tuple[tuple[str, float], ...]] = (
    ("A+", 95.0),
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
)
LOWEST_GRADE: Final = "F"
BLOCKER_CAP: Final = "C"
_ORDER: Final = tuple(grade for grade, _ in GRADE_THRESHOLDS) + (LOWEST_GRADE,)


def score_to_grade(score: float) -> str:
    for grade, floor in GRADE_THRESHOLDS:
        if score >= floor:
            return grade
    return LOWEST_GRADE


def cap_grade(grade: str, cap: str) -> str:
    """Return the worse of `grade` and `cap`."""
    return grade if _ORDER.index(grade) >= _ORDER.index(cap) else cap
```

`packages/censor/src/censor/scoring.py`:

```python
"""Checks -> dimension scores -> overall score and grade. Deterministic."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from censor.grade import BLOCKER_CAP, cap_grade, score_to_grade
from censor.model import DIMENSION_WEIGHTS, CheckResult, Dimension, DimensionScore, Status

WARN_CREDIT: Final = 0.5
_SCORED: Final = (Status.PASS, Status.WARN, Status.FAIL)


@dataclass(frozen=True)
class Scored:
    dimensions: list[DimensionScore]
    score: float | None
    grade: str | None
    capped_by_blocker: bool


def dimension_score(checks: Sequence[CheckResult], dimension: Dimension) -> float | None:
    applicable = [c for c in checks if c.dimension == dimension and c.status in _SCORED]
    total = sum(c.weight for c in applicable)
    if total == 0:
        return None
    earned = sum(
        c.weight * (1.0 if c.status == Status.PASS else WARN_CREDIT)
        for c in applicable
        if c.status != Status.FAIL
    )
    return 10.0 * earned / total


def score_checks(checks: Sequence[CheckResult]) -> Scored:
    dimensions = [
        DimensionScore(dimension=d, weight=w, score=dimension_score(checks, d))
        for d, w in DIMENSION_WEIGHTS.items()
    ]
    applied = [d for d in dimensions if d.score is not None]
    weight = sum(d.weight for d in applied)
    if weight == 0:
        return Scored(dimensions, None, None, False)
    score = 10.0 * sum(d.weight * (d.score or 0.0) for d in applied) / weight
    grade = score_to_grade(score)
    blocked = any(c.blocker and c.status == Status.FAIL for c in checks)
    capped = cap_grade(grade, BLOCKER_CAP) if blocked else grade
    return Scored(dimensions, score, capped, blocked and capped != grade)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_model_grade_scoring.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add check models, grade thresholds and scoring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Project context and the check runner

**Files:**
- Create: `packages/censor/src/censor/context.py`, `packages/censor/src/censor/runner.py`, `packages/censor/tests/_projects.py`
- Test: `packages/censor/tests/test_context.py`, `packages/censor/tests/test_runner.py`

**Interfaces:**
- Consumes: `CheckResult`, `Dimension`, `Status` (Task 2); `arsenal.project.load_project`, `ArsenalProject`, `DbtProjectRef`.
- Produces:
  - `SpecFile(path: Path, root: Path, text: str, raw: dict[str, Any] | None)` — raw YAML text, no `${VAR}` substitution; `raw` is `None` when unreadable or not a mapping. Property `name: str` = path relative to the project root when possible.
  - `ProjectContext(root: Path, manifest: ArsenalProject | None, manifest_error: str | None, pipelines: tuple[SpecFile, ...], transforms: tuple[Path | DbtProjectRef, ...], transform_files: tuple[SpecFile, ...])`
  - `load_context(root: Path) -> ProjectContext` — raises `FatalError` when `root/arsenal.yaml` does not exist; a manifest that exists but is invalid gives `manifest=None` and `manifest_error=<message>`.
  - `CheckFn = Callable[[ProjectContext], list[CheckResult]]`, `CheckGroup(name: str, dimension: Dimension, fn: CheckFn)`, `run_checks(ctx, groups: Sequence[CheckGroup]) -> list[CheckResult]`
  - test helper `write_project(root: Path, files: dict[str, str]) -> Path`

- [ ] **Step 1: Write the test helper** `packages/censor/tests/_projects.py`

```python
"""Write small DE Arsenal projects into tmp dirs. Maturity builders live here (Task 9)."""

from __future__ import annotations

from pathlib import Path

MANIFEST = """\
name: demo
pipelines:
  - collect.yaml
transforms:
  - transform.yaml
"""

COLLECT_BARE = """\
name: demo-collect
source:
  type: file
  path: ./input/*.csv
  format: csv
sink:
  type: parquet
  path: ./data/raw
"""

TRANSFORM = """\
name: clean-rows
input: ./data/raw
steps:
  - dedup: [id]
output: ./data/clean
"""


def write_project(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def bare_project(root: Path) -> Path:
    return write_project(
        root,
        {"arsenal.yaml": MANIFEST, "collect.yaml": COLLECT_BARE, "transform.yaml": TRANSFORM},
    )
```

- [ ] **Step 2: Write the failing tests**

`packages/censor/tests/test_context.py`:

```python
from pathlib import Path

import pytest
from _projects import MANIFEST, bare_project, write_project

from arsenal_core.errors import FatalError
from censor.context import load_context


def test_load_context_reads_raw_text_without_env_substitution(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST,
            "collect.yaml": "name: x\nsource:\n  url: ${UNSET_VAR}\n",
            "transform.yaml": "name: t\n",
        },
    )
    ctx = load_context(tmp_path)
    assert ctx.manifest is not None
    assert ctx.pipelines[0].text.count("${UNSET_VAR}") == 1
    assert ctx.pipelines[0].raw == {"name": "x", "source": {"url": "${UNSET_VAR}"}}
    assert ctx.pipelines[0].name == "collect.yaml"
    assert ctx.transform_files[0].name == "transform.yaml"


def test_missing_manifest_is_a_fatal_error(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="arsenal.yaml"):
        load_context(tmp_path)


def test_invalid_manifest_is_recorded_not_raised(tmp_path: Path) -> None:
    write_project(tmp_path, {"arsenal.yaml": "pipelines: [a.yaml]\n"})
    ctx = load_context(tmp_path)
    assert ctx.manifest is None
    assert ctx.manifest_error is not None and "name" in ctx.manifest_error
    assert ctx.pipelines == ()


def test_unreadable_or_non_mapping_spec_has_no_raw(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    (tmp_path / "transform.yaml").unlink()
    ctx = load_context(tmp_path)
    assert ctx.pipelines[0].raw is None
    assert ctx.transform_files[0].raw is None
    assert ctx.transform_files[0].text == ""
```

`packages/censor/tests/test_runner.py`:

```python
from pathlib import Path

from _projects import bare_project

from censor.context import ProjectContext, load_context
from censor.model import CheckResult, Dimension, Status
from censor.runner import CheckGroup, run_checks


def _ok(ctx: ProjectContext) -> list[CheckResult]:
    return [
        CheckResult(
            id="ok", dimension=Dimension.GUARDS, weight=1, status=Status.PASS,
            subject=str(ctx.root), evidence="fine",
        )
    ]


def _boom(ctx: ProjectContext) -> list[CheckResult]:
    raise RuntimeError("exploded")


def test_a_crashing_group_becomes_one_error_check_and_others_still_run(tmp_path: Path) -> None:
    ctx = load_context(bare_project(tmp_path))
    results = run_checks(
        ctx,
        [CheckGroup("boom", Dimension.SECURITY, _boom), CheckGroup("ok", Dimension.GUARDS, _ok)],
    )
    assert [r.id for r in results] == ["boom.crashed", "ok"]
    crashed = results[0]
    assert crashed.status == Status.ERROR
    assert crashed.dimension == Dimension.SECURITY
    assert "RuntimeError: exploded" in crashed.evidence
```

- [ ] **Step 3: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_context.py packages/censor/tests/test_runner.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.context'`.

Note: `_projects` is imported as a top-level module because pytest's importlib mode puts the test file's directory on `sys.path` only if a `conftest.py` exists there. Create `packages/censor/tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

- [ ] **Step 4: Implement**

`packages/censor/src/censor/context.py`:

```python
"""Load a DE Arsenal project for auditing without running or changing anything."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from arsenal.project import ArsenalProject, DbtProjectRef, load_project
from arsenal_core.errors import ArsenalError, FatalError

MANIFEST_NAME = "arsenal.yaml"


@dataclass(frozen=True)
class SpecFile:
    path: Path
    root: Path
    text: str
    raw: dict[str, Any] | None

    @property
    def name(self) -> str:
        try:
            return str(self.path.relative_to(self.root))
        except ValueError:
            return str(self.path)


@dataclass(frozen=True)
class ProjectContext:
    root: Path
    manifest: ArsenalProject | None
    manifest_error: str | None
    pipelines: tuple[SpecFile, ...]
    transforms: tuple[Path | DbtProjectRef, ...]
    transform_files: tuple[SpecFile, ...]


def _read_spec(path: Path, root: Path) -> SpecFile:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return SpecFile(path, root, "", None)
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError:
        return SpecFile(path, root, text, None)
    raw = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None
    return SpecFile(path, root, text, raw)


def load_context(root: Path) -> ProjectContext:
    root = root.absolute()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FatalError(
            f"no {MANIFEST_NAME} in {root}; pass a DE Arsenal project directory"
        )
    try:
        manifest = load_project(manifest_path)
    except ArsenalError as e:
        return ProjectContext(root, None, str(e), (), (), ())
    pipelines = tuple(_read_spec(p, root) for p in manifest.pipelines)
    transforms = tuple(manifest.transforms)
    transform_files = tuple(_read_spec(t, root) for t in transforms if isinstance(t, Path))
    return ProjectContext(root, manifest, None, pipelines, transforms, transform_files)
```

`packages/censor/src/censor/runner.py`:

```python
"""Run check groups; a crashing group becomes one `error` check instead of aborting."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

CheckFn = Callable[[ProjectContext], list[CheckResult]]


@dataclass(frozen=True)
class CheckGroup:
    name: str
    dimension: Dimension
    fn: CheckFn


def run_checks(ctx: ProjectContext, groups: Sequence[CheckGroup]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for group in groups:
        try:
            results.extend(group.fn(ctx))
        except Exception as error:  # recorded as an `error` check, never swallowed
            results.append(
                CheckResult(
                    id=f"{group.name}.crashed",
                    dimension=group.dimension,
                    weight=1,
                    status=Status.ERROR,
                    subject=str(ctx.root),
                    evidence=f"{type(error).__name__}: {error}",
                    fix="report this as a Censor bug with the evidence above",
                )
            )
    return results
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/censor/tests/test_context.py packages/censor/tests/test_runner.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): load project context and run check groups safely

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Correctness checks

**Files:**
- Create: `packages/censor/src/censor/checks/__init__.py` (empty for now, filled in Task 9), `packages/censor/src/censor/checks/correctness.py`
- Test: `packages/censor/tests/test_correctness.py`

**Interfaces:**
- Consumes: `ProjectContext`, `SpecFile` (Task 3); `arsenal_core.spec.load_pipeline(path: Path) -> PipelineSpec`; `gladius.loader.load_transform(path: Path) -> TransformSpec`; `gladius.compile.compile_sql(spec) -> str`.
- Produces: `check_correctness(ctx: ProjectContext) -> list[CheckResult]`. Ids: `correctness.manifest`, `correctness.pipeline:<name>`, `correctness.transform:<name>`. All are blockers except the env-not-set warning and dbt `na`.

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_correctness.py`

```python
from pathlib import Path

import pytest
from _projects import MANIFEST, TRANSFORM, bare_project, write_project

from censor.checks.correctness import check_correctness
from censor.context import load_context
from censor.model import Status


def _by_id(results):  # type: ignore[no-untyped-def]
    return {r.id: r for r in results}


def test_valid_project_passes_every_correctness_check(tmp_path: Path) -> None:
    results = _by_id(check_correctness(load_context(bare_project(tmp_path))))
    assert results["correctness.manifest"].status == Status.PASS
    assert results["correctness.pipeline:collect.yaml"].status == Status.PASS
    assert results["correctness.transform:transform.yaml"].status == Status.PASS


def test_invalid_manifest_fails_as_blocker_and_stops(tmp_path: Path) -> None:
    write_project(tmp_path, {"arsenal.yaml": "pipelines: [a.yaml]\n"})
    results = check_correctness(load_context(tmp_path))
    assert [r.id for r in results] == ["correctness.manifest"]
    assert results[0].status == Status.FAIL and results[0].blocker


def test_misspelled_pipeline_field_fails_as_blocker(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(
        "name: x\nsource:\n  type: file\n  path: a.csv\n  format: csv\n"
        "sink:\n  type: parquett\n  path: out\n",
        encoding="utf-8",
    )
    result = _by_id(check_correctness(load_context(tmp_path)))["correctness.pipeline:collect.yaml"]
    assert result.status == Status.FAIL
    assert result.blocker
    assert "sink" in result.evidence


def test_unset_env_var_is_a_warning_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CENSOR_TEST_DSN", raising=False)
    write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST,
            "collect.yaml": (
                "name: x\nsource:\n  type: file\n  path: ${CENSOR_TEST_DSN}\n  format: csv\n"
                "sink:\n  type: parquet\n  path: out\n"
            ),
            "transform.yaml": TRANSFORM,
        },
    )
    result = _by_id(check_correctness(load_context(tmp_path)))["correctness.pipeline:collect.yaml"]
    assert result.status == Status.WARN
    assert not result.blocker
    assert "CENSOR_TEST_DSN" in result.evidence


def test_transform_that_does_not_compile_fails(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "transform.yaml").write_text(
        "name: t\ninput: ./data/raw\nsteps:\n  - dedupe: [id]\noutput: ./clean\n",
        encoding="utf-8",
    )
    result = _by_id(check_correctness(load_context(tmp_path)))[
        "correctness.transform:transform.yaml"
    ]
    assert result.status == Status.FAIL and result.blocker


def test_dbt_transform_is_not_applicable(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {"arsenal.yaml": "name: d\ntransforms:\n  - dbt: ./dbt\n"},
    )
    results = check_correctness(load_context(tmp_path))
    dbt = [r for r in results if r.id.startswith("correctness.transform:")][0]
    assert dbt.status == Status.NA
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_correctness.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.checks'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/checks/correctness.py` (and an empty `checks/__init__.py`)

```python
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
                check_id, 2, Status.WARN, name,
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
                "correctness.manifest", 3, Status.FAIL, "arsenal.yaml",
                ctx.manifest_error or "invalid manifest", "fix arsenal.yaml, then run `arsenal check`",
            )
        ]
    results = [_result("correctness.manifest", 3, Status.PASS, "arsenal.yaml", "manifest is valid")]
    with contextlib.chdir(ctx.root):  # specs use paths relative to the project, like `arsenal check`
        results.extend(_pipeline(ctx, p) for p in ctx.manifest.pipelines)
        results.extend(_transform(ctx, t) for t in ctx.transforms)
    return results
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_correctness.py -v --no-cov`
Expected: all PASS. If `test_misspelled_pipeline_field_fails_as_blocker` shows a different field path in the message, assert on the word that the real message contains (`sink`), not on the full text.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add correctness checks (manifest, pipeline load, transform compile)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Quality guard checks

**Files:**
- Create: `packages/censor/src/censor/checks/guards.py`
- Test: `packages/censor/tests/test_guards.py`

**Interfaces:**
- Consumes: `ProjectContext`, `SpecFile.raw`, `SpecFile.name` (Task 3).
- Produces: `check_guards(ctx: ProjectContext) -> list[CheckResult]`. Per pipeline with a mapping `raw`: `guards.validate:<name>` (w3), `guards.on_violation:<name>` (w2), `guards.key_rules:<name>` (w2), `guards.schema_drift:<name>` (w2), `guards.contract:<name>` (w1). Specs whose `raw` is `None` are skipped (correctness reports them).

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_guards.py`

```python
from pathlib import Path

from _projects import COLLECT_BARE, bare_project

from censor.checks.guards import check_guards
from censor.context import load_context
from censor.model import Status

GUARDED = COLLECT_BARE + """\
validate:
  rules:
    - { field: id, not_null: true, unique: true }
  on_violation: split
schema_drift: block
contract:
  columns:
    - { name: id, type: int64, nullable: false }
"""


def _statuses(tmp_path: Path, collect: str) -> dict[str, Status]:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(collect, encoding="utf-8")
    return {r.id.split(":")[0]: r.status for r in check_guards(load_context(tmp_path))}


def test_bare_pipeline(tmp_path: Path) -> None:
    assert _statuses(tmp_path, COLLECT_BARE) == {
        "guards.validate": Status.FAIL,
        "guards.on_violation": Status.NA,
        "guards.key_rules": Status.NA,
        "guards.schema_drift": Status.WARN,
        "guards.contract": Status.WARN,
    }


def test_fully_guarded_pipeline_passes(tmp_path: Path) -> None:
    assert set(_statuses(tmp_path, GUARDED).values()) == {Status.PASS}


def test_omitted_on_violation_warns_about_quarantine_default(tmp_path: Path) -> None:
    collect = COLLECT_BARE + "validate:\n  rules:\n    - { field: amount, min: 0 }\n"
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(collect, encoding="utf-8")
    results = {r.id.split(":")[0]: r for r in check_guards(load_context(tmp_path))}
    assert results["guards.on_violation"].status == Status.WARN
    assert "quarantine" in results["guards.on_violation"].evidence
    assert results["guards.key_rules"].status == Status.WARN


def test_unparseable_spec_is_skipped(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text(": : :\n", encoding="utf-8")
    assert check_guards(load_context(tmp_path)) == []
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_guards.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.checks.guards'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/checks/guards.py`

```python
"""Does each pipeline keep bad data out? Reads raw YAML so unset env vars do not matter."""

from __future__ import annotations

from typing import Any, Final, cast

from censor.context import ProjectContext, SpecFile
from censor.model import CheckResult, Dimension, Status

_DIM: Final = Dimension.GUARDS


def _r(kind: str, spec: SpecFile, weight: int, status: Status, evidence: str, fix: str) -> CheckResult:
    return CheckResult(
        id=f"guards.{kind}:{spec.name}",
        dimension=_DIM,
        weight=weight,
        status=status,
        subject=spec.name,
        evidence=evidence,
        fix=fix if status != Status.PASS else "",
    )


def _rules(validate: dict[str, Any]) -> list[dict[str, Any]]:
    rules: object = validate.get("rules", [])
    if not isinstance(rules, list):
        return []
    return [cast(dict[str, Any], r) for r in cast(list[object], rules) if isinstance(r, dict)]


def _pipeline_guards(spec: SpecFile, raw: dict[str, Any]) -> list[CheckResult]:
    validate_raw: object = raw.get("validate")
    validate = cast(dict[str, Any], validate_raw) if isinstance(validate_raw, dict) else None
    results = [
        _r("validate", spec, 3, Status.PASS if validate else Status.FAIL,
           "validate block present" if validate else "no validate block: bad values load unchecked",
           "add `validate.rules` from what the first clean run showed (trust skill)"),
    ]
    if validate is None:
        results.append(_r("on_violation", spec, 2, Status.NA, "no validate block", ""))
        results.append(_r("key_rules", spec, 2, Status.NA, "no validate block", ""))
    else:
        explicit = "on_violation" in validate
        results.append(
            _r("on_violation", spec, 2, Status.PASS if explicit else Status.WARN,
               f"on_violation: {validate['on_violation']}" if explicit
               else "on_violation omitted: defaults to quarantine, which sets whole files aside",
               "write `on_violation: split` unless one bad row must reject the file")
        )
        keyed = any(r.get("not_null") or r.get("unique") for r in _rules(validate))
        results.append(
            _r("key_rules", spec, 2, Status.PASS if keyed else Status.WARN,
               "a rule checks not_null or unique" if keyed else "no not_null/unique rule on any field",
               "add `{ field: <key>, not_null: true, unique: true }` for the row key")
        )
    drift = raw.get("schema_drift", "allow")
    results.append(
        _r("schema_drift", spec, 2, Status.PASS if drift in ("warn", "block") else Status.WARN,
           f"schema_drift: {drift}",
           "set `schema_drift: block` (or `warn`) so changed source columns are noticed")
    )
    has_contract = isinstance(raw.get("contract"), dict)
    results.append(
        _r("contract", spec, 1, Status.PASS if has_contract else Status.WARN,
           "contract declared" if has_contract else "no contract on the output shape",
           "declare `contract.columns` when someone downstream depends on exact columns")
    )
    return results


def check_guards(ctx: ProjectContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in ctx.pipelines:
        if spec.raw is not None:
            results.extend(_pipeline_guards(spec, spec.raw))
    return results
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_guards.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add quality guard checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Security checks (literal secrets in spec text)

**Files:**
- Create: `packages/censor/src/censor/checks/security.py`
- Test: `packages/censor/tests/test_security.py`

**Interfaces:**
- Consumes: `ProjectContext.pipelines`, `ProjectContext.transform_files`, `SpecFile.text`, `SpecFile.name`.
- Produces: `find_secrets(text: str) -> list[tuple[int, str]]` (1-based line, kind) and `check_security(ctx) -> list[CheckResult]`, one `security.secrets:<name>` per spec file (w5, blocker on fail). Evidence lists `line N: <kind>` only, never the value.

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_security.py`

```python
from pathlib import Path

import pytest
from _projects import bare_project

from censor.checks.security import check_security, find_secrets
from censor.context import load_context
from censor.model import Status

SECRET_TOKEN = "ghp_" + "a" * 36


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("  dsn: postgres://app:hunter2@db:5432/x", "credentials in URL"),
        ("  Authorization: Bearer abcdef123456", "literal authorization"),
        ("  api_key: 'abc123'", "literal api_key"),
        (f"  note: {SECRET_TOKEN}", "GitHub token"),
        ("  key: AKIAABCDEFGHIJKLMNOP", "AWS access key"),
    ],
)
def test_detects_literal_secrets(line: str, kind: str) -> None:
    assert find_secrets(f"name: x\n{line}\n") == [(2, kind)]


@pytest.mark.parametrize(
    "line",
    [
        "  dsn_env: PG_DSN",
        "  token_env: GITHUB_TOKEN",
        "  token_url: https://auth.example.com/token",
        "  Authorization: Bearer ${GITHUB_TOKEN}",
        "  url: postgres://app:${PG_PASSWORD}@db/x",
        "  # password: hunter2",
        "  password: ''",
    ],
)
def test_ignores_env_references_comments_and_empty_values(line: str) -> None:
    assert find_secrets(f"{line}\n") == []


def test_check_security_fails_as_blocker_without_leaking_the_value(tmp_path: Path) -> None:
    bare_project(tmp_path)
    collect = tmp_path / "collect.yaml"
    collect.write_text(collect.read_text() + f"# {SECRET_TOKEN}\nx: {SECRET_TOKEN}\n")
    results = {r.subject: r for r in check_security(load_context(tmp_path))}
    bad = results["collect.yaml"]
    assert bad.status == Status.FAIL and bad.blocker
    assert SECRET_TOKEN not in bad.evidence
    assert "GitHub token" in bad.evidence
    assert results["transform.yaml"].status == Status.PASS
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_security.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.checks.security'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/checks/security.py`

```python
"""Secrets belong in environment variables (`*_env`, `${VAR}`), never in spec text."""

from __future__ import annotations

import re
from typing import Final

from censor.context import ProjectContext, SpecFile
from censor.model import CheckResult, Dimension, Status

_ENV_REF: Final = "${"
_TOKEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)
_URL_CREDENTIALS: Final = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
_SECRET_KEY: Final = re.compile(
    r"^\s*[\"']?(authorization|x-api-key|api[_-]?key|token|password|secret)[\"']?\s*:\s*(.*)$",
    re.IGNORECASE,
)


def _literal_value(value: str) -> bool:
    value = value.split(" #", 1)[0].strip().strip("\"'").strip()
    return bool(value) and _ENV_REF not in value


def _line_secret(line: str) -> str | None:
    if line.lstrip().startswith("#"):
        return None
    for kind, pattern in _TOKEN_PATTERNS:
        if pattern.search(line):
            return kind
    match = _URL_CREDENTIALS.search(line)
    if match and _ENV_REF not in match.group(0):
        return "credentials in URL"
    key = _SECRET_KEY.match(line)
    if key and _literal_value(key.group(2)):
        return f"literal {key.group(1).lower()}"
    return None


def find_secrets(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        kind = _line_secret(line)
        if kind:
            found.append((number, kind))
    return found


def _check_file(spec: SpecFile) -> CheckResult:
    found = find_secrets(spec.text)
    evidence = (
        "; ".join(f"line {n}: {kind}" for n, kind in found) if found else "no literal secrets"
    )
    return CheckResult(
        id=f"security.secrets:{spec.name}",
        dimension=Dimension.SECURITY,
        weight=5,
        status=Status.FAIL if found else Status.PASS,
        blocker=bool(found),
        subject=spec.name,
        evidence=evidence,
        fix="move the value to an environment variable (`dsn_env`, `token_env`, `${VAR}`) and rotate it"
        if found
        else "",
    )


def check_security(ctx: ProjectContext) -> list[CheckResult]:
    return [_check_file(s) for s in (*ctx.pipelines, *ctx.transform_files)]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_security.py -v --no-cov`
Expected: all PASS. (`  # password: hunter2` is skipped as a comment; `password: ''` has an empty value.)

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): flag literal secrets in spec files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Operability checks

**Files:**
- Create: `packages/censor/src/censor/checks/operability.py`
- Test: `packages/censor/tests/test_operability.py`

**Interfaces:**
- Consumes: `ProjectContext.root`.
- Produces: `check_operability(ctx) -> list[CheckResult]` with ids `operability.readme` (w1), `operability.schedule` (w2), `operability.lineage` (w1; PASS on project evidence only: `.arsenal/aquila.db`, or `de-aquila` / `de-arsenal[catalog]` in a root `pyproject.toml` or `requirements*.txt`. Final-review fix 4 replaced the original `module_available` seam, which graded the auditor's environment); constant `SCHEDULE_GLOBS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_operability.py`

```python
from pathlib import Path

import pytest
from _projects import bare_project, write_project

from censor.checks import operability
from censor.context import load_context
from censor.model import Status


def _statuses(root: Path) -> dict[str, Status]:
    return {r.id: r.status for r in operability.check_operability(load_context(root))}


def test_bare_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operability, "module_available", lambda name: False)
    assert _statuses(bare_project(tmp_path)) == {
        "operability.readme": Status.FAIL,
        "operability.schedule": Status.WARN,
        "operability.lineage": Status.WARN,
    }


@pytest.mark.parametrize(
    "schedule_file",
    ["dags/daily.py", "airflow/dags/daily.py", ".github/workflows/run.yml"],
)
def test_schedule_that_runs_arsenal_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schedule_file: str
) -> None:
    monkeypatch.setattr(operability, "module_available", lambda name: True)
    bare_project(tmp_path)
    write_project(tmp_path, {"README.md": "# demo\n", schedule_file: "run: arsenal run\n"})
    assert set(_statuses(tmp_path).values()) == {Status.PASS}


def test_schedule_file_that_never_mentions_arsenal_still_warns(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {"dags/other.py": "print('hello')\n"})
    assert _statuses(tmp_path)["operability.schedule"] == Status.WARN
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_operability.py -v --no-cov`
Expected: FAIL, `ImportError: cannot import name 'operability'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/checks/operability.py`

```python
"""Can someone else run, schedule and trace this project?"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Final

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

# Fixed locations only: globbing the whole tree would walk data/ and .venv/.
SCHEDULE_GLOBS: Final = (
    "dags/*.py",
    "airflow/dags/*.py",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "crontab",
)
_SCHEDULE_MARKERS: Final = ("arsenal", "pugio", "gladius")


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _r(check_id: str, weight: int, status: Status, subject: str, evidence: str, fix: str) -> CheckResult:
    return CheckResult(
        id=check_id, dimension=Dimension.OPERABILITY, weight=weight, status=status,
        subject=subject, evidence=evidence, fix=fix if status != Status.PASS else "",
    )


def _schedule_file(root: Path) -> Path | None:
    for pattern in SCHEDULE_GLOBS:
        for path in sorted(root.glob(pattern)):
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                continue
            if any(marker in text for marker in _SCHEDULE_MARKERS):
                return path
    return None


def check_operability(ctx: ProjectContext) -> list[CheckResult]:
    has_readme = (ctx.root / "README.md").is_file()
    schedule = _schedule_file(ctx.root)
    has_lineage = module_available("aquila")
    return [
        _r("operability.readme", 1, Status.PASS if has_readme else Status.FAIL, "README.md",
           "README.md present" if has_readme else "no README.md",
           "add a README saying what the pipeline loads and who uses it"),
        _r("operability.schedule", 2, Status.PASS if schedule else Status.WARN,
           str(schedule.relative_to(ctx.root)) if schedule else "project",
           "runs on a schedule" if schedule else "no schedule found in dags/, airflow/dags/, "
           ".github/workflows/ or crontab",
           "schedule `arsenal run` (see examples/transfer/airflow)"),
        _r("operability.lineage", 1, Status.PASS if has_lineage else Status.WARN, "aquila",
           "aquila is installed" if has_lineage else "aquila is not installed: no lineage",
           "pip install 'de-arsenal[catalog]', then `aquila scan`"),
    ]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_operability.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add operability checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Agent readiness checks (DE harness)

**Files:**
- Create: `packages/censor/src/censor/checks/agent.py`
- Test: `packages/censor/tests/test_agent.py`

**Interfaces:**
- Consumes: `ProjectContext.root`.
- Produces: `check_agent_readiness(ctx) -> list[CheckResult]` with ids `agent.claude_md` (w2), `agent.plugin` (w1), `agent.deny_state` (w1), `agent.secret_hook` (w1). Constants `PLUGIN_PREFIX = "de-arsenal@"`, `REQUIRED_COMMANDS = ("arsenal check", "arsenal run")`.

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_agent.py`

```python
import json
from pathlib import Path

from _projects import bare_project, write_project

from censor.checks.agent import check_agent_readiness
from censor.context import load_context
from censor.model import Status

SETTINGS = {
    "enabledPlugins": {"de-arsenal@de-arsenal": True},
    "permissions": {"deny": ["Bash(rm -rf .arsenal*)"]},
}
HOOK = "#!/bin/bash\n# scan staged *.yaml and *.yml files for secrets\n"
CLAUDE_MD = "Validate with `arsenal check`, then `arsenal run`.\n"


def _statuses(root: Path) -> dict[str, Status]:
    return {r.id: r.status for r in check_agent_readiness(load_context(root))}


def test_project_without_harness(tmp_path: Path) -> None:
    assert _statuses(bare_project(tmp_path)) == {
        "agent.claude_md": Status.FAIL,
        "agent.plugin": Status.WARN,
        "agent.deny_state": Status.WARN,
        "agent.secret_hook": Status.WARN,
    }


def test_full_harness_passes(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(
        tmp_path,
        {
            "CLAUDE.md": CLAUDE_MD,
            ".claude/settings.json": json.dumps(SETTINGS),
            ".claude/hooks/secret-scan.sh": HOOK,
        },
    )
    assert set(_statuses(tmp_path).values()) == {Status.PASS}


def test_claude_md_without_arsenal_commands_warns(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {"CLAUDE.md": "# notes\n"})
    assert _statuses(tmp_path)["agent.claude_md"] == Status.WARN


def test_invalid_settings_json_warns_instead_of_crashing(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {".claude/settings.json": "{not json"})
    results = {r.id: r for r in check_agent_readiness(load_context(tmp_path))}
    assert results["agent.plugin"].status == Status.WARN
    assert "not valid JSON" in results["agent.plugin"].evidence
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_agent.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.checks.agent'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/checks/agent.py`

```python
"""Can a coding agent operate this pipeline safely? DE-specific harness checks only;
generic harness quality is harness-eval's job."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

PLUGIN_PREFIX: Final = "de-arsenal@"
REQUIRED_COMMANDS: Final = ("arsenal check", "arsenal run")
_STATE_DIR: Final = ".arsenal"


def _r(check_id: str, weight: int, status: Status, subject: str, evidence: str, fix: str) -> CheckResult:
    return CheckResult(
        id=check_id, dimension=Dimension.AGENT_READINESS, weight=weight, status=status,
        subject=subject, evidence=evidence, fix=fix if status != Status.PASS else "",
    )


def _settings(root: Path) -> tuple[dict[str, Any], str | None]:
    path = root / ".claude" / "settings.json"
    if not path.is_file():
        return {}, None
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, ".claude/settings.json is not valid JSON"
    return (cast(dict[str, Any], loaded), None) if isinstance(loaded, dict) else ({}, None)


def _claude_md(root: Path) -> CheckResult:
    path = root / "CLAUDE.md"
    fix = "add a CLAUDE.md that says to run `arsenal check` before and `arsenal run` after edits"
    if not path.is_file():
        return _r("agent.claude_md", 2, Status.FAIL, "CLAUDE.md", "no CLAUDE.md", fix)
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [c for c in REQUIRED_COMMANDS if c not in text]
    if missing:
        return _r("agent.claude_md", 2, Status.WARN, "CLAUDE.md",
                  "CLAUDE.md does not mention " + ", ".join(f"`{c}`" for c in missing), fix)
    return _r("agent.claude_md", 2, Status.PASS, "CLAUDE.md", "names arsenal check and run", fix)


def _plugin(settings: dict[str, Any], problem: str | None) -> CheckResult:
    enabled: object = settings.get("enabledPlugins", {})
    names = cast(dict[str, object], enabled).keys() if isinstance(enabled, dict) else []
    ok = any(name.startswith(PLUGIN_PREFIX) for name in names)
    evidence = problem or ("de-arsenal plugin enabled for this project" if ok
                           else "de-arsenal plugin not enabled in .claude/settings.json")
    return _r("agent.plugin", 1, Status.PASS if ok else Status.WARN, ".claude/settings.json",
              evidence, "/plugin install de-arsenal@de-arsenal, then enable it for the project")


def _deny_state(settings: dict[str, Any]) -> CheckResult:
    permissions: object = settings.get("permissions", {})
    deny: object = cast(dict[str, object], permissions).get("deny", []) if isinstance(permissions, dict) else []
    rules = [r for r in cast(list[object], deny) if isinstance(r, str)] if isinstance(deny, list) else []
    ok = any(_STATE_DIR in rule for rule in rules)
    return _r("agent.deny_state", 1, Status.PASS if ok else Status.WARN, ".claude/settings.json",
              "a deny rule protects .arsenal/" if ok else "nothing stops an agent deleting .arsenal/",
              'add "Bash(rm -rf .arsenal*)" to permissions.deny: .arsenal/ holds run state')


def _secret_hook(root: Path) -> CheckResult:
    hooks = root / ".claude" / "hooks"
    found = False
    for path in sorted(hooks.glob("*")) if hooks.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace").lower() if path.is_file() else ""
        if "secret" in text and ("yaml" in text or "yml" in text):
            found = True
            break
    return _r("agent.secret_hook", 1, Status.PASS if found else Status.WARN, ".claude/hooks",
              "a secret-scan hook covers YAML" if found else "no secret-scan hook that covers YAML specs",
              "add a PreToolUse hook that scans *.yaml for secrets before commit")


def check_agent_readiness(ctx: ProjectContext) -> list[CheckResult]:
    settings, problem = _settings(ctx.root)
    return [_claude_md(ctx.root), _plugin(settings, problem), _deny_state(settings), _secret_hook(ctx.root)]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_agent.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add DE agent-readiness checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Audit pipeline and maturity fixtures

**Files:**
- Modify: `packages/censor/src/censor/checks/__init__.py`, `packages/censor/tests/_projects.py`
- Create: `packages/censor/src/censor/audit.py`
- Test: `packages/censor/tests/test_audit_maturity.py`

**Interfaces:**
- Consumes: all check functions (Tasks 4-8), `run_checks`, `CheckGroup` (Task 3), `score_checks` (Task 2).
- Produces: `QUICK_CHECKS: tuple[CheckGroup, ...]`; `new_run_id(tier: str, now: datetime | None = None) -> str` (format `YYYYMMDD-HHMMSS-<tier>`, UTC); `audit_project(root: Path, *, now: datetime | None = None) -> AuditReport` (tier `quick`). Test builders `minimal_project`, `functional_project`, `robust_project`, `production_project` (each `(root: Path) -> Path`).

- [ ] **Step 1: Add the maturity builders** to `packages/censor/tests/_projects.py` (append)

```python
import json as _json

GUARDED_COLLECT = COLLECT_BARE + """\
validate:
  rules:
    - { field: id, not_null: true, unique: true }
  on_violation: split
"""
ROBUST_COLLECT = GUARDED_COLLECT + """\
schema_drift: block
contract:
  columns:
    - { name: id, type: int64, nullable: false }
"""
CLAUDE_MD = "Run `arsenal check` before and `arsenal run` after edits.\n"
DAG = "DAG('daily', schedule='@daily')\nBashOperator(task_id='run', bash_command='arsenal run')\n"
SETTINGS = _json.dumps(
    {
        "enabledPlugins": {"de-arsenal@de-arsenal": True},
        "permissions": {"deny": ["Bash(rm -rf .arsenal*)"]},
    }
)
SECRET_HOOK = "# scan *.yaml and *.yml for secrets\n"
# lineage evidence: the project declares Aquila (operability.lineage reads the project, not the
# auditor's environment)
PYPROJECT = '[project]\nname = "demo"\ndependencies = ["de-arsenal[catalog]>=0.6,<0.7"]\n'


def minimal_project(root: Path) -> Path:
    return bare_project(root)


def functional_project(root: Path) -> Path:
    bare_project(root)
    return write_project(root, {"collect.yaml": GUARDED_COLLECT, "README.md": "# demo\n"})


def robust_project(root: Path) -> Path:
    functional_project(root)
    return write_project(
        root, {"collect.yaml": ROBUST_COLLECT, "CLAUDE.md": CLAUDE_MD, "dags/daily.py": DAG}
    )


def production_project(root: Path) -> Path:
    robust_project(root)
    return write_project(
        root,
        {
            ".claude/settings.json": SETTINGS,
            ".claude/hooks/secret-scan.sh": SECRET_HOOK,
            "pyproject.toml": PYPROJECT,
        },
    )
```

- [ ] **Step 2: Write the failing tests** `packages/censor/tests/test_audit_maturity.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from _projects import (
    functional_project,
    minimal_project,
    production_project,
    robust_project,
)

from censor.audit import audit_project, new_run_id
from censor.model import Dimension, Status

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)


def test_run_id_format() -> None:
    assert new_run_id("quick", NOW) == "20261125-093000-quick"


def test_grades_rise_with_maturity(tmp_path: Path) -> None:
    builders = [minimal_project, functional_project, robust_project, production_project]
    scores = []
    for i, build in enumerate(builders):
        report = audit_project(build(tmp_path / str(i)), now=NOW)
        assert report.score is not None
        scores.append(report.score)
    assert scores == sorted(scores) and len(set(scores)) == 4
    assert scores[0] < 80
    production = audit_project(tmp_path / "3", now=NOW)
    assert production.grade in ("A+", "A")


def test_quick_audit_has_no_standard_dimension_scores(tmp_path: Path) -> None:
    report = audit_project(production_project(tmp_path), now=NOW)
    by_dim = {d.dimension: d.score for d in report.dimensions}
    assert by_dim[Dimension.FRESHNESS] is None
    assert by_dim[Dimension.CORRECTNESS] == 10.0
    assert report.tier == "quick"
    assert report.run_id == "20261125-093000-quick"


def test_literal_secret_caps_production_project_at_c(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    collect = root / "collect.yaml"
    # a valid field, so correctness still passes and only the security blocker lowers the grade
    collect.write_text(
        collect.read_text().replace("path: ./input/*.csv", "path: s3://u:pw@h/input/*.csv")
    )
    report = audit_project(root, now=NOW)
    assert report.score is not None and report.score >= 80  # B before the cap
    assert report.grade == "C"
    assert report.capped_by_blocker is True
    assert report.has_failures
    assert any(c.status == Status.FAIL and c.blocker for c in report.checks)
```

- [ ] **Step 3: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_audit_maturity.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.audit'`.

- [ ] **Step 4: Implement**

`packages/censor/src/censor/checks/__init__.py`:

```python
"""The Quick tier: checks that read files only, never data or the network."""

from censor.checks.agent import check_agent_readiness
from censor.checks.correctness import check_correctness
from censor.checks.guards import check_guards
from censor.checks.operability import check_operability
from censor.checks.security import check_security
from censor.model import Dimension
from censor.runner import CheckGroup

QUICK_CHECKS: tuple[CheckGroup, ...] = (
    CheckGroup("correctness", Dimension.CORRECTNESS, check_correctness),
    CheckGroup("guards", Dimension.GUARDS, check_guards),
    CheckGroup("security", Dimension.SECURITY, check_security),
    CheckGroup("operability", Dimension.OPERABILITY, check_operability),
    CheckGroup("agent", Dimension.AGENT_READINESS, check_agent_readiness),
)
```

`packages/censor/src/censor/audit.py`:

```python
"""Load -> check -> score. The one entry point the CLI calls."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from censor import __version__
from censor.checks import QUICK_CHECKS
from censor.context import load_context
from censor.model import AuditReport
from censor.runner import run_checks
from censor.scoring import score_checks


def new_run_id(tier: str, now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return f"{moment:%Y%m%d-%H%M%S}-{tier}"


def audit_project(root: Path, *, now: datetime | None = None) -> AuditReport:
    ctx = load_context(root)
    checks = run_checks(ctx, QUICK_CHECKS)
    scored = score_checks(checks)
    return AuditReport(
        run_id=new_run_id("quick", now),
        tier="quick",
        target=str(ctx.root),
        censor_version=__version__,
        score=scored.score,
        grade=scored.grade,
        capped_by_blocker=scored.capped_by_blocker,
        dimensions=scored.dimensions,
        checks=checks,
    )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/censor/tests/test_audit_maturity.py -v --no-cov`
Expected: all PASS. If two adjacent maturity levels tie, the builder for the higher level is missing a step; fix the builder, not the weights.

- [ ] **Step 6: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): assemble quick audit and maturity fixtures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: History and Markdown report

**Files:**
- Create: `packages/censor/src/censor/history.py`, `packages/censor/src/censor/report.py`
- Test: `packages/censor/tests/test_history.py`, `packages/censor/tests/test_report.py`

**Interfaces:**
- Consumes: `AuditReport`, `Status` (Task 2); `audit_project` (Task 9) in tests.
- Produces:
  - `CENSOR_SUBDIR = Path(".arsenal/censor")`; `censor_dir(base: Path) -> Path`
  - `save_report(report: AuditReport, base: Path, *, markdown: str) -> Path` — writes `runs/<run_id>.json`, `runs/<run_id>.parquet`, `reports/<run_id>.md`, creates `.gitignore` containing `*` only if absent; returns the JSON path.
  - `load_report(base: Path, run_id: str) -> AuditReport` (raises `FatalError` if missing)
  - `list_reports(base: Path) -> list[AuditReport]` (sorted by `run_id`)
  - `CheckChange(id: str, before: Status | None, after: Status | None)`; `diff_reports(a: AuditReport, b: AuditReport) -> list[CheckChange]` (sorted by id, only changed)
  - `render_markdown(report: AuditReport, lang: Literal["en", "ko"] = "en") -> str`

- [ ] **Step 1: Write the failing tests**

`packages/censor/tests/test_history.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from _projects import minimal_project, production_project

from arsenal_core.errors import FatalError
from censor.audit import audit_project
from censor.history import censor_dir, diff_reports, list_reports, load_report, save_report
from censor.model import Status

T1 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


def test_save_writes_json_parquet_markdown_and_gitignore(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    report = audit_project(root, now=T1)
    path = save_report(report, root, markdown="# r\n")
    base = censor_dir(root)
    assert path == base / "runs" / f"{report.run_id}.json"
    assert (base / "reports" / f"{report.run_id}.md").read_text() == "# r\n"
    assert (base / ".gitignore").read_text() == "*\n"
    table = pq.read_table(base / "runs" / f"{report.run_id}.parquet")
    assert table.num_rows == len(report.checks)
    assert set(table.column_names) >= {"run_id", "id", "dimension", "status", "weight"}
    assert load_report(root, report.run_id) == report


def test_existing_gitignore_is_left_alone(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    base = censor_dir(root)
    base.mkdir(parents=True)
    (base / ".gitignore").write_text("custom\n")
    save_report(audit_project(root, now=T1), root, markdown="")
    assert (base / ".gitignore").read_text() == "custom\n"


def test_list_and_diff(tmp_path: Path) -> None:
    root = minimal_project(tmp_path / "p")
    first = audit_project(root, now=T1)
    save_report(first, root, markdown="")
    production_project(root)
    second = audit_project(root, now=T2)
    save_report(second, root, markdown="")
    assert [r.run_id for r in list_reports(root)] == [first.run_id, second.run_id]
    changes = {c.id: c for c in diff_reports(first, second)}
    assert changes["guards.validate:collect.yaml"].before == Status.FAIL
    assert changes["guards.validate:collect.yaml"].after == Status.PASS
    assert "correctness.manifest" not in changes


def test_load_missing_run_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no saved run"):
        load_report(tmp_path, "20260101-000000-quick")
```

`packages/censor/tests/test_report.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

from _projects import minimal_project

from censor.audit import audit_project
from censor.report import render_markdown

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def test_markdown_shows_grade_dimensions_and_fixes(tmp_path: Path) -> None:
    report = audit_project(minimal_project(tmp_path), now=NOW)
    text = render_markdown(report)
    assert text.startswith(f"# Censor report — {report.run_id}")
    assert f"**Grade {report.grade}**" in text
    assert "| correctness |" in text
    assert "guards.validate:collect.yaml" in text
    assert "trust skill" in text  # the fix line is shown
    assert "| freshness | n/a |" in text


def test_korean_labels(tmp_path: Path) -> None:
    report = audit_project(minimal_project(tmp_path), now=NOW)
    text = render_markdown(report, "ko")
    assert "등급" in text and "고칠 항목" in text
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_history.py packages/censor/tests/test_report.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.history'`.

- [ ] **Step 3: Implement**

`packages/censor/src/censor/history.py`:

```python
"""Run history under <base>/.arsenal/censor/. Parquet so `gladius query` can aggregate it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.errors import FatalError
from censor.model import AuditReport, Status

CENSOR_SUBDIR: Final = Path(".arsenal") / "censor"


@dataclass(frozen=True)
class CheckChange:
    id: str
    before: Status | None
    after: Status | None


def censor_dir(base: Path) -> Path:
    return base / CENSOR_SUBDIR


def save_report(report: AuditReport, base: Path, *, markdown: str) -> Path:
    root = censor_dir(base)
    runs = root / "runs"
    reports = root / "reports"
    runs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists() and not gitignore.is_symlink():
        gitignore.write_text("*\n", encoding="utf-8")
    json_path = runs / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    rows = [{"run_id": report.run_id, **c.model_dump(mode="json")} for c in report.checks]
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.Table.from_pylist(rows), runs / f"{report.run_id}.parquet", compression="zstd"
    )
    (reports / f"{report.run_id}.md").write_text(markdown, encoding="utf-8")
    return json_path


def load_report(base: Path, run_id: str) -> AuditReport:
    path = censor_dir(base) / "runs" / f"{run_id}.json"
    if not path.is_file():
        raise FatalError(f"no saved run {run_id} in {censor_dir(base)}")
    return AuditReport.model_validate_json(path.read_text(encoding="utf-8"))


def list_reports(base: Path) -> list[AuditReport]:
    runs = censor_dir(base) / "runs"
    paths = sorted(runs.glob("*.json")) if runs.is_dir() else []
    return [AuditReport.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]


def diff_reports(before: AuditReport, after: AuditReport) -> list[CheckChange]:
    a = {c.id: c.status for c in before.checks}
    b = {c.id: c.status for c in after.checks}
    return [
        CheckChange(check_id, a.get(check_id), b.get(check_id))
        for check_id in sorted(a.keys() | b.keys())
        if a.get(check_id) != b.get(check_id)
    ]
```

`packages/censor/src/censor/report.py`:

```python
"""Markdown report: grade -> dimensions -> what to fix."""

from __future__ import annotations

from typing import Final, Literal

from censor.model import AuditReport, Status

Lang = Literal["en", "ko"]
_LABELS: Final[dict[Lang, dict[str, str]]] = {
    "en": {
        "grade": "Grade", "score": "score", "capped": "capped at C by a failed blocker",
        "dimension": "Dimension", "dim_score": "Score (0-10)", "fix_heading": "To fix",
        "nothing": "Nothing to fix.", "none": "no grade: no check applied",
    },
    "ko": {
        "grade": "등급", "score": "점수", "capped": "차단 체크 실패로 C 상한 적용",
        "dimension": "차원", "dim_score": "점수 (0-10)", "fix_heading": "고칠 항목",
        "nothing": "고칠 항목이 없습니다.", "none": "등급 없음: 적용된 체크가 없음",
    },
}
_ORDER: Final = {Status.FAIL: 0, Status.ERROR: 1, Status.WARN: 2}


def render_markdown(report: AuditReport, lang: Lang = "en") -> str:
    t = _LABELS[lang]
    lines = [f"# Censor report — {report.run_id}", "", f"`{report.target}` · tier {report.tier}", ""]
    if report.grade is None or report.score is None:
        lines.append(t["none"])
    else:
        capped = f" ({t['capped']})" if report.capped_by_blocker else ""
        lines.append(f"**{t['grade']} {report.grade}** · {t['score']} {report.score:.1f}{capped}")
    lines += ["", f"| {t['dimension']} | {t['dim_score']} |", "|---|---|"]
    for d in report.dimensions:
        lines.append(f"| {d.dimension.value} | {'n/a' if d.score is None else f'{d.score:.1f}'} |")
    todo = sorted(
        (c for c in report.checks if c.status in _ORDER),
        key=lambda c: (_ORDER[c.status], -c.weight, c.id),
    )
    lines += ["", f"## {t['fix_heading']}", ""]
    if not todo:
        lines.append(t["nothing"])
    for c in todo:
        blocker = " · blocker" if c.blocker else ""
        lines.append(f"- **{c.status.value}**{blocker} `{c.id}` — {c.evidence}")
        if c.fix:
            lines.append(f"  - {c.fix}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_history.py packages/censor/tests/test_report.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): save run history and render markdown reports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Censor CLI (`audit`, `report`, `compare`) and `run_cli`

**Files:**
- Create: `packages/censor/src/censor/cli.py`
- Test: `packages/censor/tests/test_cli.py`

**Interfaces:**
- Consumes: `audit_project` (Task 9); `save_report`, `list_reports`, `load_report`, `diff_reports` (Task 10); `render_markdown`.
- Produces: Typer `app`; `run_cli(argv: Sequence[str]) -> int` (runs `app` without `sys.exit`, returns the exit code; Task 12 uses it).

- [ ] **Step 1: Write the failing tests** `packages/censor/tests/test_cli.py`

```python
import json
from datetime import UTC, datetime
from pathlib import Path

from _projects import minimal_project, production_project
from typer.testing import CliRunner

from censor.audit import audit_project
from censor.cli import app, run_cli
from censor.history import save_report

runner = CliRunner()


def test_audit_prints_json_to_stdout_and_exits_1_on_failures(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root), "--json-only"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["tier"] == "quick"
    assert (root / ".arsenal" / "censor" / "runs" / f"{payload['run_id']}.json").is_file()


def test_clean_project_exits_0_and_writes_summary_to_stderr(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["grade"] in ("A+", "A")
    assert "grade" in result.stderr.lower()


def test_missing_project_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "arsenal.yaml" in result.stderr


def test_history_write_failure_exits_2_but_still_prints_json(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    (root / ".arsenal").write_text("not a directory")
    result = runner.invoke(app, ["audit", str(root)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["run_id"]
    assert "could not save" in result.stderr


def test_report_and_compare(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    first = audit_project(root, now=datetime(2026, 11, 25, 9, 0, tzinfo=UTC))
    save_report(first, root, markdown="")
    production_project(root)
    second = audit_project(root, now=datetime(2026, 11, 25, 10, 0, tzinfo=UTC))
    save_report(second, root, markdown="")
    listing = runner.invoke(app, ["report", str(root)])
    assert listing.exit_code == 0
    assert first.run_id in listing.stdout and second.run_id in listing.stdout
    diff = runner.invoke(app, ["compare", first.run_id, second.run_id, "--project", str(root)])
    assert diff.exit_code == 0
    assert "guards.validate:collect.yaml: fail -> pass" in diff.stdout


def test_compare_unknown_run_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["compare", "a", "b", "--project", str(tmp_path)])
    assert result.exit_code == 2


def test_run_cli_returns_exit_code(tmp_path: Path) -> None:
    assert run_cli(["audit", str(production_project(tmp_path)), "--json-only"]) == 0
    assert run_cli(["audit", str(tmp_path / "missing")]) == 2
    assert run_cli(["no-such-command"]) == 2
```

Note: with the workspace's Typer 0.26, `CliRunner()` keeps stderr separate (`result.stderr`); verified before writing this plan. Do not pass `mix_stderr`.

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/censor/tests/test_cli.py -v --no-cov`
Expected: FAIL, `ModuleNotFoundError: No module named 'censor.cli'`.

- [ ] **Step 3: Implement** `packages/censor/src/censor/cli.py`

```python
"""censor CLI. JSON on stdout, people on stderr. Exit 0 clean, 1 failures, 2 tool error."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from arsenal_core.errors import ArsenalError
from censor.audit import audit_project
from censor.history import diff_reports, list_reports, load_report, save_report
from censor.report import Lang, render_markdown

app = typer.Typer(help="Censor — audit a DE Arsenal project and grade it.", no_args_is_help=True)

EXIT_FAILURES = 1
EXIT_TOOL_ERROR = 2


def _err(message: str) -> None:
    typer.echo(message, err=True)


@app.command()
def audit(
    project: Annotated[Path, typer.Argument(help="Project directory")] = Path("."),
    lang: Annotated[str, typer.Option("--lang", help="Report language: en or ko")] = "en",
    json_only: Annotated[bool, typer.Option("--json-only", help="No summary on stderr")] = False,
) -> None:
    """Grade a project (quick tier: reads files only, runs nothing)."""
    if lang not in ("en", "ko"):
        raise typer.BadParameter("use en or ko", param_hint="--lang")
    report_lang: Lang = "ko" if lang == "ko" else "en"
    try:
        report = audit_project(project)
    except ArsenalError as e:
        _err(f"censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    typer.echo(report.model_dump_json(indent=2))
    if not json_only:
        _err(f"grade {report.grade or '-'} · score "
             f"{'-' if report.score is None else f'{report.score:.1f}'} · run {report.run_id}")
    try:
        save_report(report, project.absolute(), markdown=render_markdown(report, report_lang))
    except OSError as e:
        _err(f"censor: could not save the run under {project}/.arsenal/censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    if report.has_failures:
        raise typer.Exit(EXIT_FAILURES)


@app.command()
def report(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """List saved runs: run id, grade, score."""
    for saved in list_reports(project.absolute()):
        score = "-" if saved.score is None else f"{saved.score:.1f}"
        typer.echo(f"{saved.run_id}\t{saved.grade or '-'}\t{score}")


@app.command()
def compare(
    run_a: str,
    run_b: str,
    project: Annotated[Path, typer.Option("--project")] = Path("."),
) -> None:
    """Show the checks whose status changed between two saved runs."""
    try:
        before = load_report(project.absolute(), run_a)
        after = load_report(project.absolute(), run_b)
    except ArsenalError as e:
        _err(f"censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    for change in diff_reports(before, after):
        old = change.before.value if change.before else "absent"
        new = change.after.value if change.after else "absent"
        typer.echo(f"{change.id}: {old} -> {new}")


def run_cli(argv: Sequence[str]) -> int:
    """Run the CLI in-process and return its exit code (used by `arsenal eval`)."""
    command = typer.main.get_command(app)
    # Standalone mode prints usage errors and always ends in SystemExit. Catching that avoids
    # Typer's vendored Click exception classes, which are not public API (typer 0.26).
    try:
        command.main(args=list(argv), prog_name="censor", standalone_mode=True)
    except SystemExit as e:
        if e.code is None:
            return 0
        return e.code if isinstance(e.code, int) else EXIT_TOOL_ERROR
    return 0
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/censor/tests/test_cli.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/censor
git commit -m "feat(censor): add audit, report and compare commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `arsenal eval` alias

**Files:**
- Modify: `packages/arsenal/src/arsenal/cli.py` (add a command before the `app.add_typer(...)` lines at the end)
- Test: `packages/arsenal/tests/test_eval_alias.py`

**Interfaces:**
- Consumes: `censor.cli.run_cli(argv) -> int` (Task 11).
- Produces: `arsenal eval [ARGS...]` ≡ `censor audit [ARGS...]`; prints `INSTALL_HINT` and exits 2 when `censor` is not importable.

- [ ] **Step 1: Write the failing tests** `packages/arsenal/tests/test_eval_alias.py`

```python
import json
import sys
from pathlib import Path

import pytest
from arsenal.cli import app
from typer.testing import CliRunner

from censor.cli import app as censor_app

runner = CliRunner()

MANIFEST = "name: demo\npipelines:\n  - collect.yaml\n"
COLLECT = (
    "name: demo\nsource:\n  type: file\n  path: ./in/*.csv\n  format: csv\n"
    "sink:\n  type: parquet\n  path: ./out\n"
)


def _project(root: Path) -> Path:
    root.mkdir()
    (root / "arsenal.yaml").write_text(MANIFEST)
    (root / "collect.yaml").write_text(COLLECT)
    return root


def _without_run_id(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    payload.pop("run_id")
    return payload


def test_arsenal_eval_matches_censor_audit(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    via_arsenal = runner.invoke(app, ["eval", str(root), "--json-only"])
    via_censor = runner.invoke(censor_app, ["audit", str(root), "--json-only"])
    assert via_arsenal.exit_code == via_censor.exit_code == 1
    assert _without_run_id(via_arsenal.stdout) == _without_run_id(via_censor.stdout)


def test_arsenal_eval_without_censor_prints_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "censor.cli", None)
    result = runner.invoke(app, ["eval", str(tmp_path)])
    assert result.exit_code == 2
    assert "de-censor" in result.stderr
```

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/arsenal/tests/test_eval_alias.py -v --no-cov`
Expected: FAIL, `No such command 'eval'` (exit code 2 with usage text) in the first test.

- [ ] **Step 3: Implement** — insert into `packages/arsenal/src/arsenal/cli.py` just above `app.add_typer(dataset.app, name="dataset")`:

```python
INSTALL_HINT = (
    "arsenal eval needs Censor: pip install de-censor "
    "(or: uv tool install de-arsenal --with de-censor)"
)


@app.command(
    "eval",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def eval_(ctx: typer.Context) -> None:
    """Grade this project with Censor. Same arguments as `censor audit`."""
    try:
        from censor.cli import run_cli
    except ImportError:
        typer.echo(INSTALL_HINT, err=True)
        raise typer.Exit(2) from None
    code = run_cli(["audit", *ctx.args])
    if code:
        raise typer.Exit(code)
```

Do **not** add `de-censor` to `packages/arsenal/pyproject.toml`: `de-censor` depends on `de-arsenal`, and a reverse dependency would make a cycle.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/arsenal/tests/test_eval_alias.py packages/arsenal/tests/test_cli.py -v --no-cov`
Expected: all PASS (existing arsenal CLI tests still pass).

- [ ] **Step 5: Commit**

```bash
git add packages/arsenal
git commit -m "feat(arsenal): add arsenal eval as an alias for censor audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: JSON schema artifact, e2e recipe audit, full gate

**Files:**
- Modify: `scripts/gen_schema_docs.py`, `tests/e2e/test_recipes.py`
- Create (generated): `schemas/censor-report.json`
- Modify: `docs/plans/2026-11-25-censor-design.md` §2 (record the dependency decision)

**Interfaces:**
- Consumes: `censor.model.AuditReport` (Task 2), `censor.cli.app` (Task 11).

- [ ] **Step 1: Write the failing e2e test** — append to `tests/e2e/test_recipes.py`:

```python
def test_csv_cleanup_recipe_passes_censor_correctness(tmp_path: Path) -> None:
    from censor.cli import app as censor_app

    dest = tmp_path / "csv-proj"
    assert runner.invoke(app, ["init", "csv-cleanup", "--dest", str(dest)]).exit_code == 0
    result = runner.invoke(censor_app, ["audit", str(dest), "--json-only"])
    report = json.loads(result.stdout)
    correctness = [c for c in report["checks"] if c["dimension"] == "correctness"]
    assert correctness and all(c["status"] == "pass" for c in correctness)
    # the recipe ships without a validate block, so guards fail and the exit code says so
    assert result.exit_code == 1
```

Add `import json` to the imports at the top of `tests/e2e/test_recipes.py` if it is not there.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/e2e/test_recipes.py::test_csv_cleanup_recipe_passes_censor_correctness -v --no-cov`
Expected: PASS (Tasks 1-11 already provide everything). If it fails on correctness, the recipe and Censor disagree with `arsenal check`; compare with `uv run arsenal check <dest>` before changing either.

- [ ] **Step 3: Register the schema artifact** in `scripts/gen_schema_docs.py`

Add next to the other path constants:

```python
_CENSOR_REPORT_JSON: Final = Path("schemas/censor-report.json")
```

Add next to the other importers:

```python
def _import_censor_report() -> type[Any]:
    from censor.model import AuditReport

    return AuditReport
```

In `generate_artifacts()`, add `censor_report = _import_censor_report()` beside the other imports and add this entry to the returned dict:

```python
        _CENSOR_REPORT_JSON: _render_json_schema(censor_report),
```

Update the docstring line "Build the full content of all 10 generated artifacts" to say 11.

- [ ] **Step 4: Generate and check**

Run: `uv run python scripts/gen_schema_docs.py && uv run python scripts/gen_schema_docs.py --check`
Expected: `schemas/censor-report.json` is created; `--check` exits 0.
Run: `uv run pytest packages/arsenal-core/tests/test_gen_schema_docs.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Record the dependency decision in the spec.** In `docs/plans/2026-11-25-censor-design.md` §2, replace the two bullets that start with "`de-censor` 의존:" and "`de-arsenal`은 Censor에 직접 의존하지 않는다." with:

```markdown
- `de-censor` 의존: `de-arsenal`(`load_project`), `arsenal-core`, `de-gladius`(compile), `pyarrow`, `pyyaml`, `typer`.
  Plan 2에서 `pugio`(상태·DLQ)와 `duckdb`를 쓴다(둘 다 `de-arsenal`이 이미 가져온다). `de-augur`, `de-aquila`는
  extra(`de-censor[augur]`, `de-censor[catalog]`)로 둔다.
- `de-arsenal`은 `de-censor`에 의존하지 않는다(`[eval]` extra 없음). `de-censor`가 `de-arsenal`에 의존하므로 반대 방향을
  선언하면 순환이 된다. `arsenal eval`은 지연 import하고, 없으면 `pip install de-censor`
  (또는 `uv tool install de-arsenal --with de-censor`)를 안내하고 종료 코드 2.
```

- [ ] **Step 6: Run the full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest`
Expected: ruff clean, pyright 0 errors, all tests pass, coverage ≥ 80%. Fix pyright strict complaints in `packages/censor` with explicit `cast(...)` or narrowed types, not with blanket `# type: ignore`.

- [ ] **Step 7: Commit**

```bash
git add scripts/gen_schema_docs.py schemas/censor-report.json tests/e2e/test_recipes.py docs/plans/2026-11-25-censor-design.md packages
git commit -m "feat(censor): publish report schema and audit the csv-cleanup recipe in e2e

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec coverage (this plan)

| Spec section | Covered by | Deferred to |
|---|---|---|
| §2 components, `arsenal eval` alias, CLI `audit/report/compare` | Tasks 1, 11, 12 | `attach-findings`, `--tier standard` (Plan 2/3) |
| §3 Quick dimensions (correctness, guards, security, operability, agent readiness) | Tasks 4-8 | reliability, freshness, data health (Plan 2); Augur (Plan 3) |
| §4 scoring, grade, blocker cap | Task 2 | — |
| §5 Full flow | — | Plan 3 |
| §6 scale guard | — | Plan 2 |
| §7 output, exit codes, history, report, `.gitignore` | Tasks 10, 11, 13 | — |
| §8 error handling (check errors, missing target, history write failure) | Tasks 3, 11 | Postgres/lease/Augur rows (Plan 2/3) |
| §9 tests: unit, maturity, CLI, alias, e2e, schema | Tasks 2-13 | read-only hash checks, Postgres (Plan 2) |
| §10 release | — | Plan 3 |
