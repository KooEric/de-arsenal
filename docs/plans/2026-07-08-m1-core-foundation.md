# M0+M1: 신뢰성 코어 + 수집 수직 슬라이스 구현 계획

> **Historical implementation plan:** M0/M1은 merge된 main에서 완료됐다. 이 문서의
> 체크박스는 당시 TDD 작업 기록이며, 현재 릴리스 상태는
> [docs/04-implementation-plan.md](../04-implementation-plan.md)와
> [docs/release.md](../release.md)를 기준으로 한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 끊겨도 중복·누락 없이 재개되는 REST→Parquet 수집 파이프라인(`pugio run`)을 TDD로 완성한다.

**Architecture:** arsenal-core(에러 분류·결정적 ID·SQLite StateStore·YAML 스펙·재시도)를 먼저 쌓고, 그 위에 pugio(REST source, Parquet sink, Runner, CLI)를 수직 슬라이스로 올린다. at-least-once 실행 + 결정적 파일명 멱등 쓰기 = exactly-once 결과.

**Tech Stack:** Python 3.11+, uv workspace, Pydantic v2, sqlite3(WAL), httpx+respx, PyArrow, tenacity, Typer, pytest+hypothesis

**설계 노트(전 태스크 공통):**
- offset 페이지네이션은 unit을 미리 전부 열거할 수 없다(끝을 모름). 따라서 `Source.units()`는 **lazy 무한 generator**이고, `fetch()`가 `FetchResult(batch, exhausted)`로 끝을 알린다. Runner는 exhausted를 보면 종료한다. done unit은 fetch 없이 건너뛴다.
- 이 구조는 M2의 cursor 모드(커서 값=unit_key)와 호환된다.

---

### Task 0: 워크스페이스 부트스트랩 (M0)

**Files:**
- Create: `pyproject.toml`, `packages/arsenal-core/pyproject.toml`, `packages/pugio/pyproject.toml`, `packages/gladius/pyproject.toml`
- Create: `packages/arsenal-core/src/arsenal_core/__init__.py` (+ pugio, gladius 동일)
- Create: `packages/arsenal-core/tests/test_import.py` (+ pugio, gladius 동일)
- Create: `.github/workflows/ci.yml`, `.gitignore`

- [ ] **Step 1: 루트 pyproject.toml 작성**

```toml
[project]
name = "de-arsenal-workspace"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
arsenal-core = { workspace = true }

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "respx>=0.21",
    "hypothesis>=6",
    "ruff>=0.6",
    "pyright>=1.1",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pyright]
include = ["packages"]
typeCheckingMode = "strict"

[tool.pytest.ini_options]
testpaths = ["packages", "tests"]
addopts = "--cov=packages --cov-report=term-missing --cov-fail-under=80"

[tool.coverage.run]
omit = ["*/tests/*"]
```

- [ ] **Step 2: 패키지 3개 스켈레톤**

`packages/arsenal-core/pyproject.toml`:
```toml
[project]
name = "arsenal-core"
version = "0.1.0"
description = "Shared reliability core for DE Arsenal tools"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.7", "pyyaml>=6", "tenacity>=8.3"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

`packages/pugio/pyproject.toml` — name/description만 다르고 동일 구조, dependencies:
```toml
dependencies = ["arsenal-core", "httpx>=0.27", "pyarrow>=17", "typer>=0.12"]

[project.scripts]
pugio = "pugio.cli:app"
```

`packages/gladius/pyproject.toml` — dependencies:
```toml
dependencies = ["arsenal-core", "duckdb>=1.0", "pyarrow>=17", "typer>=0.12"]
```

각 패키지에 `src/<pkg>/__init__.py`:
```python
__version__ = "0.1.0"
```

각 패키지에 `tests/test_import.py` (예: arsenal-core):
```python
import arsenal_core


def test_import() -> None:
    assert arsenal_core.__version__
```

- [ ] **Step 3: 검증**

Run: `uv sync && uv run pytest`
Expected: 3 tests PASS (coverage 게이트는 코드가 없으므로 통과)

- [ ] **Step 4: CI + .gitignore**

`.gitignore`: `__pycache__/`, `.venv/`, `*.egg-info/`, `.arsenal/`, `data/`, `.coverage`, `dist/`

`.github/workflows/ci.yml`:
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install ${{ matrix.python }}
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: bootstrap uv workspace with core/pugio/gladius packages"
```

---

