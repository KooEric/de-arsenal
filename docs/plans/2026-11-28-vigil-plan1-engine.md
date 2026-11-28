# Vigil Plan 1: Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working `vigil` CLI (`test`, `run`, `coverage`, `unpack`) that loads detection rules from YAML, tests them against fixtures, runs them over an `events` Parquet lake, and reports coverage gaps.

**Architecture:** A new workspace package `packages/vigil` (PyPI `de-vigil`). `events.py` owns the `events` table and hands out DuckDB connections locked with `enable_external_access=false`. `rule.py` loads and validates rule files. `testing.py`, `run.py`, `coverage.py` and `unpack.py` are pure functions over those; `cli.py` wires Typer.

**Tech Stack:** Python 3.11, DuckDB, PyArrow, Pydantic v2 (frozen models), PyYAML, Typer, pytest + `typer.testing.CliRunner`.

Spec: [2026-11-28-vigil-design.md](2026-11-28-vigil-design.md). Next: [Plan 2](2026-11-28-vigil-plan2-recipe.md) = `cloudtrail-detect` recipe, e2e, docs. The v0.8.0 version bump and PyPI publish are a separate release plan.

## Global Constraints

- Python `>=3.11`; ruff line length 100; pyright `strict` covers `packages/`.
- Root pytest config adds `--cov-fail-under=80` over all packages. While iterating run `uv run pytest packages/vigil -q --no-cov`; the full `uv run pytest` runs once in Task 6.
- All workspace packages share one version. `de-vigil` is `0.7.3` with cross pin `arsenal-core>=0.7,<0.8`.
- `de-vigil` depends on `arsenal-core`, `duckdb`, `pyarrow`, `pydantic`, `pyyaml`, `typer` only. It must not import `arsenal`, `pugio`, `gladius` or `censor`.
- Rules run only on a connection from `vigil.events` (table `events`, external access off). Never run a rule query on a plain `duckdb.connect()`.
- Exit codes: `0` ok, `1` a rule test failed / a rule errored during `run` / `--fail-on` matched, `2` usage or input error.
- stdout carries one JSON document; human text goes to stderr.
- Before each commit: `uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil`. DuckDB and PyArrow ship partial types; when pyright strict reports an unknown-type error on one of their calls, add `# pyright: ignore[<rule it names>]` on that line, as `packages/pugio/src/pugio/sources/file.py` does. Do not loosen the config.
- Format `packages/vigil` only; do not reformat other packages.
- Commit messages: `<type>(vigil): <description>` and end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## File Structure

```
packages/vigil/
  pyproject.toml, README.md, LICENSE
  src/vigil/
    __init__.py, py.typed
    events.py     EVENT_COLUMNS, empty_events, fixture_events, resolve_event_files, load_events
    rule.py       Severity, SEVERITY_ORDER, RuleTests, Rule, load_rules
    testing.py    check_rule, check_rules
    run.py        ALERT_SCHEMA, alert_id, RunSummary, existing_alert_ids, run_rules
    coverage.py   Technique, CoverageReport (+ parts), load_techniques, build_coverage
    unpack.py     unpack_cloudtrail
    cli.py        app: test, run, coverage, unpack
    reference/cloudtrail-techniques.yaml
  tests/
    conftest.py, _events.py
    test_events.py, test_rule.py, test_testing.py, test_run.py, test_coverage.py, test_unpack.py, test_cli.py
schemas/vigil-rule.json, schemas/vigil-coverage.json
```

---

### Task 1: Package scaffold and the `events` table

**Files:**
- Create: `packages/vigil/pyproject.toml`, `packages/vigil/README.md`, `packages/vigil/LICENSE`
- Create: `packages/vigil/src/vigil/__init__.py`, `packages/vigil/src/vigil/py.typed`, `packages/vigil/src/vigil/events.py`
- Create: `packages/vigil/tests/conftest.py`, `packages/vigil/tests/_events.py`, `packages/vigil/tests/test_events.py`
- Modify: `pyproject.toml` (root), `.github/workflows/release.yml`

**Interfaces:**
- Produces: `EVENT_COLUMNS: dict[str, str]`, `empty_events() -> duckdb.DuckDBPyConnection`, `fixture_events(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection`, `resolve_event_files(pattern: str) -> list[str]`, `load_events(pattern: str) -> duckdb.DuckDBPyConnection`. Test helper `_events.write_events(path: Path, rows: list[dict[str, Any]]) -> None`.

- [ ] **Step 1: Scaffold the package**

`packages/vigil/pyproject.toml`:

```toml
[project]
name = "de-vigil"
version = "0.7.3"
description = "Vigil — run detection rules over normalized security logs and report coverage gaps"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
license-files = ["LICENSE"]
dependencies = [
    "arsenal-core>=0.7,<0.8",
    "duckdb>=1.0",
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
    "Topic :: Security",
]

[project.urls]
Homepage = "https://github.com/KooEric/de-arsenal"
Repository = "https://github.com/KooEric/de-arsenal"

[project.scripts]
vigil = "vigil.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/vigil"]
```

`packages/vigil/src/vigil/__init__.py`:

```python
"""Vigil — detection rules over normalized security logs."""

from importlib.metadata import version

__version__ = version("de-vigil")
```

`packages/vigil/README.md`:

```markdown
# Vigil (`de-vigil`)

> 야간 순찰대. 정규화된 보안 로그에 탐지 룰을 실행하고, 룰이 비어 있는 곳을 알려 준다. (experimental)
```

Then:

```bash
cp packages/censor/LICENSE packages/vigil/LICENSE
touch packages/vigil/src/vigil/py.typed
```

`packages/vigil/tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

- [ ] **Step 2: Register the package in the workspace**

Root `pyproject.toml`: add `"de-vigil",` after `"de-censor",` in `dependencies`; add `de-vigil = { workspace = true }` after the `de-censor` line in `[tool.uv.sources]`; add `"vigil"` to `known-first-party`.

`.github/workflows/release.yml`: add `          - de-vigil` after `          - de-censor` (line 34) and `            de-vigil) package_prefix="de_vigil" ;;` after the `de-censor)` case (line 102).

Run: `uv sync && uv run python scripts/check_release_metadata.py`
Expected: `release metadata: 15 packages at v0.7.3`

- [ ] **Step 3: Write the test helper and the failing tests**

`packages/vigil/tests/_events.py`:

```python
"""Write an `events` Parquet file for tests."""

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("event_time", pa.timestamp("us")),
        ("source", pa.string()),
        ("service", pa.string()),
        ("action", pa.string()),
        ("actor_type", pa.string()),
        ("actor_id", pa.string()),
        ("src_ip", pa.string()),
        ("region", pa.string()),
        ("outcome", pa.string()),
        ("mfa_used", pa.bool_()),
        ("resource", pa.string()),
        ("raw", pa.string()),
    ]
)


def write_events(path: Path, rows: list[dict[str, Any]]) -> None:
    full = [{**dict.fromkeys(SCHEMA.names), **row} for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(full, schema=SCHEMA), path)