### Task 1: 에러 분류 체계

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/errors.py`
- Test: `packages/arsenal-core/tests/test_errors.py`

- [ ] **Step 1: 실패하는 테스트**

```python
import pytest

from arsenal_core.errors import (
    ArsenalError,
    AuthExpiredError,
    FatalError,
    RetryableError,
    classify_http_status,
)


def test_hierarchy() -> None:
    assert issubclass(AuthExpiredError, RetryableError)
    assert issubclass(RetryableError, ArsenalError)
    assert issubclass(FatalError, ArsenalError)
    assert not issubclass(FatalError, RetryableError)


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (401, AuthExpiredError),
        (429, RetryableError),
        (500, RetryableError),
        (503, RetryableError),
        (400, FatalError),
        (404, FatalError),
    ],
)
def test_classify_http_status(status: int, exc_type: type[ArsenalError]) -> None:
    assert classify_http_status(status) is exc_type


def test_2xx_is_not_an_error() -> None:
    assert classify_http_status(200) is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest packages/arsenal-core/tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arsenal_core.errors'`

- [ ] **Step 3: 구현**

```python
"""에러 분류 체계. 커넥터가 분류하고, 러너는 타입만 보고 행동한다."""


class ArsenalError(Exception):
    """모든 Arsenal 예외의 루트."""


class FatalError(ArsenalError):
    """재시도 무의미: 설정 오류, 4xx(401/429 제외), 계약 위반. 즉시 중단."""


class RetryableError(ArsenalError):
    """일시적 실패: 네트워크, 5xx, 429, lock 충돌. backoff 재시도."""


class AuthExpiredError(RetryableError):
    """인증 만료. 에러가 아니라 갱신 트리거 (M2에서 refresh hook 연결)."""


def classify_http_status(status: int) -> type[ArsenalError] | None:
    if status < 400:
        return None
    if status == 401:
        return AuthExpiredError
    if status == 429 or status >= 500:
        return RetryableError
    return FatalError
```

- [ ] **Step 4: 통과 확인** — Run: 위 명령. Expected: 9 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: error taxonomy with http status classification"`

---

### Task 2: 결정적 Unit ID

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/identity.py`
- Test: `packages/arsenal-core/tests/test_identity.py`

- [ ] **Step 1: 실패하는 테스트**

```python
import re

from hypothesis import given
from hypothesis import strategies as st

from arsenal_core.identity import unit_id

TEXT = st.text(min_size=1, max_size=50)


@given(TEXT, TEXT, TEXT)
def test_deterministic(p: str, s: str, k: str) -> None:
    assert unit_id(p, s, k) == unit_id(p, s, k)


@given(TEXT, TEXT, TEXT, TEXT)
def test_different_key_different_id(p: str, s: str, k1: str, k2: str) -> None:
    if k1 != k2:
        assert unit_id(p, s, k1) != unit_id(p, s, k2)


def test_format_16_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{16}", unit_id("pipe", "src", "offset=0"))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/arsenal-core/tests/test_identity.py -v` / Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현**

```python
"""결정적 Unit ID — 멱등성의 근거. 같은 unit은 언제 실행해도 같은 ID."""

import hashlib

_ID_LEN = 16


def unit_id(pipeline: str, source: str, unit_key: str) -> str:
    raw = f"{pipeline}\x1f{source}\x1f{unit_key}"  # \x1f 구분자로 경계 모호성 제거
    return hashlib.sha256(raw.encode()).hexdigest()[:_ID_LEN]
```

- [ ] **Step 4: 통과 확인** — Expected: 3 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: deterministic unit id"`

---

### Task 3: StateStore (SQLite WAL)

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/state/__init__.py`, `packages/arsenal-core/src/arsenal_core/state/store.py`
- Test: `packages/arsenal-core/tests/test_state_store.py`

- [ ] **Step 1: 실패하는 테스트**

```python
from pathlib import Path

from arsenal_core.state import StateStore, UnitSpec


def make_unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key=key, payload={"offset": 0})


def test_register_is_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.register(u)  # 두 번 등록해도
    assert store.status(u.unit_id) == "pending"
    assert store.counts("p") == {"pending": 1}


def test_mark_done_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = StateStore(db)
    u = make_unit("offset=0")
    store.register(u)
    store.mark_done(u.unit_id)
    store.close()
    reopened = StateStore(db)  # 프로세스 재시작 시뮬레이션
    assert reopened.status(u.unit_id) == "done"