```

`packages/vigil/tests/test_events.py`:

```python
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _events import write_events

from arsenal_core.errors import FatalError
from vigil.events import EVENT_COLUMNS, empty_events, fixture_events, load_events


def test_empty_events_has_every_column_and_no_rows() -> None:
    con = empty_events()
    names = [row[0] for row in con.execute("DESCRIBE events").fetchall()]
    assert names == list(EVENT_COLUMNS)
    assert con.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_fixture_events_fills_id_time_and_nulls() -> None:
    con = fixture_events([{"action": "ConsoleLogin"}, {"event_id": "mine", "action": "X"}])
    rows = con.execute("SELECT event_id, action, actor_id FROM events ORDER BY action").fetchall()
    assert rows == [("fixture-0", "ConsoleLogin", None), ("mine", "X", None)]


def test_fixture_events_rejects_unknown_column() -> None:
    with pytest.raises(FatalError, match="unknown columns"):
        fixture_events([{"acton": "typo"}])


def test_connections_cannot_read_outside_events(tmp_path: Path) -> None:
    outside = tmp_path / "secret.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    con = empty_events()
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{outside}')")
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access=true")


def test_load_events_reads_a_directory(tmp_path: Path) -> None:
    write_events(tmp_path / "events" / "a.parquet", [{"event_id": "e1", "action": "A"}])
    write_events(
        tmp_path / "events" / "b.parquet",
        [{"event_id": "e2", "action": "B", "event_time": datetime(2026, 1, 2)}],
    )
    con = load_events(str(tmp_path / "events"))
    assert con.execute("SELECT count(*) FROM events").fetchone() == (2,)


def test_load_events_without_files_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no event files"):
        load_events(str(tmp_path / "*.parquet"))


def test_load_events_names_missing_columns(tmp_path: Path) -> None:
    pq.write_table(pa.table({"event_id": ["e1"]}), tmp_path / "thin.parquet")
    with pytest.raises(FatalError, match="missing columns"):
        load_events(str(tmp_path / "thin.parquet"))
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: collection error, `ModuleNotFoundError: No module named 'vigil.events'`

- [ ] **Step 5: Implement `events.py`**

`packages/vigil/src/vigil/events.py`:

```python
"""The `events` table that rules read, and the locked connections that hold it.

Every connection returned here has external access switched off, so a rule query can read
`events` and nothing else on disk or over the network. DuckDB does not let the setting be
switched back on while the connection is open.
"""

from __future__ import annotations

import glob as globlib
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from arsenal_core.errors import FatalError

EVENT_COLUMNS: Final[dict[str, str]] = {
    "event_id": "VARCHAR",
    "event_time": "TIMESTAMP",
    "source": "VARCHAR",
    "service": "VARCHAR",
    "action": "VARCHAR",
    "actor_type": "VARCHAR",
    "actor_id": "VARCHAR",
    "src_ip": "VARCHAR",
    "region": "VARCHAR",
    "outcome": "VARCHAR",
    "mfa_used": "BOOLEAN",
    "resource": "VARCHAR",
    "raw": "VARCHAR",
}

# event_time given to a fixture event that does not set one.
FIXTURE_TIME: Final = datetime(2026, 1, 1)


def _lock(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET enable_external_access=false")


def empty_events() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    columns = ", ".join(f"{name} {kind}" for name, kind in EVENT_COLUMNS.items())
    con.execute(f"CREATE TABLE events ({columns})")
    _lock(con)
    return con


def fixture_events(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    """`events` holding exactly `rows`. Columns a row leaves out are NULL."""
    con = empty_events()
    placeholders = ", ".join("?" for _ in EVENT_COLUMNS)
    for index, row in enumerate(rows):
        unknown = sorted(set(row) - set(EVENT_COLUMNS))
        if unknown:
            con.close()
            raise FatalError(f"fixture event has unknown columns: {unknown}")
        full: dict[str, Any] = dict.fromkeys(EVENT_COLUMNS)
        full["event_id"] = f"fixture-{index}"
        full["event_time"] = FIXTURE_TIME
        full.update(row)
        con.execute(f"INSERT INTO events VALUES ({placeholders})", list(full.values()))
    return con


def resolve_event_files(pattern: str) -> list[str]:
    """A directory means every *.parquet under it; anything else is a glob."""
    path = Path(pattern)
    if path.is_dir():
        files = sorted(str(p) for p in path.rglob("*.parquet"))
    else:
        files = sorted(globlib.glob(pattern, recursive=True))
    if not files:
        raise FatalError(f"no event files match {pattern}")
    return files


def load_events(pattern: str) -> duckdb.DuckDBPyConnection:
    """Copy the lake into an in-memory `events` table, then lock the connection."""
    files = resolve_event_files(pattern)
    listing = ", ".join("'" + name.replace("'", "''") + "'" for name in files)
    con = duckdb.connect()
    try:
        con.execute(
            f"CREATE TABLE events AS SELECT * FROM read_parquet([{listing}], union_by_name=true)"
        )
    except duckdb.Error as e:
        con.close()
        raise FatalError(f"cannot read events from {pattern}: {e}") from e
    present = {str(row[0]) for row in con.execute("DESCRIBE events").fetchall()}
    missing = [name for name in EVENT_COLUMNS if name not in present]
    if missing:
        con.close()
        raise FatalError(f"events is missing columns: {missing}")
    _lock(con)
    return con
```

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `7 passed`

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil pyproject.toml uv.lock .github/workflows/release.yml
git commit -m "feat(vigil): package scaffold and locked events connections"
```

---

### Task 2: Rule model and loader

**Files:**
- Create: `packages/vigil/src/vigil/rule.py`, `packages/vigil/tests/test_rule.py`, `schemas/vigil-rule.json`

**Interfaces:**
- Consumes: `vigil.events.empty_events`.
- Produces: `Severity = Literal["low","medium","high","critical"]`, `SEVERITY_ORDER: tuple[Severity, ...]`, `RuleTests(match: list[Case], no_match: list[Case])`, `Rule(id, title, severity, attack: list[str], source, query, tests: RuleTests)`, `load_rules(rules_dir: Path) -> list[Rule]` (sorted by file name; raises `FatalError`). A `Case` is `dict[str, Any] | list[dict[str, Any]]`. `Rule.query` is stored stripped with no trailing `;`.

- [ ] **Step 1: Write the failing tests**

`packages/vigil/tests/test_rule.py`:

```python
import json
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from vigil.rule import Rule, load_rules

SCHEMA = Path(__file__).parents[3] / "schemas" / "vigil-rule.json"

GOOD = """\
id: aws-root-console-login
title: Root console login
severity: high
attack: [T1078.004]
source: cloudtrail
query: |
  SELECT * FROM events WHERE action = 'ConsoleLogin' AND actor_type = 'Root';
tests:
  match:
    - { action: ConsoleLogin, actor_type: Root }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser }
"""