def test_done_units_are_skippable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u1, u2 = make_unit("offset=0"), make_unit("offset=100")
    store.register(u1)
    store.register(u2)
    store.mark_done(u1.unit_id)
    assert store.is_done(u1.unit_id) is True
    assert store.is_done(u2.unit_id) is False


def test_mark_failed_records_error_and_attempts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_failed(u.unit_id, "boom")
    store.mark_failed(u.unit_id, "boom again")
    rec = store.get(u.unit_id)
    assert rec.status == "failed"
    assert rec.attempts == 2
    assert rec.last_error == "boom again"


def test_mark_done_records_unit_metrics(tmp_path: Path) -> None:
    """단위 지표는 비용 가시성의 원료 — P0부터 기록한다 (docs/07 참조)."""
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_done(u.unit_id, row_count=100, byte_count=2048, duration_ms=350)
    m = store.metrics(u.unit_id)
    assert (m.row_count, m.byte_count, m.duration_ms) == (100, 2048, 350)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/arsenal-core/tests/test_state_store.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`state/__init__.py`:
```python
from arsenal_core.state.store import StateStore, UnitMetrics, UnitRecord, UnitSpec

__all__ = ["StateStore", "UnitMetrics", "UnitRecord", "UnitSpec"]
```

`state/store.py`:
```python
"""SQLite(WAL) 영속 상태 저장소. 상태는 절대 메모리에만 두지 않는다."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arsenal_core.identity import unit_id as make_unit_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit_id    TEXT PRIMARY KEY,
    pipeline   TEXT NOT NULL,
    unit_key   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    row_count   INTEGER,
    byte_count  INTEGER,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_pipeline_status ON units(pipeline, status);
CREATE TABLE IF NOT EXISTS cursors (
    pipeline TEXT NOT NULL, source TEXT NOT NULL,
    cursor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline, source)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class UnitSpec:
    unit_id: str
    pipeline: str
    unit_key: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls, *, pipeline: str, source: str, unit_key: str, payload: dict[str, Any]
    ) -> "UnitSpec":
        return cls(
            unit_id=make_unit_id(pipeline, source, unit_key),
            pipeline=pipeline,
            unit_key=unit_key,
            payload=payload,
        )


@dataclass(frozen=True)
class UnitRecord:
    unit_id: str
    status: str
    attempts: int
    last_error: str | None


@dataclass(frozen=True)
class UnitMetrics:
    row_count: int | None
    byte_count: int | None
    duration_ms: int | None


class StateStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def register(self, unit: UnitSpec) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO units "
            "(unit_id, pipeline, unit_key, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (unit.unit_id, unit.pipeline, unit.unit_key, json.dumps(unit.payload), _now(), _now()),
        )
        self._conn.commit()

    def _set_status(self, uid: str, status: str, error: str | None = None) -> None:
        bump = 1 if status == "failed" else 0
        self._conn.execute(
            "UPDATE units SET status=?, last_error=?, attempts=attempts+?, updated_at=? "
            "WHERE unit_id=?",
            (status, error, bump, _now(), uid),
        )
        self._conn.commit()

    def mark_running(self, uid: str) -> None:
        self._set_status(uid, "running")

    def mark_done(
        self,
        uid: str,
        *,
        row_count: int | None = None,
        byte_count: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE units SET status='done', row_count=?, byte_count=?, duration_ms=?, "
            "updated_at=? WHERE unit_id=?",
            (row_count, byte_count, duration_ms, _now(), uid),
        )
        self._conn.commit()

    def mark_failed(self, uid: str, error: str) -> None:
        self._set_status(uid, "failed", error)

    def metrics(self, uid: str) -> UnitMetrics:
        row = self._conn.execute(
            "SELECT row_count, byte_count, duration_ms FROM units WHERE unit_id=?", (uid,)
        ).fetchone()
        if row is None:
            raise KeyError(uid)
        return UnitMetrics(*row)

    def status(self, uid: str) -> str | None:
        row = self._conn.execute("SELECT status FROM units WHERE unit_id=?", (uid,)).fetchone()
        return row[0] if row else None

    def is_done(self, uid: str) -> bool:
        return self.status(uid) == "done"

    def get(self, uid: str) -> UnitRecord:
        row = self._conn.execute(
            "SELECT unit_id, status, attempts, last_error FROM units WHERE unit_id=?", (uid,)
        ).fetchone()
        if row is None:
            raise KeyError(uid)
        return UnitRecord(*row)

    def counts(self, pipeline: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, count(*) FROM units WHERE pipeline=? GROUP BY status", (pipeline,)
        ).fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: 통과 확인** — Expected: 5 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: sqlite wal state store with idempotent registration and unit metrics"`

---

### Task 4: YAML 스펙 로더

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/spec/__init__.py`, `packages/arsenal-core/src/arsenal_core/spec/models.py`, `packages/arsenal-core/src/arsenal_core/spec/loader.py`
- Test: `packages/arsenal-core/tests/test_spec.py`

- [ ] **Step 1: 실패하는 테스트**

```python
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec import load_pipeline

VALID = """
name: github-issues
source:
  type: rest
  url: https://api.example.com/items
  headers: { Authorization: "Bearer ${TEST_TOKEN}" }
  pagination: { mode: offset, param: offset, size_param: limit, size: 100 }
sink:
  type: parquet
  path: ./data/items
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pipe.yaml"
    p.write_text(text)
    return p


def test_valid_spec_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok123")
    spec = load_pipeline(write(tmp_path, VALID))
    assert spec.name == "github-issues"
    assert spec.source.pagination.size == 100
    assert spec.source.headers["Authorization"] == "Bearer tok123"  # env 치환


def test_missing_required_field_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="source.url"):
        load_pipeline(write(tmp_path, "name: x\nsource: {type: rest}\nsink: {type: parquet, path: d}"))


def test_unknown_field_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok123")
    with pytest.raises(FatalError, match="tyop"):
        load_pipeline(write(tmp_path, VALID.replace("type: parquet", "type: parquet\n  tyop: 1")))


def test_missing_env_var_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_TOKEN", raising=False)
    with pytest.raises(FatalError, match="TEST_TOKEN"):
        load_pipeline(write(tmp_path, VALID))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/arsenal-core/tests/test_spec.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`spec/models.py`:
```python
"""파이프라인 YAML의 Pydantic 모델. 선언이 인터페이스다 — extra는 거부."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaginationSpec(_Frozen):
    mode: Literal["offset"]  # M2: "page", "cursor" 추가
    param: str = "offset"
    size_param: str = "limit"
    size: int = 100


class RateLimitSpec(_Frozen):
    rps: float


class SourceSpec(_Frozen):
    type: Literal["rest"]
    url: str
    headers: dict[str, str] = {}
    pagination: PaginationSpec
    rate_limit: RateLimitSpec | None = None
    encoding: str = "utf-8"


class SinkSpec(_Frozen):
    type: Literal["parquet"]  # M2: "duckdb", "postgres" 추가
    path: Path


class PipelineSpec(_Frozen):
    name: str
    state_dir: Path = Path(".arsenal")
    source: SourceSpec
    sink: SinkSpec
```

`spec/loader.py`:
```python
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import PipelineSpec

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        value = os.environ.get(m.group(1))
        if value is None:
            raise FatalError(f"environment variable not set: {m.group(1)}")
        return value

    return _ENV_PATTERN.sub(repl, text)


def load_pipeline(path: Path) -> PipelineSpec:
    raw = yaml.safe_load(_substitute_env(path.read_text()))
    try:
        return PipelineSpec.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise FatalError(f"invalid pipeline spec {path}:\n  " + "\n  ".join(lines)) from e
```

`spec/__init__.py`:
```python
from arsenal_core.spec.loader import load_pipeline
from arsenal_core.spec.models import PipelineSpec, SinkSpec, SourceSpec

__all__ = ["PipelineSpec", "SinkSpec", "SourceSpec", "load_pipeline"]
```

- [ ] **Step 4: 통과 확인** — Expected: 4 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: yaml pipeline spec with env substitution and friendly errors"`

---

### Task 5: 재시도 래퍼

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/retry.py`
- Test: `packages/arsenal-core/tests/test_retry.py`

- [ ] **Step 1: 실패하는 테스트**

```python
import pytest

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.retry import with_retry


def test_retryable_is_retried_until_success() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("transient")
        return "ok"

    assert with_retry(flaky, max_attempts=5, base_wait=0) == "ok"
    assert calls["n"] == 3


def test_fatal_is_not_retried() -> None:
    calls = {"n": 0}

    def bad() -> None:
        calls["n"] += 1
        raise FatalError("config")

    with pytest.raises(FatalError):
        with_retry(bad, max_attempts=5, base_wait=0)
    assert calls["n"] == 1