def _write(rules_dir: Path, name: str, text: str) -> Path:
    rules_dir.mkdir(exist_ok=True)
    path = rules_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_rules_reads_a_valid_rule(tmp_path: Path) -> None:
    _write(tmp_path, "root.yaml", GOOD)
    (rule,) = load_rules(tmp_path)
    assert rule.id == "aws-root-console-login"
    assert rule.attack == ["T1078.004"]
    assert not rule.query.endswith(";")
    assert len(rule.tests.match) == 1


def test_missing_directory_and_empty_directory_are_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="not found"):
        load_rules(tmp_path / "nope")
    with pytest.raises(FatalError, match="no rule files"):
        load_rules(tmp_path)


def test_schema_error_names_file_and_field(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.yaml", GOOD.replace("severity: high", "severity: urgent"))
    with pytest.raises(FatalError) as info:
        load_rules(tmp_path)
    assert str(path) in str(info.value)
    assert "severity" in str(info.value)


def test_duplicate_id_names_both_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", GOOD)
    _write(tmp_path, "b.yaml", GOOD)
    with pytest.raises(FatalError, match=r"duplicate rule id.*a\.yaml.*b\.yaml"):
        load_rules(tmp_path)


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE events",
        "SELECT * FROM events; DROP TABLE events",
        "SELEC * FROM events",
    ],
)
def test_query_must_be_one_select(tmp_path: Path, query: str) -> None:
    text = GOOD.split("query:")[0] + f'query: "{query}"\n'
    _write(tmp_path, "bad.yaml", text)
    with pytest.raises(FatalError, match="query"):
        load_rules(tmp_path)


def test_query_referencing_unknown_column_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("actor_type = 'Root'", "actor_kind = 'Root'"))
    with pytest.raises(FatalError, match="fails against events"):
        load_rules(tmp_path)


def test_query_must_return_event_id(tmp_path: Path) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("SELECT * FROM", "SELECT action FROM"))
    with pytest.raises(FatalError, match="must return event_id"):
        load_rules(tmp_path)


def test_bad_technique_id_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("[T1078.004]", "[cloud-accounts]"))
    with pytest.raises(FatalError, match="attack"):
        load_rules(tmp_path)


def test_schema_file_matches_model() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == Rule.model_json_schema()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest packages/vigil/tests/test_rule.py -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'vigil.rule'`

- [ ] **Step 3: Implement `rule.py`**

`packages/vigil/src/vigil/rule.py`:

```python
"""Detection rule files. One YAML file is one rule; every rule is validated on load."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from arsenal_core.errors import FatalError
from vigil.events import empty_events

Severity = Literal["low", "medium", "high", "critical"]
SEVERITY_ORDER: Final[tuple[Severity, ...]] = ("low", "medium", "high", "critical")

Event = dict[str, Any]
# One event, or several events that must be looked at together (correlation rules).
Case = Event | list[Event]

_TECHNIQUE: Final = re.compile(r"^T\d{4}(\.\d{3})?$")


class RuleTests(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match: list[Case] = Field(default_factory=list)
    no_match: list[Case] = Field(default_factory=list)


class Rule(BaseModel):
    """The rule file contract (schemas/vigil-rule.json)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    severity: Severity
    attack: list[str] = Field(min_length=1)
    source: str = Field(min_length=1)
    query: str
    tests: RuleTests = Field(default_factory=RuleTests)

    @field_validator("attack")
    @classmethod
    def _technique_ids(cls, value: list[str]) -> list[str]:
        bad = [item for item in value if not _TECHNIQUE.match(item)]
        if bad:
            raise ValueError(f"not ATT&CK technique ids (T1234 or T1234.001): {bad}")
        return value

    @field_validator("query")
    @classmethod
    def _one_select(cls, value: str) -> str:
        text = value.strip().rstrip(";").strip()
        try:
            statements = duckdb.extract_statements(text)
        except duckdb.Error as e:
            raise ValueError(f"does not parse: {e}") from e
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("must be exactly one SELECT statement")
        return text


def load_rules(rules_dir: Path) -> list[Rule]:
    if not rules_dir.is_dir():
        raise FatalError(f"rules directory not found: {rules_dir}")
    paths = sorted(rules_dir.glob("*.yaml"))
    if not paths:
        raise FatalError(f"no rule files (*.yaml) in {rules_dir}")
    rules: list[Rule] = []
    seen: dict[str, Path] = {}
    for path in paths:
        rule = _load_one(path)
        if rule.id in seen:
            raise FatalError(f"duplicate rule id {rule.id!r}: {seen[rule.id]} and {path}")
        seen[rule.id] = path
        rules.append(rule)
    return rules


def _load_one(path: Path) -> Rule:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise FatalError(f"{path}: cannot read rule: {e}") from e
    try:
        rule = Rule.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'rule'}: {error['msg']}"
            for error in e.errors()
        )
        raise FatalError(f"{path}: {problems}") from e
    _check_against_events(path, rule)
    return rule


def _check_against_events(path: Path, rule: Rule) -> None:
    """Run the query on an empty `events`: catches unknown columns and a missing event_id."""
    con = empty_events()
    try:
        description = con.execute(f"SELECT * FROM ({rule.query}) LIMIT 0").description
    except duckdb.Error as e:
        raise FatalError(f"{path}: query fails against events: {e}") from e
    finally:
        con.close()
    names = [str(column[0]) for column in description or []]
    if "event_id" not in names:
        raise FatalError(f"{path}: query must return event_id (returns: {names})")
```

- [ ] **Step 4: Generate the schema file**

```bash
uv run python -c "
import json, pathlib
from vigil.rule import Rule
pathlib.Path('schemas/vigil-rule.json').write_text(json.dumps(Rule.model_json_schema(), indent=2) + '\n', encoding='utf-8')
"
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `18 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil schemas/vigil-rule.json
git commit -m "feat(vigil): rule model and loader with SELECT-only and column checks"
```

---

### Task 3: `vigil test`

**Files:**
- Create: `packages/vigil/src/vigil/testing.py`, `packages/vigil/src/vigil/cli.py`
- Create: `packages/vigil/tests/test_testing.py`, `packages/vigil/tests/test_cli.py`

**Interfaces:**
- Consumes: `Rule`, `load_rules`, `fixture_events`.
- Produces: `check_rule(rule: Rule) -> list[str]` (failure messages, empty = pass), `check_rules(rules: list[Rule]) -> dict[str, list[str]]`. In `cli.py`: `app`, `EXIT_FAILURES = 1`, `EXIT_TOOL_ERROR = 2`, `RulesOption`, `_load(rules_dir: Path) -> list[Rule]`, `_err(message: str) -> None`. `vigil test` stdout: `{"rules": <int>, "failed": {"<rule id>": ["<message>", ...]}}`.

- [ ] **Step 1: Write the failing tests**

`packages/vigil/tests/test_testing.py`:

```python
from vigil.rule import Rule, RuleTests
from vigil.testing import check_rule

PAIR_QUERY = (
    "SELECT p.* FROM events p JOIN events k ON k.actor_id = p.actor_id "
    "WHERE k.action = 'CreateAccessKey' AND p.action = 'AttachUserPolicy' "
    "AND p.event_time >= k.event_time AND p.event_time <= k.event_time + INTERVAL 1 HOUR"
)


def _rule(query: str, tests: RuleTests) -> Rule:
    return Rule(
        id="r", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
        query=query, tests=tests,
    )  # fmt: skip


def test_passing_rule_has_no_failures() -> None:
    rule = _rule(
        "SELECT * FROM events WHERE actor_type = 'Root'",
        RuleTests(match=[{"actor_type": "Root"}], no_match=[{"actor_type": "IAMUser"}]),
    )
    assert check_rule(rule) == []


def test_missing_cases_fail() -> None:
    rule = _rule("SELECT * FROM events", RuleTests())
    assert check_rule(rule) == [
        "no match case (tests.match is empty)",
        "no no_match case (tests.no_match is empty)",
    ]


def test_wrong_expectations_are_reported_with_index() -> None:
    rule = _rule(
        "SELECT * FROM events WHERE actor_type = 'Root'",
        RuleTests(match=[{"actor_type": "IAMUser"}], no_match=[{"actor_type": "Root"}]),
    )
    assert check_rule(rule) == [
        "match[0]: expected a detection, got none",
        "no_match[0]: expected no detection, got 1",
    ]


def test_multi_event_case_runs_in_isolation() -> None:
    key = {"actor_id": "u1", "action": "CreateAccessKey", "event_time": "2026-01-01 10:00:00"}
    soon = {"actor_id": "u1", "action": "AttachUserPolicy", "event_time": "2026-01-01 10:30:00"}
    late = {"actor_id": "u1", "action": "AttachUserPolicy", "event_time": "2026-01-01 12:00:00"}
    rule = _rule(PAIR_QUERY, RuleTests(match=[[key, soon]], no_match=[[key, late], soon]))
    assert check_rule(rule) == []
```

`packages/vigil/tests/test_cli.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from vigil.cli import app

runner = CliRunner()

RULE = """\
id: {id}
title: Root console login
severity: {severity}
attack: [T1078.004]
source: cloudtrail
query: SELECT * FROM events WHERE action = 'ConsoleLogin' AND actor_type = 'Root'
tests:
  match:
    - {{ action: ConsoleLogin, actor_type: {match_actor} }}
  no_match:
    - {{ action: ConsoleLogin, actor_type: IAMUser }}
"""


def write_rule(
    rules_dir: Path, rule_id: str = "root-login", severity: str = "high", match_actor: str = "Root"
) -> None:
    rules_dir.mkdir(exist_ok=True)
    text = RULE.format(id=rule_id, severity=severity, match_actor=match_actor)
    (rules_dir / f"{rule_id}.yaml").write_text(text, encoding="utf-8")


def test_test_command_passes(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"rules": 1, "failed": {}}


def test_test_command_fails_with_exit_1(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules", match_actor="IAMUser")
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 1, result.output
    assert list(json.loads(result.stdout)["failed"]) == ["root-login"]


def test_missing_rules_directory_is_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "nope")])
    assert result.exit_code == 2, result.output
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'vigil.testing'`

- [ ] **Step 3: Implement `testing.py`**

`packages/vigil/src/vigil/testing.py`:

```python
"""Run each rule against its own fixture cases. A case runs alone in a fresh `events`."""

from __future__ import annotations

from vigil.events import fixture_events
from vigil.rule import Case, Rule


def check_rule(rule: Rule) -> list[str]:
    """Failure messages for one rule; an empty list means it passes."""
    failures: list[str] = []
    if not rule.tests.match:
        failures.append("no match case (tests.match is empty)")
    if not rule.tests.no_match:
        failures.append("no no_match case (tests.no_match is empty)")
    for index, case in enumerate(rule.tests.match):
        if _hits(rule, case) == 0:
            failures.append(f"match[{index}]: expected a detection, got none")
    for index, case in enumerate(rule.tests.no_match):
        hits = _hits(rule, case)
        if hits:
            failures.append(f"no_match[{index}]: expected no detection, got {hits}")
    return failures


def check_rules(rules: list[Rule]) -> dict[str, list[str]]:
    return {rule.id: check_rule(rule) for rule in rules}


def _hits(rule: Rule, case: Case) -> int:
    rows = case if isinstance(case, list) else [case]
    con = fixture_events(rows)
    try:
        return len(con.execute(rule.query).fetchall())
    finally:
        con.close()
```

- [ ] **Step 4: Implement `cli.py` with the `test` command**

The `@app.callback()` matters: with a single command and no callback, Typer drops the sub-command name and `vigil test` would not parse.

`packages/vigil/src/vigil/cli.py`:

```python
"""vigil CLI. JSON on stdout, people on stderr. Exit 0 ok, 1 failures, 2 tool error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from arsenal_core.errors import ArsenalError
from vigil.rule import Rule, load_rules
from vigil.testing import check_rules

app = typer.Typer(
    help="Vigil — detection rules over normalized security logs.", no_args_is_help=True
)

EXIT_FAILURES = 1
EXIT_TOOL_ERROR = 2

RulesOption = Annotated[Path, typer.Option("--rules", help="Directory of rule YAML files.")]


@app.callback()
def main() -> None:
    """Vigil — detection rules over normalized security logs."""


def _err(message: str) -> None:
    typer.echo(message, err=True)


def _fail(error: ArsenalError) -> typer.Exit:
    _err(f"vigil: {error}")
    return typer.Exit(EXIT_TOOL_ERROR)


def _load(rules_dir: Path) -> list[Rule]:
    try:
        return load_rules(rules_dir)
    except ArsenalError as e:
        raise _fail(e) from e


@app.command("test")
def test_command(rules: RulesOption = Path("rules")) -> None:
    """Check every rule against its fixture cases."""
    results = check_rules(_load(rules))
    for rule_id, failures in results.items():
        _err(f"{'FAIL' if failures else 'ok  '} {rule_id}")
        for failure in failures:
            _err(f"     {failure}")
    failed = {rule_id: failures for rule_id, failures in results.items() if failures}
    typer.echo(json.dumps({"rules": len(results), "failed": failed}))
    if failed:
        raise typer.Exit(EXIT_FAILURES)
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `25 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil
git commit -m "feat(vigil): vigil test runs each rule against its fixture cases"
```

---

### Task 4: `vigil run`

**Files:**
- Create: `packages/vigil/src/vigil/run.py`, `packages/vigil/tests/test_run.py`
- Modify: `packages/vigil/src/vigil/cli.py`, `packages/vigil/tests/test_cli.py`

**Interfaces:**
- Consumes: `Rule`, `Severity`, `SEVERITY_ORDER`, `load_events`, `_events.write_events`, and from `test_cli.py` the `write_rule` helper.
- Produces: `ALERT_SCHEMA: pa.Schema`, `alert_id(rule_id: str, event_id: str) -> str`, `RunSummary(new_by_rule: dict[str, int], failed: dict[str, str], new_severities: frozenset[str])`, `existing_alert_ids(out_dir: Path) -> set[str]`, `run_rules(rules: list[Rule], con: duckdb.DuckDBPyConnection, out_dir: Path, detected_at: datetime) -> RunSummary`. `vigil run` stdout: `{"rules": <int>, "new_alerts": <int>, "by_rule": {...}, "failed": {...}}`.

- [ ] **Step 1: Write the failing tests**

`packages/vigil/tests/test_run.py`:

```python
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
from _events import write_events