def test_exhausted_attempts_reraises() -> None:
    def always() -> None:
        raise RetryableError("down")

    with pytest.raises(RetryableError):
        with_retry(always, max_attempts=3, base_wait=0)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/arsenal-core/tests/test_retry.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

```python
"""Retryable만 재시도하는 backoff 래퍼. 엔진은 빌린다 — tenacity 사용."""

from collections.abc import Callable
from typing import TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from arsenal_core.errors import RetryableError

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_WAIT = 1.0  # seconds


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_wait: float = DEFAULT_BASE_WAIT,
) -> T:
    wrapped = retry(
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=base_wait, max=60),
        reraise=True,
    )(fn)
    return wrapped()
```

- [ ] **Step 4: 통과 확인** — Expected: 3 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: retry wrapper that retries only retryable errors"`

---

### Task 6: Source 프로토콜 + REST 소스 (offset)

**Files:**
- Create: `packages/pugio/src/pugio/sources/__init__.py`, `packages/pugio/src/pugio/sources/base.py`, `packages/pugio/src/pugio/sources/rest.py`
- Test: `packages/pugio/tests/test_rest_source.py`

- [ ] **Step 1: 실패하는 테스트**

```python
import httpx
import pytest
import respx

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PaginationSpec, SourceSpec
from pugio.sources.rest import RestSource

SPEC = SourceSpec(
    type="rest",
    url="https://api.test/items",
    pagination=PaginationSpec(mode="offset", param="offset", size_param="limit", size=2),
)


def make_source() -> RestSource:
    return RestSource(SPEC, pipeline="p", client=httpx.Client())


def test_units_are_lazy_and_deterministic() -> None:
    from itertools import islice

    units = list(islice(make_source().units(), 2))
    assert units[0].unit_key == "offset=0"
    assert units[1].unit_key == "offset=2"
    # 같은 스펙 → 같은 unit_id (멱등의 근거)
    assert units[0].unit_id == next(iter(make_source().units())).unit_id


@respx.mock
def test_fetch_full_page_not_exhausted() -> None:
    respx.get("https://api.test/items", params={"offset": 0, "limit": 2}).respond(
        json=[{"id": 1}, {"id": 2}]
    )
    src = make_source()
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.batch is not None
    assert result.batch.num_rows == 2
    assert result.exhausted is False


@respx.mock
def test_fetch_partial_page_is_exhausted() -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 5}])
    result = make_source().fetch(next(iter(make_source().units())))
    assert result.batch is not None and result.batch.num_rows == 1
    assert result.exhausted is True


@respx.mock
def test_fetch_empty_page_exhausted_no_batch() -> None:
    respx.get("https://api.test/items").respond(json=[])
    result = make_source().fetch(next(iter(make_source().units())))
    assert result.batch is None
    assert result.exhausted is True


@respx.mock
@pytest.mark.parametrize(("status", "exc"), [(500, RetryableError), (404, FatalError)])
def test_http_errors_are_classified(status: int, exc: type[Exception]) -> None:
    respx.get("https://api.test/items").respond(status_code=status)
    with pytest.raises(exc):
        make_source().fetch(next(iter(make_source().units())))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/pugio/tests/test_rest_source.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`sources/base.py`:
```python
"""Source 커넥터 계약. 커넥터는 재시도·상태·멱등을 모른다."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa

from arsenal_core.state import UnitSpec


@dataclass(frozen=True)
class FetchResult:
    batch: pa.RecordBatch | None  # None = 데이터 없음
    exhausted: bool  # True = 이 unit이 마지막, 더 이상 unit 없음


class Source(Protocol):
    def units(self) -> Iterator[UnitSpec]:
        """Unit of Work를 lazy하게 열거. 끝을 모르면 무한 generator."""
        ...

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """unit 하나를 Arrow로. 실패는 분류된 예외(errors.py)로 던진다."""
        ...
```

`sources/rest.py`:
```python
"""REST 소스 — offset 페이지네이션 (M2: page/cursor/encoding/rate limit/auth 추가)."""

import itertools
from collections.abc import Iterator

import httpx
import pyarrow as pa

from arsenal_core.errors import classify_http_status
from arsenal_core.spec.models import SourceSpec
from arsenal_core.state import UnitSpec

from pugio.sources.base import FetchResult


class RestSource:
    def __init__(self, spec: SourceSpec, *, pipeline: str, client: httpx.Client) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client

    def units(self) -> Iterator[UnitSpec]:
        size = self._spec.pagination.size
        for offset in itertools.count(0, size):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"offset={offset}",
                payload={"offset": offset, "limit": size},
            )

    def fetch(self, unit: UnitSpec) -> FetchResult:
        p = self._spec.pagination
        resp = self._client.get(
            self._spec.url,
            params={p.param: unit.payload["offset"], p.size_param: unit.payload["limit"]},
            headers=self._spec.headers,
        )
        exc_type = classify_http_status(resp.status_code)
        if exc_type is not None:
            raise exc_type(f"GET {self._spec.url} -> {resp.status_code}: {resp.text[:200]}")
        rows: list[dict[str, object]] = resp.json()
        exhausted = len(rows) < p.size
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        return FetchResult(batch=batch, exhausted=exhausted)
```

`sources/__init__.py`:
```python
from pugio.sources.base import FetchResult, Source
from pugio.sources.rest import RestSource

__all__ = ["FetchResult", "RestSource", "Source"]
```

- [ ] **Step 4: 통과 확인** — Expected: 7 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: rest source with offset pagination and error classification"`

---

### Task 7: Sink 프로토콜 + Parquet sink (결정적 파일명)

**Files:**
- Create: `packages/pugio/src/pugio/sinks/__init__.py`, `packages/pugio/src/pugio/sinks/base.py`, `packages/pugio/src/pugio/sinks/parquet.py`
- Test: `packages/pugio/tests/test_parquet_sink.py`

- [ ] **Step 1: 실패하는 테스트**

```python
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.sinks.parquet import ParquetSink


def batch(ids: list[int]) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist([{"id": i} for i in ids])


def unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key=key, payload={})


def test_writes_file_named_by_unit_id(tmp_path: Path) -> None:
    sink = ParquetSink(tmp_path / "out")
    u = unit("offset=0")
    sink.write(u, batch([1, 2]))
    assert (tmp_path / "out" / f"{u.unit_id}.parquet").exists()


def test_write_twice_same_unit_is_idempotent(tmp_path: Path) -> None:
    sink = ParquetSink(tmp_path / "out")
    u = unit("offset=0")
    sink.write(u, batch([1, 2]))
    sink.write(u, batch([1, 2]))  # 재실행 시뮬레이션
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    assert pq.read_table(files[0]).num_rows == 2  # 중복 없음


def test_no_partial_file_visible(tmp_path: Path) -> None:
    """tmp에 쓰고 atomic rename — .tmp 잔여물이 결과로 보이면 안 된다."""
    sink = ParquetSink(tmp_path / "out")
    sink.write(unit("offset=0"), batch([1]))
    assert not list((tmp_path / "out").glob("*.tmp"))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/pugio/tests/test_parquet_sink.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`sinks/base.py`:
```python
from typing import Protocol

import pyarrow as pa

from arsenal_core.state import UnitSpec


class Sink(Protocol):
    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """멱등하게 쓴다. 같은 unit 재호출 시 결과가 변하지 않아야 한다."""
        ...
```

`sinks/parquet.py`:
```python
"""객체 스토리지형 멱등 전략: 결정적 파일명 + atomic replace.