from vigil.events import load_events
from vigil.rule import Rule
from vigil.run import ALERT_SCHEMA, alert_id, existing_alert_ids, run_rules

NOW = datetime(2026, 11, 28, 9, 0, 0)

ROOT = Rule(
    id="root-login", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
    query="SELECT * FROM events WHERE actor_type = 'Root'",
)  # fmt: skip
BROKEN = Rule(
    id="broken", title="t", severity="low", attack=["T1530"], source="cloudtrail",
    query="SELECT event_id FROM events WHERE CAST(action AS INTEGER) = 1",
)  # fmt: skip


def _lake(tmp_path: Path) -> str:
    write_events(
        tmp_path / "events" / "a.parquet",
        [
            {"event_id": "e1", "actor_type": "Root", "actor_id": "root", "action": "ConsoleLogin",
             "event_time": datetime(2026, 11, 1, 10)},
            {"event_id": "e2", "actor_type": "IAMUser", "actor_id": "u1", "action": "ConsoleLogin",
             "event_time": datetime(2026, 11, 1, 11)},
        ],
    )  # fmt: skip
    return str(tmp_path / "events")


def test_alert_id_is_stable_and_rule_specific() -> None:
    assert alert_id("r1", "e1") == alert_id("r1", "e1")
    assert alert_id("r1", "e1") != alert_id("r2", "e1")
    assert len(alert_id("r1", "e1")) == 32