같은 unit은 같은 파일명 → 재실행 = 덮어쓰기 = 중복 없음.
"""

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec


class ParquetSink:
    def __init__(self, path: Path) -> None:
        self._dir = path

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        final = self._dir / f"{unit.unit_id}.parquet"
        tmp = self._dir / f"{unit.unit_id}.parquet.tmp"
        pq.write_table(pa.Table.from_batches([batch]), tmp)
        os.replace(tmp, final)  # POSIX atomic — 부분 쓰기가 결과로 보이지 않음
```

`sinks/__init__.py`:
```python
from pugio.sinks.base import Sink
from pugio.sinks.parquet import ParquetSink

__all__ = ["ParquetSink", "Sink"]
```

- [ ] **Step 4: 통과 확인** — Expected: 3 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: parquet sink with deterministic filename idempotency"`

---

### Task 8: Runner — 수집 루프

**Files:**
- Create: `packages/pugio/src/pugio/runner.py`
- Test: `packages/pugio/tests/test_runner.py`

- [ ] **Step 1: 실패하는 테스트**

```python
from pathlib import Path

import httpx
import pytest
import respx

from arsenal_core.spec.models import PaginationSpec, PipelineSpec, SinkSpec, SourceSpec
from pugio.runner import run_pipeline


def make_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="t",
        state_dir=tmp_path / ".arsenal",
        source=SourceSpec(
            type="rest",
            url="https://api.test/items",
            pagination=PaginationSpec(mode="offset", size=2),
        ),
        sink=SinkSpec(type="parquet", path=tmp_path / "out"),
    )


def mock_pages(pages: list[list[dict[str, int]]]) -> None:
    """offset 파라미터에 따라 해당 페이지를 응답하는 목 API."""

    def responder(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        idx = offset // 2
        body = pages[idx] if idx < len(pages) else []
        return httpx.Response(200, json=body)

    respx.get("https://api.test/items").mock(side_effect=responder)


@respx.mock
def test_happy_path_collects_all_pages(tmp_path: Path) -> None:
    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]])
    report = run_pipeline(make_spec(tmp_path))
    assert report.fetched == 3
    assert report.written == 3
    assert len(list((tmp_path / "out").glob("*.parquet"))) == 3


@respx.mock
def test_rerun_after_completion_is_noop(tmp_path: Path) -> None:
    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}]])
    spec = make_spec(tmp_path)
    run_pipeline(spec)
    report2 = run_pipeline(spec)  # 재실행은 정상 동작
    assert report2.written == 0
    assert report2.skipped >= 1  # 완료분은 건너뜀


class SimulatedCrash(Exception):
    pass


@respx.mock
def test_crash_and_resume_no_dup_no_loss(tmp_path: Path) -> None:
    """시나리오 B: 도중에 죽어도 재실행하면 이어서. 중복 0, 누락 0."""
    import pyarrow.parquet as pq

    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]])
    spec = make_spec(tmp_path)

    def crash_after_first(done_count: int) -> None:
        if done_count >= 1:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        run_pipeline(spec, on_unit_complete=crash_after_first)

    report = run_pipeline(spec)  # 같은 명령 그대로 재실행
    assert report.skipped == 1  # 완료했던 1개는 다시 받지 않음
    files = sorted((tmp_path / "out").glob("*.parquet"))
    all_ids = sorted(
        row["id"] for f in files for row in pq.read_table(f).to_pylist()
    )
    assert all_ids == [1, 2, 3, 4, 5]  # 중복 0, 누락 0
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/pugio/tests/test_runner.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`runner.py`:
```python
"""수집 루프. 사용자가 보는 것은 선언뿐 — 재시도·체크포인트·재개는 여기가 흡수한다."""

import functools
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from arsenal_core.retry import with_retry
from arsenal_core.spec.models import PipelineSpec
from arsenal_core.state import StateStore
from pugio.sinks.parquet import ParquetSink
from pugio.sources.rest import RestSource


@dataclass(frozen=True)
class RunReport:
    fetched: int
    written: int
    skipped: int


def run_pipeline(
    spec: PipelineSpec,
    *,
    on_unit_complete: Callable[[int], None] | None = None,  # 테스트 훅 (크래시 주입)
    max_attempts: int = 5,
) -> RunReport:
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    source = RestSource(spec.source, pipeline=spec.name, client=httpx.Client())
    sink = ParquetSink(spec.sink.path)
    fetched = written = skipped = done_count = 0

    try:
        for unit in source.units():
            store.register(unit)
            if store.is_done(unit.unit_id):
                skipped += 1
                continue
            store.mark_running(unit.unit_id)
            started = time.monotonic()
            try:
                result = with_retry(
                    functools.partial(source.fetch, unit), max_attempts=max_attempts
                )
            except Exception as e:
                store.mark_failed(unit.unit_id, str(e))
                raise
            fetched += 1
            if result.batch is not None:
                sink.write(unit, result.batch)  # 멱등 쓰기 먼저,
                written += 1
            store.mark_done(  # done 마킹은 그 다음 (핵심 불변식)
                unit.unit_id,
                row_count=result.batch.num_rows if result.batch is not None else 0,
                byte_count=result.batch.nbytes if result.batch is not None else 0,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            done_count += 1
            if on_unit_complete is not None:
                on_unit_complete(done_count)
            if result.exhausted:
                break
        return RunReport(fetched=fetched, written=written, skipped=skipped)
    finally:
        store.close()
```

- [ ] **Step 4: 통과 확인** — Expected: 3 PASS (재개 테스트 포함)

주의: `test_rerun_after_completion_is_noop`이 실패한다면 원인은 "완주 후 재실행 시 마지막 partial 페이지 다음을 다시 fetch"하는 설계 특성이다. skipped 카운트는 done 스킵 수, 마지막 경계 재확인 fetch 1회는 허용된다(재개 비용 ≤ 청크 1개). assertion이 이 계약과 일치하는지 확인할 것.

- [ ] **Step 5: Commit** — `git commit -am "feat: runner with resume-by-default collection loop"`

---

### Task 9: CLI

**Files:**
- Create: `packages/pugio/src/pugio/cli.py`
- Test: `packages/pugio/tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트**

```python
from pathlib import Path

import respx
from typer.testing import CliRunner

from pugio.cli import app

runner = CliRunner()

YAML = """
name: cli-test
state_dir: {state_dir}
source:
  type: rest
  url: https://api.test/items
  pagination: {{ mode: offset, size: 2 }}