def test_run_writes_alerts_with_the_alert_schema(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    summary = run_rules([ROOT], load_events(_lake(tmp_path)), out, NOW)
    assert summary.new_by_rule == {"root-login": 1}
    assert summary.new_severities == frozenset({"high"})
    (file,) = list(out.glob("*.parquet"))
    table = pq.read_table(file)
    assert table.schema.equals(ALERT_SCHEMA)
    row = table.to_pylist()[0]
    assert (row["rule_id"], row["event_id"], row["actor_id"]) == ("root-login", "e1", "root")
    assert row["attack"] == ["T1078.004"]
    assert row["detected_at"] == NOW


def test_second_run_adds_nothing(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    lake = _lake(tmp_path)
    run_rules([ROOT], load_events(lake), out, NOW)
    summary = run_rules([ROOT], load_events(lake), out, NOW)
    assert summary.new_by_rule == {"root-login": 0}
    assert summary.new_severities == frozenset()
    assert len(list(out.glob("*.parquet"))) == 1
    assert len(existing_alert_ids(out)) == 1


def test_a_failing_rule_does_not_stop_the_others(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    summary = run_rules([BROKEN, ROOT], load_events(_lake(tmp_path)), out, NOW)
    assert list(summary.failed) == ["broken"]
    assert summary.new_by_rule == {"root-login": 1}


def test_no_detections_writes_no_file(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    quiet = ROOT.model_copy(update={"query": "SELECT * FROM events WHERE actor_type = 'Nobody'"})
    run_rules([quiet], load_events(_lake(tmp_path)), out, NOW)
    assert not out.exists() or not list(out.iterdir())
```

Append to `packages/vigil/tests/test_cli.py`:

```python
from datetime import datetime

from _events import write_events
from click.testing import Result


def _lake(tmp_path: Path) -> str:
    write_events(
        tmp_path / "events" / "a.parquet",
        [{"event_id": "e1", "actor_type": "Root", "action": "ConsoleLogin",
          "event_time": datetime(2026, 11, 1, 10)}],
    )  # fmt: skip
    return str(tmp_path / "events")


def _run(tmp_path: Path, *extra: str) -> Result:
    return runner.invoke(
        app,
        ["run", "--rules", str(tmp_path / "rules"), "--events", _lake(tmp_path),
         "--out", str(tmp_path / "alerts"), *extra],
    )  # fmt: skip


def test_run_command_reports_new_alerts_then_none(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    first = _run(tmp_path)
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["new_alerts"] == 1
    second = _run(tmp_path)
    assert json.loads(second.stdout)["new_alerts"] == 0


def test_fail_on_exits_1_only_at_or_above_the_threshold(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules", severity="medium")
    assert _run(tmp_path, "--fail-on", "high").exit_code == 0
    (tmp_path / "alerts" / next(p.name for p in (tmp_path / "alerts").iterdir())).unlink()
    assert _run(tmp_path, "--fail-on", "medium").exit_code == 1


def test_run_without_event_files_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(
        app,
        ["run", "--rules", str(tmp_path / "rules"), "--events", str(tmp_path / "none"),
         "--out", str(tmp_path / "alerts")],
    )  # fmt: skip
    assert result.exit_code == 2, result.output
```

Move the three new import lines to the top of `test_cli.py` with the existing imports (ruff's import sorting requires it).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'vigil.run'`

- [ ] **Step 3: Implement `run.py`**

`packages/vigil/src/vigil/run.py`:

```python
"""Run rules over `events` and append new alerts to a Parquet directory.

An alert is identified by (rule, event). Running again over the same lake finds the same
ids, sees they are already on disk, and writes nothing.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from vigil.rule import Rule

ALERT_SCHEMA: Final = pa.schema(
    [
        ("alert_id", pa.string()),
        ("rule_id", pa.string()),
        ("severity", pa.string()),
        ("attack", pa.list_(pa.string())),
        ("event_id", pa.string()),
        ("event_time", pa.timestamp("us")),
        ("actor_id", pa.string()),
        ("detected_at", pa.timestamp("us")),
    ]
)

_ALERT_ID_LENGTH: Final = 32


@dataclass(frozen=True)
class RunSummary:
    new_by_rule: dict[str, int]
    failed: dict[str, str]
    new_severities: frozenset[str]


def alert_id(rule_id: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{rule_id}\x1f{event_id}".encode()).hexdigest()
    return digest[:_ALERT_ID_LENGTH]


def existing_alert_ids(out_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not out_dir.is_dir():
        return ids
    for file in sorted(out_dir.glob("*.parquet")):
        ids.update(pq.read_table(file, columns=["alert_id"]).column("alert_id").to_pylist())
    return ids


def run_rules(
    rules: list[Rule], con: duckdb.DuckDBPyConnection, out_dir: Path, detected_at: datetime
) -> RunSummary:
    seen = existing_alert_ids(out_dir)
    rows: list[dict[str, Any]] = []
    new_by_rule: dict[str, int] = {}
    failed: dict[str, str] = {}
    for rule in rules:
        try:
            hits = con.execute(
                "SELECT DISTINCT e.event_id, e.event_time, e.actor_id "
                f"FROM ({rule.query}) AS q JOIN events AS e ON e.event_id = q.event_id "
                "ORDER BY e.event_time, e.event_id"
            ).fetchall()
        except duckdb.Error as e:
            failed[rule.id] = str(e)
            continue
        count = 0
        for event_id, event_time, actor_id in hits:
            identifier = alert_id(rule.id, str(event_id))
            if identifier in seen:
                continue
            seen.add(identifier)
            count += 1
            rows.append(
                {
                    "alert_id": identifier,
                    "rule_id": rule.id,
                    "severity": rule.severity,
                    "attack": rule.attack,
                    "event_id": event_id,
                    "event_time": event_time,
                    "actor_id": actor_id,
                    "detected_at": detected_at,
                }
            )
        new_by_rule[rule.id] = count
    if rows:
        _write(out_dir, rows, detected_at)
    return RunSummary(new_by_rule, failed, frozenset(str(row["severity"]) for row in rows))


def _write(out_dir: Path, rows: list[dict[str, Any]], detected_at: datetime) -> None:
    """Write to a temp name and rename, so a crash leaves the existing alerts untouched."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = detected_at.strftime("%Y%m%dT%H%M%S")
    final = out_dir / f"alerts-{stamp}-{uuid4().hex[:8]}.parquet"
    temp = out_dir / f".{final.name}.tmp"
    pq.write_table(pa.Table.from_pylist(rows, schema=ALERT_SCHEMA), temp)
    os.replace(temp, final)
```

- [ ] **Step 4: Add the `run` command to `cli.py`**

Add imports: `from datetime import UTC, datetime`, `from enum import StrEnum`, `from vigil.events import load_events`, `from vigil.rule import SEVERITY_ORDER, Rule, load_rules`, `from vigil.run import run_rules`. Then add below `test_command`:

```python
class FailOn(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@app.command("run")
def run_command(
    events: Annotated[str, typer.Option("--events", help="Glob or directory of events Parquet.")],
    out: Annotated[Path, typer.Option("--out", help="Directory alerts are appended to.")],
    rules: RulesOption = Path("rules"),
    fail_on: Annotated[
        FailOn | None, typer.Option("--fail-on", help="Exit 1 on a new alert at or above this.")
    ] = None,
) -> None:
    """Run every rule over the events lake and record new alerts."""
    loaded = _load(rules)
    try:
        con = load_events(events)
    except ArsenalError as e:
        raise _fail(e) from e
    try:
        summary = run_rules(loaded, con, out, datetime.now(UTC).replace(tzinfo=None))
    finally:
        con.close()
    for rule_id, count in summary.new_by_rule.items():
        _err(f"{count:>5} new  {rule_id}")
    for rule_id, reason in summary.failed.items():
        _err(f"ERROR      {rule_id}: {reason}")
    typer.echo(
        json.dumps(
            {
                "rules": len(loaded),
                "new_alerts": sum(summary.new_by_rule.values()),
                "by_rule": summary.new_by_rule,
                "failed": summary.failed,
            }
        )
    )
    rank = {name: position for position, name in enumerate(SEVERITY_ORDER)}
    over = fail_on is not None and any(
        rank[severity] >= rank[fail_on.value] for severity in summary.new_severities
    )
    if summary.failed or over:
        raise typer.Exit(EXIT_FAILURES)
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `33 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil
git commit -m "feat(vigil): vigil run records new alerts once per rule and event"
```

---

### Task 5: `vigil coverage`

**Files:**
- Create: `packages/vigil/src/vigil/coverage.py`, `packages/vigil/src/vigil/reference/cloudtrail-techniques.yaml`, `packages/vigil/tests/test_coverage.py`, `schemas/vigil-coverage.json`
- Modify: `packages/vigil/src/vigil/cli.py`, `packages/vigil/tests/test_cli.py`

**Interfaces:**
- Consumes: `Rule`, `EVENT_COLUMNS`, `load_events`.
- Produces: `Technique(id: str, name: str)`, `load_techniques() -> list[Technique]`, `build_coverage(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> CoverageReport`. `CoverageReport` fields: `schema_version: Literal[1]`, `rules: int`, `techniques: list[TechniqueCoverage(id, name, rules: list[str], tests: int)]`, `uncovered: list[str]`, `unlisted: list[str]`, `sources: list[SourceCoverage(source, rules: int, last_event_time: datetime | None)]`, `columns: list[ColumnHealth(column, used_by: list[str], null_ratio: float | None)]`. `vigil coverage` stdout is `CoverageReport` as JSON.

- [ ] **Step 1: Write the technique list**

Check each name against https://attack.mitre.org/matrices/enterprise/cloud/iaas/ and fix any that differ before committing.

`packages/vigil/src/vigil/reference/cloudtrail-techniques.yaml`:

```yaml
# ATT&CK techniques that AWS CloudTrail management events can show.
# Source: MITRE ATT&CK Enterprise v20 (October 2026), IaaS matrix. Hand-picked 2026-11-28; not the full matrix.
- { id: T1078.004, name: "Valid Accounts: Cloud Accounts" }
- { id: T1098.001, name: "Account Manipulation: Additional Cloud Credentials" }
- { id: T1087.004, name: "Account Discovery: Cloud Account" }
- { id: T1098.003, name: "Account Manipulation: Additional Cloud Roles" }
- { id: T1110, name: "Brute Force" }
- { id: T1136.003, name: "Create Account: Cloud Account" }
- { id: T1485, name: "Data Destruction" }
- { id: T1496, name: "Resource Hijacking" }
- { id: T1526, name: "Cloud Service Discovery" }
- { id: T1530, name: "Data from Cloud Storage" }
- { id: T1535, name: "Unused/Unsupported Cloud Regions" }
- { id: T1537, name: "Transfer Data to Cloud Account" }
- { id: T1556.006, name: "Modify Authentication Process: Multi-Factor Authentication" }
- { id: T1685.002, name: "Disable or Modify Tools: Disable or Modify Cloud Log" }
- { id: T1578.001, name: "Modify Cloud Compute Infrastructure: Create Snapshot" }
- { id: T1578.002, name: "Modify Cloud Compute Infrastructure: Create Cloud Instance" }
- { id: T1580, name: "Cloud Infrastructure Discovery" }
- { id: T1619, name: "Cloud Storage Object Discovery" }
- { id: T1648, name: "Serverless Execution" }
- { id: T1651, name: "Cloud Administration Command" }
```

- [ ] **Step 2: Write the failing tests**

`packages/vigil/tests/test_coverage.py`:

```python
import json
from datetime import datetime
from pathlib import Path

from _events import write_events

from vigil.coverage import CoverageReport, build_coverage, load_techniques
from vigil.events import load_events
from vigil.rule import Rule, RuleTests

SCHEMA = Path(__file__).parents[3] / "schemas" / "vigil-coverage.json"

ROOT = Rule(
    id="root-login", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
    query="SELECT * FROM events WHERE actor_type = 'Root' AND mfa_used IS NULL",
    tests=RuleTests(match=[{"actor_type": "Root"}], no_match=[{"actor_type": "IAMUser"}]),
)  # fmt: skip
ODD = Rule(
    id="odd", title="t", severity="low", attack=["T9999"], source="cloudtrail",
    query="SELECT * FROM events WHERE action = 'X'",
)  # fmt: skip


def test_technique_list_is_valid_and_unique() -> None:
    techniques = load_techniques()
    assert len(techniques) == 20
    assert len({t.id for t in techniques}) == 20


def test_coverage_from_rules_only() -> None:
    report = build_coverage([ROOT, ODD], None)
    assert report.rules == 2
    by_id = {t.id: t for t in report.techniques}
    assert by_id["T1078.004"].rules == ["root-login"]
    assert by_id["T1078.004"].tests == 2
    assert "T1530" in report.uncovered
    assert "T1078.004" not in report.uncovered
    assert report.unlisted == ["T9999"]
    assert [(s.source, s.rules, s.last_event_time) for s in report.sources] == [
        ("cloudtrail", 2, None)
    ]
    columns = {c.column: c for c in report.columns}
    assert columns["actor_type"].used_by == ["root-login"]
    assert columns["action"].used_by == ["odd"]
    assert columns["mfa_used"].null_ratio is None
    assert "region" not in columns


def test_coverage_with_events_adds_null_ratio_and_last_event(tmp_path: Path) -> None:
    write_events(
        tmp_path / "events" / "a.parquet",
        [
            {"event_id": "e1", "source": "cloudtrail", "actor_type": "Root",
             "event_time": datetime(2026, 11, 1, 10)},
            {"event_id": "e2", "source": "cloudtrail", "actor_type": None, "mfa_used": True,
             "event_time": datetime(2026, 11, 2, 10)},
        ],
    )  # fmt: skip
    report = build_coverage([ROOT], load_events(str(tmp_path / "events")))
    columns = {c.column: c for c in report.columns}
    assert columns["actor_type"].null_ratio == 0.5
    assert columns["mfa_used"].null_ratio == 0.5
    assert report.sources[0].last_event_time == datetime(2026, 11, 2, 10)


def test_schema_file_matches_model() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == CoverageReport.model_json_schema()
```

Append to `packages/vigil/tests/test_cli.py`:

```python
def test_coverage_command_prints_the_report(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(
        app, ["coverage", "--rules", str(tmp_path / "rules"), "--events", _lake(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["rules"] == 1
    assert "T1530" in payload["uncovered"]


def test_coverage_json_only_keeps_stderr_empty(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(app, ["coverage", "--rules", str(tmp_path / "rules"), "--json-only"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'vigil.coverage'`

- [ ] **Step 4: Implement `coverage.py`**

`packages/vigil/src/vigil/coverage.py`:

```python
"""Coverage: which techniques have rules, which have none, and whether the data rules need is there.

`CoverageReport` is the stdout JSON contract (schemas/vigil-coverage.json).
"""

from __future__ import annotations

import re
from datetime import datetime
from importlib import resources
from typing import Any, Final, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict

from vigil.events import EVENT_COLUMNS
from vigil.rule import Rule

_TECHNIQUES_FILE: Final = "cloudtrail-techniques.yaml"
_RATIO_DIGITS: Final = 4


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Technique(_Frozen):
    id: str
    name: str


class TechniqueCoverage(_Frozen):
    id: str
    name: str
    rules: list[str]
    tests: int


class SourceCoverage(_Frozen):
    source: str
    rules: int
    last_event_time: datetime | None


class ColumnHealth(_Frozen):
    column: str
    used_by: list[str]
    null_ratio: float | None


class CoverageReport(_Frozen):
    schema_version: Literal[1] = 1
    rules: int
    techniques: list[TechniqueCoverage]
    uncovered: list[str]
    unlisted: list[str]
    sources: list[SourceCoverage]
    columns: list[ColumnHealth]


def load_techniques() -> list[Technique]:
    text = (resources.files("vigil") / "data" / _TECHNIQUES_FILE).read_text(encoding="utf-8")
    raw: list[dict[str, Any]] = yaml.safe_load(text)
    return [Technique.model_validate(item) for item in raw]


def build_coverage(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> CoverageReport:
    """`con` is a connection from `vigil.events.load_events`, or None to read rule files only."""
    known = load_techniques()
    techniques = [_technique_coverage(technique, rules) for technique in known]
    known_ids = {technique.id for technique in known}
    used_ids = {technique_id for rule in rules for technique_id in rule.attack}
    return CoverageReport(
        rules=len(rules),
        techniques=techniques,
        uncovered=[item.id for item in techniques if not item.rules],
        unlisted=sorted(used_ids - known_ids),
        sources=_sources(rules, con),
        columns=_columns(rules, con),
    )


def _technique_coverage(technique: Technique, rules: list[Rule]) -> TechniqueCoverage:
    covering = [rule for rule in rules if technique.id in rule.attack]
    tests = sum(len(rule.tests.match) + len(rule.tests.no_match) for rule in covering)
    return TechniqueCoverage(
        id=technique.id, name=technique.name, rules=[rule.id for rule in covering], tests=tests
    )


def _sources(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> list[SourceCoverage]:
    result: list[SourceCoverage] = []
    for source in sorted({rule.source for rule in rules}):
        last: datetime | None = None
        if con is not None:
            row = con.execute(
                "SELECT max(event_time) FROM events WHERE source = ?", [source]
            ).fetchone()
            last = row[0] if row else None
        count = sum(1 for rule in rules if rule.source == source)
        result.append(SourceCoverage(source=source, rules=count, last_event_time=last))
    return result


def _columns(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> list[ColumnHealth]:
    """Columns at least one rule names in its query. Matching is by whole word in the SQL text."""
    result: list[ColumnHealth] = []
    for column in EVENT_COLUMNS:
        word = re.compile(rf"\b{column}\b")
        used_by = [rule.id for rule in rules if word.search(rule.query)]
        if not used_by:
            continue
        result.append(
            ColumnHealth(column=column, used_by=used_by, null_ratio=_null_ratio(con, column))
        )
    return result


def _null_ratio(con: duckdb.DuckDBPyConnection | None, column: str) -> float | None:
    if con is None:
        return None
    row = con.execute(f"SELECT count(*), count({column}) FROM events").fetchone()
    if row is None or row[0] == 0:
        return None
    return round(1 - row[1] / row[0], _RATIO_DIGITS)
```

- [ ] **Step 5: Generate the schema file**

```bash
uv run python -c "
import json, pathlib
from vigil.coverage import CoverageReport
pathlib.Path('schemas/vigil-coverage.json').write_text(json.dumps(CoverageReport.model_json_schema(), indent=2) + '\n', encoding='utf-8')
"
```

- [ ] **Step 6: Add the `coverage` command to `cli.py`**

Add import `from vigil.coverage import CoverageReport, build_coverage`. Then add below `run_command`:

```python
def _print_coverage(report: CoverageReport) -> None:
    _err(f"{report.rules} rules")
    for technique in report.techniques:
        mark = "    " if technique.rules else "GAP "
        _err(f"{mark}{technique.id:<10} {len(technique.rules)} rules  {technique.name}")
    for source in report.sources:
        _err(f"source {source.source}: {source.rules} rules, last event {source.last_event_time}")
    for column in report.columns:
        ratio = "n/a" if column.null_ratio is None else f"{column.null_ratio:.1%}"
        _err(f"column {column.column}: NULL {ratio}, used by {len(column.used_by)} rules")
    if report.unlisted:
        _err(f"techniques not in the reference list: {', '.join(report.unlisted)}")


@app.command("coverage")
def coverage_command(
    rules: RulesOption = Path("rules"),
    events: Annotated[
        str | None, typer.Option("--events", help="Glob or directory of events Parquet.")
    ] = None,
    json_only: Annotated[bool, typer.Option("--json-only", help="Print JSON only.")] = False,
) -> None:
    """Report techniques without rules, and NULL ratios of the columns rules read."""
    loaded = _load(rules)
    con = None
    if events is not None:
        try:
            con = load_events(events)
        except ArsenalError as e:
            raise _fail(e) from e
    try:
        report = build_coverage(loaded, con)
    finally:
        if con is not None:
            con.close()
    if not json_only:
        _print_coverage(report)
    typer.echo(report.model_dump_json())
```

- [ ] **Step 7: Run to verify they pass**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `39 passed`

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil schemas/vigil-coverage.json
git commit -m "feat(vigil): vigil coverage reports uncovered techniques and column NULL ratios"
```

---

### Task 6: `vigil unpack`

**Files:**
- Create: `packages/vigil/src/vigil/unpack.py`, `packages/vigil/tests/test_unpack.py`
- Modify: `packages/vigil/src/vigil/cli.py`, `packages/vigil/tests/test_cli.py`

**Interfaces:**
- Produces: `unpack_cloudtrail(src_dir: Path, out_file: Path) -> int` (number of events written). Output lines are `{"raw": "<event as a JSON string>"}`. `vigil unpack` stdout: `{"events": <int>, "out": "<path>"}`.

- [ ] **Step 1: Write the failing tests**

`packages/vigil/tests/test_unpack.py`:

```python
import gzip
import json
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from vigil.unpack import unpack_cloudtrail


def _records(*names: str) -> str:
    return json.dumps({"Records": [{"eventID": name, "eventName": "X"} for name in names]})


def test_unpack_reads_json_and_gzip_into_raw_lines(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    (src / "a.json").write_text(_records("e1", "e2"), encoding="utf-8")
    with gzip.open(src / "nested" / "b.json.gz", "wt", encoding="utf-8") as handle:
        handle.write(_records("e3"))
    out = tmp_path / "out" / "events.jsonl"

    assert unpack_cloudtrail(src, out) == 3

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [list(line) for line in lines] == [["raw"], ["raw"], ["raw"]]
    assert [json.loads(line["raw"])["eventID"] for line in lines] == ["e1", "e2", "e3"]


def test_file_without_records_is_fatal_and_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    bad = src / "bad.json"
    bad.write_text('{"events": []}', encoding="utf-8")
    out = tmp_path / "events.jsonl"
    with pytest.raises(FatalError, match="bad.json"):
        unpack_cloudtrail(src, out)
    assert not out.exists()


def test_missing_or_empty_source_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="not found"):
        unpack_cloudtrail(tmp_path / "nope", tmp_path / "o.jsonl")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FatalError, match="no CloudTrail files"):
        unpack_cloudtrail(tmp_path / "empty", tmp_path / "o.jsonl")
```

Append to `packages/vigil/tests/test_cli.py`:

```python
def test_unpack_command(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.json").write_text(json.dumps({"Records": [{"eventID": "e1"}]}), encoding="utf-8")
    out = tmp_path / "events.jsonl"
    result = runner.invoke(app, ["unpack", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"events": 1, "out": str(out)}
    assert runner.invoke(app, ["unpack", str(tmp_path / "nope"), "--out", str(out)]).exit_code == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'vigil.unpack'`

- [ ] **Step 3: Implement `unpack.py`**

`packages/vigil/src/vigil/unpack.py`:

```python
"""Turn CloudTrail log files into JSONL that Pugio's file source can read.

CloudTrail delivers `{"Records": [event, ...]}` files, usually gzipped. Each event becomes
one line `{"raw": "<the event as a JSON string>"}`: one string column, so events with
different nested shapes still load with a fixed schema. Field extraction happens later in SQL.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any

from arsenal_core.errors import FatalError


def unpack_cloudtrail(src_dir: Path, out_file: Path) -> int:
    if not src_dir.is_dir():
        raise FatalError(f"source directory not found: {src_dir}")
    files = sorted([*src_dir.rglob("*.json"), *src_dir.rglob("*.json.gz")])
    if not files:
        raise FatalError(f"no CloudTrail files (*.json, *.json.gz) under {src_dir}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    temp = out_file.with_name(f".{out_file.name}.tmp")
    count = 0
    try:
        with temp.open("w", encoding="utf-8") as handle:
            for file in files:
                for record in _records(file):
                    raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    handle.write(json.dumps({"raw": raw}, ensure_ascii=False) + "\n")
                    count += 1
    except FatalError:
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, out_file)
    return count


def _records(file: Path) -> list[Any]:
    opener = gzip.open if file.suffix == ".gz" else open
    try:
        with opener(file, "rt", encoding="utf-8") as handle:
            document: Any = json.load(handle)
    except (OSError, ValueError) as e:
        raise FatalError(f"cannot read {file}: {e}") from e
    records: Any = document.get("Records") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise FatalError(f'{file}: not a CloudTrail log file (no "Records" array)')
    return records  # pyright: ignore[reportUnknownVariableType]
```

- [ ] **Step 4: Add the `unpack` command to `cli.py`**

Add import `from vigil.unpack import unpack_cloudtrail`. Then add below `coverage_command`:

```python
@app.command("unpack")
def unpack_command(
    src: Annotated[Path, typer.Argument(help="Directory of CloudTrail *.json / *.json.gz files.")],
    out: Annotated[Path, typer.Option("--out", help="JSONL file to write.")],
) -> None:
    """Unpack CloudTrail log files into JSONL, one event per line."""
    try:
        count = unpack_cloudtrail(src, out)
    except ArsenalError as e:
        raise _fail(e) from e
    _err(f"{count} events -> {out}")
    typer.echo(json.dumps({"events": count, "out": str(out)}))
```

- [ ] **Step 5: Run the package tests, then the whole suite**

Run: `uv run pytest packages/vigil -q --no-cov`
Expected: `43 passed`

Run: `uv run pytest -q`
Expected: all pass, total coverage at or above 80%. If `packages/vigil` pulls the total down, read the `term-missing` lines for `packages/vigil/src/vigil/*` and add a test for each uncovered branch before continuing.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format packages/vigil && uv run ruff check packages/vigil && uv run pyright packages/vigil
git add packages/vigil
git commit -m "feat(vigil): vigil unpack turns CloudTrail log files into JSONL"
```