sink:
  type: parquet
  path: {out}
"""


def write_spec(tmp_path: Path) -> Path:
    p = tmp_path / "pipe.yaml"
    p.write_text(YAML.format(state_dir=tmp_path / ".arsenal", out=tmp_path / "out"))
    return p


@respx.mock
def test_run_command(tmp_path: Path) -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 1}])
    result = runner.invoke(app, ["run", str(write_spec(tmp_path))])
    assert result.exit_code == 0
    assert "written=1" in result.output


@respx.mock
def test_status_command(tmp_path: Path) -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 1}])
    spec = write_spec(tmp_path)
    runner.invoke(app, ["run", str(spec)])
    result = runner.invoke(app, ["status", str(spec)])
    assert result.exit_code == 0
    assert "done" in result.output


def test_invalid_spec_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    result = runner.invoke(app, ["run", str(bad)])
    assert result.exit_code == 1
    assert "source" in result.output  # 어떤 필드가 문제인지 보인다
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest packages/pugio/tests/test_cli.py -v` / Expected: FAIL

- [ ] **Step 3: 구현**

`cli.py`:
```python
from pathlib import Path

import typer

from arsenal_core.errors import ArsenalError
from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore
from pugio.runner import run_pipeline

app = typer.Typer(help="Pugio — 수집·전송. 재실행은 곧 재개다.")


@app.command()
def run(spec_path: Path) -> None:
    """파이프라인 실행. 중단됐던 실행은 자동으로 이어서 한다."""
    try:
        spec = load_pipeline(spec_path)
        report = run_pipeline(spec)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"done: fetched={report.fetched} written={report.written} skipped={report.skipped}")


@app.command()
def status(spec_path: Path) -> None:
    """unit 상태 요약."""
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        counts = store.counts(spec.name)
    finally:
        store.close()
    if not counts:
        typer.echo("no runs yet")
        return
    for state, n in sorted(counts.items()):
        typer.echo(f"{state}: {n}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: 통과 확인** — Expected: 3 PASS
- [ ] **Step 5: Commit** — `git commit -am "feat: pugio cli with run and status commands"`

---

### Task 10: 예제 + 마무리 검증

**Files:**
- Create: `examples/github-issues.yaml`
- Modify: `README.md` (시작하기 섹션을 실제 동작 기준으로 갱신)

- [ ] **Step 1: 예제 작성**

`examples/github-issues.yaml`:
```yaml
name: github-issues
source:
  type: rest
  url: https://api.github.com/repos/duckdb/duckdb/issues
  headers: { Authorization: "Bearer ${GITHUB_TOKEN}" }
  pagination: { mode: offset, param: page, size_param: per_page, size: 100 }
sink:
  type: parquet
  path: ./data/issues
```

- [ ] **Step 2: 전체 검증**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`
Expected: 전부 통과, coverage ≥ 80%

- [ ] **Step 3: 수동 신뢰성 검증 (1회)**

```bash
GITHUB_TOKEN=... uv run pugio run examples/github-issues.yaml   # 실행 도중 Ctrl-C
uv run pugio status examples/github-issues.yaml                  # done N, running 1 확인
GITHUB_TOKEN=... uv run pugio run examples/github-issues.yaml   # 이어서 완주 확인
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: m1 complete — resumable rest-to-parquet collection"
```

---

## M1 완료 기준 (DoD) 최종 체크

- [ ] `test_crash_and_resume_no_dup_no_loss` 통과 — 중복 0, 누락 0 자동 증명
- [ ] 완주 후 재실행이 no-op (skipped만 증가)
- [ ] CI 초록 (lint, typecheck, pytest, coverage ≥ 80%)
- [ ] 예제 YAML로 수동 재현 가능
- [ ] M2 상세 계획 작성 착수 (`docs/plans/` — 이 문서와 같은 형식)
