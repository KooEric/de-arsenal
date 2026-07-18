# M2: Pugio P0 완성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **초안 상태**: M1 완료 전에 전체 틀을 잡기 위해 작성됨. 시그니처는 스켈레톤(커밋 c05de04) 기준. **M2 착수 시 M1의 실제 구현과 대조해 갱신 후 실행한다.** 갱신 시 이 블록을 제거.

**Goal:** Pugio를 실전 투입 가능하게 — 페이지네이션 3종, 인코딩, rate limit, 인증 자동 갱신, DB 멱등 sink, 검증 게이트, DLQ.

**Architecture:** M1의 units()/fetch() 계약을 유지하며 확장한다. cursor 모드는 `FetchResult.next_cursor` + StateStore cursors 테이블로 재개를 지원. 인증 만료는 러너가 `AuthExpiredError`를 잡아 refresh 후 같은 unit을 재시도. DB sink는 "staging 적재 → 키 기준 delete+insert"를 한 트랜잭션으로 묶어 멱등을 보장.

**Tech Stack:** httpx+respx, pyarrow.compute(벡터화 검증), duckdb(Arrow 직접 등록), psycopg3, testcontainers

---

### Task 2.0: 스펙 확장 — M2 필드 전체를 모델에 선언

**Files:**
- Modify: `packages/arsenal-core/src/arsenal_core/spec/models.py`
- Modify: `packages/pugio/src/pugio/sources/base.py` (FetchResult)
- Test: `packages/arsenal-core/tests/test_spec_m2.py`

- [ ] **Step 1: 실패하는 테스트** — 대표 케이스만 발췌(전체는 모드×싱크 파라미터라이즈):

```python
import pytest
from pydantic import TypeAdapter

from arsenal_core.spec.models import AuthSpec, PaginationSpec, SinkSpec, ValidateRule


def test_cursor_pagination_requires_cursor_path() -> None:
    with pytest.raises(ValueError, match="cursor_path"):
        PaginationSpec(mode="cursor", cursor_param="after")  # cursor_path 누락


def test_sink_union_discriminates_on_type() -> None:
    sink = TypeAdapter(SinkSpec).validate_python(
        {"type": "duckdb", "path": "out.db", "table": "issues", "merge_key": ["id"]}
    )
    assert sink.merge_key == ["id"]


def test_oauth2_auth_spec() -> None:
    a = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://auth.test/token",
        client_id_env="CID", client_secret_env="CSECRET",
    )
    assert a.expiry_buffer_s == 60  # 만료 60초 전 선제 갱신 기본값


def test_validate_rule_shape() -> None:
    r = ValidateRule(field="id", not_null=True, unique=True)
    assert r.min is None and r.max is None
```

- [ ] **Step 2: 구현** — 모델 확장 (기존 필드는 절대 변경하지 않는다 — 하위 호환):

```python
class PaginationSpec(_Frozen):
    mode: Literal["offset", "page", "cursor", "link"]  # link = RFC 5988 Link 헤더
    # offset/page 공용
    param: str = "offset"           # page 모드에선 페이지 번호 파라미터명
    size_param: str = "limit"
    size: int = 100
    start_page: int = 1             # page 모드 시작 번호
    # cursor 전용
    cursor_param: str | None = None      # 요청 쿼리 파라미터명
    cursor_path: str | None = None       # 응답에서 다음 커서 위치, 점 표기 (예: "meta.next")

    @model_validator(mode="after")
    def _cursor_fields_required(self) -> "PaginationSpec":
        if self.mode == "cursor" and not (self.cursor_param and self.cursor_path):
            raise ValueError("cursor mode requires cursor_param and cursor_path")
        return self


class AuthSpec(_Frozen):
    type: Literal["static_token", "oauth2_client_credentials"]
    token_env: str | None = None         # static_token
    token_url: str | None = None         # oauth2
    client_id_env: str | None = None
    client_secret_env: str | None = None
    expiry_buffer_s: int = 60


class ValidateRule(_Frozen):
    field: str
    not_null: bool = False
    unique: bool = False
    min: float | None = None
    max: float | None = None


class ValidateSpec(_Frozen):
    rules: list[ValidateRule]
    on_violation: Literal["block", "quarantine", "warn"] = "quarantine"


class ParquetSinkSpec(_Frozen):
    type: Literal["parquet"]
    path: Path


class DuckDBSinkSpec(_Frozen):
    type: Literal["duckdb"]
    path: Path                            # .db 파일
    table: str
    merge_key: list[str]                  # 멱등 MERGE 키


class PostgresSinkSpec(_Frozen):
    type: Literal["postgres"]
    dsn_env: str                          # DSN은 환경변수로만 (비밀 원칙)
    table: str
    merge_key: list[str]


SinkSpec = Annotated[
    ParquetSinkSpec | DuckDBSinkSpec | PostgresSinkSpec, Field(discriminator="type")
]

class SourceSpec(_Frozen):
    ...  # 기존 필드 유지
    auth: AuthSpec | None = None

class PipelineSpec(_Frozen):
    ...  # 기존 필드 유지
    # 주의: 필드명 validate는 pydantic BaseModel.validate와 충돌 — alias로 우회
    validation: ValidateSpec | None = Field(None, alias="validate")
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
```

YAML에서는 계획대로 `validate:` 키를 쓰고, 코드에서는 `spec.validation`으로 접근한다.

`FetchResult` 확장 (기본값 덕에 M1 코드 무수정):

```python
@dataclass(frozen=True)
class FetchResult:
    batch: pa.RecordBatch | None
    exhausted: bool
    next_cursor: str | None = None   # cursor 모드: 응답에서 추출한 다음 커서
```

- [ ] **Step 3: 통과·정합 확인 후 커밋** — 기존 M1 테스트 전체가 그대로 통과해야 한다(하위 호환 증명). `git commit -m "feat: m2 spec fields — pagination modes, auth, validate, sink union"`

---

### Task 2.1: page 페이지네이션

**Files:**
- Modify: `packages/pugio/src/pugio/sources/rest.py`
- Test: `packages/pugio/tests/test_rest_pagination.py`

- [ ] **Step 1: 테스트** — `mode: page`는 offset 대신 `start_page`부터 1씩 증가하는 페이지 번호를 보낸다:

```python
@respx.mock
def test_page_mode_sends_incrementing_page_numbers() -> None:
    seen: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params)["page"])
        seen.append(page)
        return httpx.Response(200, json=[{"id": page}] if page <= 2 else [])

    respx.get("https://api.test/items").mock(side_effect=responder)
    spec = SourceSpec(
        type="rest", url="https://api.test/items",
        pagination=PaginationSpec(mode="page", param="page", size_param="per_page", size=1),
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    for unit in src.units():
        if src.fetch(unit).exhausted:
            break
    assert seen == [1, 2, 3]
```

- [ ] **Step 2: 구현** — `units()`에서 mode 분기: unit_key=`"page={n}"`, payload=`{"page": n, "size": size}`. `fetch()`의 파라미터 구성을 `_request_params(unit)` 헬퍼로 추출해 모드별 매핑:

```python
def _request_params(self, unit: UnitSpec) -> dict[str, str | int]:
    p = self._spec.pagination
    if p.mode == "offset":
        return {p.param: unit.payload["offset"], p.size_param: p.size}
    if p.mode == "page":
        return {p.param: unit.payload["page"], p.size_param: p.size}
    return {p.cursor_param: unit.payload["cursor"], p.size_param: p.size}  # cursor
```

- [ ] **Step 3: 커밋** — `git commit -m "feat: page pagination mode"`

---

### Task 2.2: cursor 페이지네이션 + 커서 영속화

cursor 모드는 다음 unit이 이전 응답에 의존한다 — 미리 열거 불가. 설계:
- `units()`는 현재 커서로 unit 하나를 yield하고, `fetch()`가 저장해 둔 `self._pending_cursor`로 다음 unit을 만든다.
- 재개: 러너가 `StateStore.get_cursor()`로 마지막 **완료** 커서를 읽어 소스 생성 시 주입. done-skip 대신 frontier에서 시작.
- 영속 시점: 러너가 `mark_done` 직후 `set_cursor(next_cursor)` — "쓰기 성공 → done → 커서 전진" 순서 유지.

**Files:**
- Modify: `packages/pugio/src/pugio/sources/rest.py`, `packages/pugio/src/pugio/runner.py`
- Modify: `packages/arsenal-core/src/arsenal_core/state/store.py` (get_cursor/set_cursor)
- Test: `packages/pugio/tests/test_rest_cursor.py`, `packages/arsenal-core/tests/test_state_cursor.py`

- [ ] **Step 1: StateStore 커서 API 테스트**

```python
def test_cursor_roundtrip_and_upsert(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    assert store.get_cursor("p", "src") is None
    store.set_cursor("p", "src", "abc")
    store.set_cursor("p", "src", "def")  # upsert
    assert store.get_cursor("p", "src") == "def"
```

- [ ] **Step 2: StateStore 구현** — `INSERT INTO cursors ... ON CONFLICT(pipeline, source) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at`

- [ ] **Step 3: RestSource cursor 테스트**

```python
@respx.mock
def test_cursor_mode_follows_next_and_resumes() -> None:
    pages = {"": (["a1"], "c1"), "c1": (["a2"], "c2"), "c2": (["a3"], None)}

    def responder(request: httpx.Request) -> httpx.Response:
        cur = dict(request.url.params).get("after", "")
        rows, nxt = pages[cur]
        return httpx.Response(200, json={"data": [{"v": r} for r in rows], "meta": {"next": nxt}})

    respx.get("https://api.test/items").mock(side_effect=responder)
    spec = SourceSpec(
        type="rest", url="https://api.test/items",
        pagination=PaginationSpec(
            mode="cursor", cursor_param="after", cursor_path="meta.next", size=10
        ),
        record_path="data",
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client(), initial_cursor=None)
    results = []
    for unit in src.units():
        r = src.fetch(unit)
        results.append(r)
        if r.exhausted:
            break
    assert [r.next_cursor for r in results] == ["c1", "c2", None]
    # 재개: c1에서 시작하면 c1 이후만
    src2 = RestSource(spec, pipeline="p", client=httpx.Client(), initial_cursor="c1")
    first = next(iter(src2.units()))
    assert first.payload["cursor"] == "c1"
```

주의: cursor 응답은 보통 envelope(JSON 객체) 안에 배열이 있다 — `record_path` 필드(점 표기)를 SourceSpec에 함께 추가한다(offset/page에도 유효, 기본 None=응답 자체가 배열).

- [ ] **Step 4: 구현** — `_dig(obj, "meta.next")` 점 표기 헬퍼, units()의 cursor 분기(위 설계), fetch()에서 next_cursor 추출·`self._pending_cursor` 갱신, `exhausted = next_cursor is None`.

- [ ] **Step 5: 러너 연동 테스트** — 3페이지 중 2페이지 완료 후 크래시 → 재실행이 `after=c1`부터 시작함을 respx 호출 기록으로 검증.

- [ ] **Step 6: 러너 구현** — cursor 모드일 때: `initial = store.get_cursor(...)`로 소스 생성, `mark_done` 후 `store.set_cursor(pipeline, url, result.next_cursor)` (None이면 미갱신).

- [ ] **Step 7: link 모드 — cursor 메커니즘 재사용** — GitHub·Shopify·GitLab의 표준(RFC 5988). "커서 값 = 다음 페이지 전체 URL"인 cursor 모드로 취급한다. 차이는 추출 위치뿐: 응답 본문(`cursor_path`)이 아니라 `Link` 헤더의 `rel="next"`.

```python
_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')

def _next_from_link_header(resp: httpx.Response) -> str | None:
    m = _LINK_NEXT.search(resp.headers.get("link", ""))
    return m.group(1) if m else None
```

fetch()는 link 모드에서 unit.payload["cursor"]가 있으면 그 URL로 직접 GET(파라미터 재구성 없음). 테스트: GitHub 스타일 Link 헤더 3페이지 목 + 재개 검증.

- [ ] **Step 8: 커밋** — `git commit -m "feat: cursor and link pagination with persistent resume"`

---

### Task 2.3: 인코딩 (euc-kr 등)

**Files:**
- Modify: `packages/pugio/src/pugio/sources/rest.py`
- Test: `packages/pugio/tests/test_rest_encoding.py` (+ `tests/fixtures/euc_kr_body.py`)

- [ ] **Step 1: 테스트** — euc-kr 바이트 응답을 지정 인코딩으로 디코드:

```python
@respx.mock
def test_euc_kr_response_decoded() -> None:
    body = '[{"이름": "김철수"}]'.encode("euc-kr")
    respx.get("https://api.test/items").respond(
        content=body, headers={"content-type": "application/json"}  # charset 미표기 서버
    )
    spec = SourceSpec(type="rest", url="https://api.test/items", encoding="euc-kr",
                      pagination=PaginationSpec(mode="offset", size=10))
    result = RestSource(spec, pipeline="p", client=httpx.Client()).fetch(...)
    assert result.batch.to_pylist()[0]["이름"] == "김철수"
```

- [ ] **Step 2: 구현** — `resp.json()` 대신 `json.loads(resp.content.decode(self._spec.encoding))`. 잘못된 인코딩 선언은 `FatalError`로 변환(UnicodeDecodeError 래핑).

- [ ] **Step 3: 커밋** — `git commit -m "feat: source encoding support for non-utf8 apis"`

---

### Task 2.4: Rate limiter — 토큰 버킷 + 429 적응

**Files:**
- Create: `packages/arsenal-core/src/arsenal_core/ratelimit.py`
- Modify: `packages/pugio/src/pugio/sources/rest.py`
- Test: `packages/arsenal-core/tests/test_ratelimit.py`

- [ ] **Step 1: 테스트** — 시간은 주입한다 (docs/05 "sleep 없는 테스트"):

```python
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []
    def monotonic(self) -> float:
        return self.now
    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_token_bucket_throttles_to_rps() -> None:
    clock = FakeClock()
    tb = TokenBucket(rps=2.0, clock=clock)
    for _ in range(4):
        tb.acquire()
    # 2 rps → 4번째 호출까지 총 대기 ≈ 1.0s (첫 호출은 즉시)
    assert sum(clock.slept) == pytest.approx(1.0, abs=0.01)


def test_penalize_respects_retry_after() -> None:
    clock = FakeClock()
    tb = TokenBucket(rps=10.0, clock=clock)
    tb.penalize(30.0)  # 429 Retry-After: 30
    tb.acquire()
    assert clock.slept and clock.slept[0] >= 30.0
```

- [ ] **Step 2: 구현**

```python
class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class TokenBucket:
    """rps 상한 토큰 버킷. penalize()로 429 Retry-After를 흡수한다."""

    def __init__(self, rps: float, *, clock: Clock | None = None, burst: int = 1) -> None:
        self._interval = 1.0 / rps
        self._clock = clock or _MonotonicClock()
        self._tokens = float(burst)
        self._last = self._clock.monotonic()
        self._penalty_until = 0.0

    def acquire(self) -> None:
        now = self._clock.monotonic()
        if now < self._penalty_until:
            self._clock.sleep(self._penalty_until - now)
            now = self._clock.monotonic()
        self._tokens = min(1.0, self._tokens + (now - self._last) / self._interval)
        self._last = now
        if self._tokens < 1.0:
            self._clock.sleep((1.0 - self._tokens) * self._interval)
            self._tokens = 1.0
            self._last = self._clock.monotonic()
        self._tokens -= 1.0

    def penalize(self, seconds: float) -> None:
        self._penalty_until = self._clock.monotonic() + seconds
```

- [ ] **Step 3: RestSource 연동** — `spec.rate_limit` 있으면 fetch 앞에서 `acquire()`. 429 응답이면 `Retry-After` 헤더를 읽어 `penalize()` 후 `RetryableError` — tenacity 재시도가 다음 acquire에서 자연 대기.

- [ ] **Step 4: 커밋** — `git commit -m "feat: token bucket rate limiter with 429 adaptation"`

---

### Task 2.5: 인증 — static / oauth2 / 401 갱신 루프

**Files:**
- Modify: `packages/pugio/src/pugio/auth/__init__.py` (구현체 추가)
- Modify: `packages/pugio/src/pugio/sources/rest.py`, `packages/pugio/src/pugio/runner.py`
- Test: `packages/pugio/tests/test_auth.py`

- [ ] **Step 1: 테스트** — 핵심 시나리오 C(만료는 갱신 트리거):

```python
@respx.mock
def test_401_triggers_refresh_and_same_unit_retry(tmp_path: Path) -> None:
    calls = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers["Authorization"] == "Bearer old":
            return httpx.Response(401)
        return httpx.Response(200, json=[{"id": 1}])

    respx.get("https://api.test/items").mock(side_effect=responder)
    provider = FlippingProvider(first="old", after_refresh="new")  # 테스트 더블
    report = run_pipeline(make_spec(tmp_path), auth_provider=provider)
    assert provider.refresh_count == 1
    assert report.written == 1          # 같은 unit이 새 토큰으로 성공


def test_oauth2_refreshes_before_expiry() -> None:
    # token endpoint를 respx로 목킹, expires_in=100, buffer 60 → 40초 시점엔 재발급 안 함,
    # 41초 이후 첫 headers() 호출에서 재발급. FakeClock 주입으로 검증.
    ...
```

- [ ] **Step 2: 구현**

```python
class StaticTokenAuth:
    def __init__(self, token_env: str) -> None: ...
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ[self._env]}"}
    def refresh(self) -> None:  # 정적 토큰은 갱신 불가 — 만료면 Fatal로 승격
        raise FatalError("static token expired; rotate the secret")


class OAuth2ClientCredentials:
    """만료 expiry_buffer_s 전에 선제 재발급. 401 수신 시 refresh()로 강제 재발급."""
    def __init__(self, spec: AuthSpec, *, client: httpx.Client, clock: Clock | None = None): ...
    def headers(self) -> dict[str, str]: ...   # 만료 임박 시 내부에서 _fetch_token()
    def refresh(self) -> None: ...             # 즉시 _fetch_token()


def build_auth(spec: AuthSpec | None, client: httpx.Client) -> AuthProvider | None: ...
```

러너의 갱신 루프 (with_retry 바깥, unit당 최대 1회 갱신):

```python
try:
    result = with_retry(functools.partial(source.fetch, unit), max_attempts=max_attempts)
except AuthExpiredError:
    if auth is None:
        raise
    auth.refresh()
    result = with_retry(functools.partial(source.fetch, unit), max_attempts=max_attempts)
```

RestSource는 요청마다 `auth.headers()`를 병합하고, 401이면 `AuthExpiredError`를 던진다(M1의 classify가 이미 처리).

- [ ] **Step 3: 커밋** — `git commit -m "feat: auth providers with expiry-triggered refresh"`

---

### Task 2.6: 검증 게이트 — 벡터화 규칙 검사

**Files:**
- Modify: `packages/pugio/src/pugio/validate/__init__.py` → `gate.py` 추가
- Modify: `packages/pugio/src/pugio/runner.py`
- Test: `packages/pugio/tests/test_validate_gate.py`

- [ ] **Step 1: 테스트**

```python
def test_not_null_violation_detected() -> None:
    batch = pa.RecordBatch.from_pylist([{"id": 1}, {"id": None}])
    report = check(batch, [ValidateRule(field="id", not_null=True)])
    assert report.violations == [Violation(rule="not_null", field="id", count=1)]


def test_range_and_unique() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": 1}, {"v": 99}])
    report = check(batch, [ValidateRule(field="v", unique=True, max=10)])
    assert {v.rule for v in report.violations} == {"unique", "max"}


def test_clean_batch_passes() -> None:
    batch = pa.RecordBatch.from_pylist([{"id": 1}])
    assert check(batch, [ValidateRule(field="id", not_null=True)]).ok
```

- [ ] **Step 2: 구현** — 행 루프 금지, pyarrow.compute만:

```python
import pyarrow.compute as pc

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
    out: list[Violation] = []
    for r in rules:
        col = batch.column(r.field)          # 없는 필드는 schema 위반 → FatalError
        if r.not_null and (n := col.null_count):
            out.append(Violation("not_null", r.field, n))
        if r.unique:
            dupes = len(col) - pc.count_distinct(col).as_py()
            if dupes:
                out.append(Violation("unique", r.field, dupes))
        if r.min is not None and (n := pc.sum(pc.less(col, r.min)).as_py() or 0):
            out.append(Violation("min", r.field, n))
        if r.max is not None and (n := pc.sum(pc.greater(col, r.max)).as_py() or 0):
            out.append(Violation("max", r.field, n))
    return GateReport(out)
```

- [ ] **Step 3: 러너 배선** — write 직전에 gate. 정책: `block`→FatalError로 중단, `warn`→typer 경고 후 계속, `quarantine`→Task 2.7의 DLQ로.

- [ ] **Step 4: 커밋** — `git commit -m "feat: vectorized validation gate"`

---

### Task 2.7: DLQ — 격리·조회·재투입

**Files:**
- Create: `packages/pugio/src/pugio/dlq.py`
- Modify: `packages/arsenal-core/src/arsenal_core/state/store.py` (mark_quarantined, quarantined 조회)
- Modify: `packages/pugio/src/pugio/cli.py` (dlq 서브커맨드)
- Test: `packages/pugio/tests/test_dlq.py`

- [ ] **Step 1: 테스트**

```python
def test_quarantine_isolates_unit_and_run_continues(tmp_path: Path) -> None:
    # 2번째 페이지에만 null id 주입 → 해당 unit만 quarantined, 나머지 done
    report = run_pipeline(spec_with_validation(tmp_path))
    store = StateStore(...)
    assert store.counts("t") == {"done": 2, "quarantined": 1}
    dlq_files = list((tmp_path / ".arsenal/dlq/t").glob("*.parquet"))
    assert len(dlq_files) == 1
    reason = json.loads(dlq_files[0].with_suffix(".json").read_text())
    assert reason["violations"][0]["rule"] == "not_null"


def test_dlq_retry_requeues_unit(tmp_path: Path) -> None:
    # 격리 후: dlq retry → 상태 pending 복귀 → 재실행 시 재fetch
    ...
```

- [ ] **Step 2: 구현** — `.arsenal/dlq/{pipeline}/{unit_id}.parquet` + 같은 이름 `.json`(위반 상세, unit_key, 시각). `pugio dlq list <yaml>` 표 출력, `pugio dlq retry <yaml> --unit <id>`는 상태를 pending으로 되돌리고 DLQ 파일 제거(재fetch가 원본을 다시 받는다 — DLQ 파일은 증거이지 재적재 소스가 아님).

- [ ] **Step 3: 커밋** — `git commit -m "feat: dead letter queue with quarantine policy"`

---

### Task 2.8: DuckDB sink — temp→MERGE 멱등

DuckDB 활용 핵심: Arrow RecordBatch를 **복사 없이 등록**하고, 삭제+삽입을 한 트랜잭션으로.

**Files:**
- Create: `packages/pugio/src/pugio/sinks/duckdb.py`
- Test: `packages/pugio/tests/test_duckdb_sink.py` + 공통 계약 스위트 등록

- [ ] **Step 1: 테스트** — sink 공통 계약(docs/05) 그대로:

```python
def test_write_twice_same_unit_row_count_unchanged(tmp_path: Path) -> None:
    sink = DuckDBSink(DuckDBSinkSpec(type="duckdb", path=tmp_path / "o.db",
                                     table="t", merge_key=["id"]))
    batch = pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])
    sink.write(unit("offset=0"), batch)
    sink.write(unit("offset=0"), batch)          # 재실행
    con = duckdb.connect(str(tmp_path / "o.db"))
    assert con.sql("SELECT count(*) FROM t").fetchone()[0] == 2


def test_merge_updates_changed_rows(tmp_path: Path) -> None:
    ...  # 같은 id, 다른 v로 재write → v가 갱신됨 (upsert 의미론)
```

- [ ] **Step 2: 구현**

```python
class DuckDBSink:
    def __init__(self, spec: DuckDBSinkSpec) -> None:
        self._spec = spec

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        con = duckdb.connect(str(self._spec.path))
        try:
            con.register("staging", pa.Table.from_batches([batch]))  # Arrow zero-copy 등록
            table = _quote_ident(self._spec.table)
            # DELETE에 별칭을 쓰지 않는다 (방언 호환) — 타겟은 테이블명으로 직접 참조
            keys = " AND ".join(
                f"{table}.{_quote_ident(k)} = s.{_quote_ident(k)}"
                for k in self._spec.merge_key
            )
            con.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM staging LIMIT 0")
            con.execute("BEGIN")
            con.execute(f"DELETE FROM {table} WHERE EXISTS "
                        f"(SELECT 1 FROM staging s WHERE {keys})")
            con.execute(f"INSERT INTO {table} SELECT * FROM staging")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise RetryableError(f"duckdb write failed for unit {unit.unit_id}") from None
        finally:
            con.close()
```

delete+insert를 택한 이유: DuckDB `MERGE INTO`보다 단순하고 의미가 명확하며, 트랜잭션 안이라 원자적 — 같은 unit 재실행 시 결과 동일. (M2 갱신 시점에 DuckDB MERGE 문법 성숙도를 재확인하고 교체 판단.)

러너 배선: sink가 union이 되었으므로 `build_sink(spec.sink) -> Sink` 팩토리를 `sinks/__init__.py`에 추가하고 러너의 `ParquetSink(...)` 직접 생성을 교체한다.

- [ ] **Step 3: 커밋** — `git commit -m "feat: duckdb sink with transactional merge idempotency"`

---

### Task 2.9: PostgreSQL sink

**Files:**
- Create: `packages/pugio/src/pugio/sinks/postgres.py`
- Modify: `packages/pugio/pyproject.toml` — `[project.optional-dependencies] postgres = ["psycopg[binary]>=3.1"]`, dev에 `testcontainers[postgres]`
- Test: `packages/pugio/tests/test_postgres_sink.py` (testcontainers, Docker 없으면 skip)

- [ ] **Step 1: 테스트** — DuckDB sink와 동일한 계약 스위트를 파라미터라이즈로 공유:

```python
pg = pytest.importorskip("testcontainers.postgres")

@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    with pg.PostgresContainer("postgres:16-alpine") as c:
        yield c.get_connection_url().replace("postgresql+psycopg2", "postgresql")
```

- [ ] **Step 2: 구현** — `COPY staging FROM STDIN (FORMAT BINARY)`는 과설계, unit 단위 배치이므로 `psycopg` executemany 대신: temp table 생성 → `copy_expert`(CSV) 적재 → `INSERT INTO target SELECT * FROM staging ON CONFLICT (keys) DO UPDATE SET ...` → 트랜잭션 커밋. 타겟 테이블은 첫 write에서 Arrow 스키마→DDL 매핑으로 생성(`_arrow_to_pg_type` 헬퍼: int64→bigint, string→text, timestamp→timestamptz, double→double precision, bool→boolean — 그 외 타입은 FatalError로 명시 거부).

- [ ] **Step 3: 커밋** — `git commit -m "feat: postgres sink with on-conflict upsert"`

---

### Task 2.10: 스키마 스냅샷 기록

**Files:**
- Modify: `packages/arsenal-core/src/arsenal_core/state/store.py`, `packages/pugio/src/pugio/runner.py`
- Test: `packages/arsenal-core/tests/test_schema_snapshot.py`

- [ ] **Step 1: 테스트** — 같은 스키마 연속 기록은 1행만(변경 시에만 append), `last_schema()`가 최신 반환.
- [ ] **Step 2: 구현** — 러너가 각 run의 첫 non-empty batch에서 `batch.schema`를 JSON 직렬화(필드명·타입·nullable)해 `store.snapshot_schema(pipeline, json)` 호출. 비교는 문자열 동등성. **감지·정책은 P1** — 여기서는 기록만.
- [ ] **Step 3: 커밋** — `git commit -m "feat: schema snapshot recording"`

---

### Task 2.11: FileSource — 로컬 파일 수집 (csv/jsonl/excel)

분석가의 1번 고통 "CSV 뭉치를 쿼리 가능하게"의 입구. 페이지네이션·인증 없음 — 파일 하나=unit 하나라 멱등이 공짜다. units()가 **유한** generator이므로 러너는 exhausted 없이 generator 종료로 끝난다(M1 러너가 이미 지원하는 경로 — for 루프 자연 종료).

**Files:**
- Create: `packages/pugio/src/pugio/sources/file.py`
- Modify: `packages/arsenal-core/src/arsenal_core/spec/models.py` (SourceSpec union화)
- Modify: `packages/pugio/pyproject.toml` — `[project.optional-dependencies] excel = ["fastexcel>=0.11"]`
- Test: `packages/pugio/tests/test_file_source.py`

- [ ] **Step 1: 스펙** — SourceSpec을 sink처럼 discriminated union으로:

```python
class RestSourceSpec(_Frozen):
    type: Literal["rest"]
    ...  # 기존 SourceSpec 필드 그대로 이동

class FileSourceSpec(_Frozen):
    type: Literal["file"]
    path: str                                  # 글롭 (예: "./raw/**/*.csv")
    format: Literal["auto", "csv", "jsonl", "excel"] = "auto"   # auto = 확장자로 판별
    encoding: str = "utf-8"

SourceSpec = Annotated[RestSourceSpec | FileSourceSpec, Field(discriminator="type")]
```

기존 이름 `SourceSpec`을 union 별칭으로 유지해 M1 코드의 import가 깨지지 않게 한다(러너·소스 팩토리만 분기 추가).

- [ ] **Step 2: 테스트**

```python
def test_each_file_is_one_unit_sorted(tmp_path: Path) -> None:
    for name in ["b.csv", "a.csv"]:
        (tmp_path / name).write_text("id,v\n1,x\n")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "*.csv")), pipeline="p")
    units = list(src.units())
    assert [u.unit_key for u in units] == ["a.csv", "b.csv"]   # 정렬 = 결정적 순서


def test_csv_fetch_returns_arrow(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,v\n1,x\n2,y\n")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "*.csv")), pipeline="p")
    result = src.fetch(next(iter(src.units())))
    assert result.batch.num_rows == 2
    assert result.exhausted is False           # 종료는 generator 소진이 담당


def test_jsonl_and_euc_kr_csv(tmp_path: Path) -> None: ...
def test_unreadable_file_is_fatal(tmp_path: Path) -> None: ...  # 파싱 불가 → FatalError
```

- [ ] **Step 3: 구현**

```python
class FileSource:
    def __init__(self, spec: FileSourceSpec, *, pipeline: str) -> None: ...

    def units(self) -> Iterator[UnitSpec]:
        base = Path(self._spec.path)
        root = _glob_root(self._spec.path)      # 글롭 시작 디렉터리 — unit_key의 기준
        for p in sorted(glob.glob(self._spec.path, recursive=True)):
            rel = str(Path(p).relative_to(root))
            yield UnitSpec.create(pipeline=self._pipeline, source=self._spec.path,
                                  unit_key=rel, payload={"path": p})

    def fetch(self, unit: UnitSpec) -> FetchResult:
        p = Path(unit.payload["path"])
        fmt = self._spec.format if self._spec.format != "auto" else _detect(p.suffix)
        try:
            if fmt == "csv":
                table = pa_csv.read_csv(p, read_options=pa_csv.ReadOptions(
                    encoding=self._spec.encoding))
            elif fmt == "jsonl":
                table = pa_json.read_json(p)
            else:  # excel — optional extra
                table = _read_excel(p)          # fastexcel: Arrow 네이티브 반환
        except Exception as e:
            raise FatalError(f"cannot parse {p}: {e}") from e
        batches = table.combine_chunks().to_batches()
        return FetchResult(batch=batches[0] if batches else None, exhausted=False)
```

excel은 `fastexcel` 미설치 시 `FatalError("install pugio[excel]")` — 의존성 정책(코어는 가볍게) 유지.

- [ ] **Step 4: 커밋** — `git commit -m "feat: file source — csv/jsonl/excel to arrow"`

---

### Task 2.12: DatabaseSource — 운영 DB → 웨어하우스 동기화

DE의 1번 수집 작업. **커넥터를 만들지 않는다** — DuckDB scanner(ATTACH)가 드라이버·타입 매핑·전송을 전부 담당하고([09](../09-oss-leverage.md) 수 1), 우리는 키 범위 unit 분할과 상태만 얹는다.

**Files:**
- Create: `packages/pugio/src/pugio/sources/database.py` (스켈레톤 있음)
- Modify: `packages/arsenal-core/src/arsenal_core/spec/models.py` (DatabaseSourceSpec — 스켈레톤 있음)
- Modify: `packages/pugio/pyproject.toml` — dependencies에 `duckdb>=1.0` 추가
- Test: `packages/pugio/tests/test_database_source.py`

- [ ] **Step 1: 테스트** — SQLite dialect로 빠르게 (PG는 testcontainers 1케이스):

```python
def test_units_are_key_ranges(tmp_path: Path) -> None:
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "x") for i in range(1, 251)])
    con.commit()
    spec = DatabaseSourceSpec(type="database", dialect="sqlite", dsn_env="SRC_DB",
                              table="orders", split=SplitSpec(key="id", chunk=100))
    src = DatabaseSource(spec, pipeline="p")           # dsn은 env에서
    units = list(src.units())
    assert [u.unit_key for u in units] == ["id=1..101", "id=101..201", "id=201..301"]


def test_fetch_range_returns_arrow(tmp_path: Path) -> None:
    ...  # 첫 unit fetch → 100행, batch.num_rows == 100


def test_rerun_with_grown_table_only_adds_new_ranges(tmp_path: Path) -> None:
    ...  # 300행 추가 후 재실행 → 기존 done 범위는 skip, 새 범위만 unit 추가
```

- [ ] **Step 2: 스펙**

```python
class SplitSpec(_Frozen):
    key: str            # 단조 증가 키 (PK/serial/타임스탬프)
    chunk: int = 100_000


class DatabaseSourceSpec(_Frozen):
    type: Literal["database"]
    dialect: Literal["postgres", "mysql", "sqlite"]
    dsn_env: str        # DSN은 환경변수로만 (비밀 원칙)
    table: str
    split: SplitSpec
```

- [ ] **Step 3: 구현** — units(): `SELECT min(key), max(key)`로 경계 조회 후 chunk 단위 범위 생성(`unit_key = "id=lo..hi"` — 결정적). fetch():

```python
def fetch(self, unit: UnitSpec) -> FetchResult:
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{self._dsn}' AS src (TYPE {self._spec.dialect.upper()}, READ_ONLY)")
        lo, hi = unit.payload["lo"], unit.payload["hi"]
        table = con.execute(
            f"SELECT * FROM src.{_qi(self._spec.table)} "
            f"WHERE {_qi(self._spec.split.key)} >= ? AND {_qi(self._spec.split.key)} < ?",
            [lo, hi],
        ).arrow()
    except duckdb.Error as e:
        raise RetryableError(f"database fetch failed: {e}") from e
    finally:
        con.close()
    batches = table.to_batches()
    return FetchResult(batch=batches[0] if batches else None, exhausted=False)
```

주의: 마지막 범위 이후 **새로 늘어난 행**은 재실행 시 max(key) 재조회로 새 unit이 생긴다 — 증분 동기화가 구조에서 공짜로 나온다. UPDATE된 기존 행은 P0 범위 밖(스냅샷 의미론) — 한계선 문서에 명시.

- [ ] **Step 4: 커밋** — `git commit -m "feat: database source via duckdb scanner with key-range units"`

---

### Task 2.13: Python 커스텀 소스 탈출구

YAML로 표현 안 되는 API를 만나도 절벽이 없다. Source 프로토콜 구현체를 동적 로드 — P1 dlt 래퍼도 이 메커니즘 위에 선다.

**Files:**
- Create: `packages/pugio/src/pugio/sources/python_source.py` (스켈레톤 있음)
- Modify: `packages/arsenal-core/src/arsenal_core/spec/models.py` (PythonSourceSpec — 스켈레톤 있음)
- Test: `packages/pugio/tests/test_python_source.py`

- [ ] **Step 1: 테스트**

```python
def test_loads_user_source_and_runs(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "my_source.py").write_text(textwrap.dedent("""
        import pyarrow as pa
        from arsenal_core.state import UnitSpec
        from pugio.sources.base import FetchResult

        class MySource:
            def __init__(self, options, *, pipeline): self._p = pipeline
            def units(self):
                yield UnitSpec.create(pipeline=self._p, source="my", unit_key="only", payload={})
            def fetch(self, unit):
                return FetchResult(pa.RecordBatch.from_pylist([{"id": 1}]), exhausted=True)
    """))
    monkeypatch.syspath_prepend(tmp_path)
    src = load_python_source(
        PythonSourceSpec(type="python", target="my_source:MySource"), pipeline="p"
    )
    assert next(iter(src.units())).unit_key == "only"


def test_missing_protocol_method_is_fatal() -> None:
    ...  # fetch 없는 클래스 → FatalError("does not implement Source protocol: fetch")


def test_import_error_is_fatal_with_hint() -> None:
    ...  # 없는 모듈 → FatalError에 target 문자열과 검색 경로 포함
```

- [ ] **Step 2: 스펙 + 로더**

```python
class PythonSourceSpec(_Frozen):
    type: Literal["python"]
    target: str                          # "pkg.module:ClassName"
    options: dict[str, Any] = {}         # 생성자 첫 인자로 전달


def load_python_source(spec: PythonSourceSpec, *, pipeline: str) -> Source:
    module_name, _, cls_name = spec.target.partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise FatalError(f"cannot load python source {spec.target!r}: {e}") from e
    for method in ("units", "fetch"):
        if not callable(getattr(cls, method, None)):
            raise FatalError(f"{spec.target} does not implement Source protocol: {method}")
    return cls(spec.options, pipeline=pipeline)
```

보안 명시: target은 사용자 자신의 코드다(dbt 매크로와 같은 신뢰 모델). 원격/서드파티 target을 받는 서비스화는 이 설계의 범위 밖.

- [ ] **Step 3: 커밋** — `git commit -m "feat: python source escape hatch"`

---

### Task 2.14: 실전 API 5종 스펙 검증

페이지네이션 4종이 탁상 설계인지 실전인지 여기서 판명한다. **코드가 아니라 스펙 작성 훈련** — 각 API를 실제 YAML로 작성하고, 표현 불가 지점을 스펙에 역반영한다.

**Files:**
- Create: `examples/real-world/{github,stripe,data-go-kr,notion,slack}.yaml`
- Create: `docs/reference/api-coverage.md` (검증 결과 기록)

- [x] 대상과 검증 포인트:

| API | 검증 포인트 |
|---|---|
| GitHub | `mode: link` (Link 헤더), 시간당 rate limit |
| Stripe | `mode: cursor` (`starting_after`), envelope(`record_path: data`) |
| 공공데이터포털 | euc-kr 인코딩, `mode: page`, 키 쿼리 파라미터 인증 |
| Notion | cursor + 초당 3req rate limit, POST 검색 API(→ method 필드 필요성 판정) |
| Slack | cursor(`next_cursor`), 429 Retry-After |

- [x] 각 YAML은 respx 목으로 계약 테스트(실 계정 불필요). 표현 불가 항목은 (a) 스펙 필드 추가 또는 (b) "Python 탈출구 사용" 판정을 `api-coverage.md`에 기록 — 숨기지 않는다.
  - method/body 필드를 `RestSourceSpec`에 추가(Notion POST 검색이 필요로 함, GET 기본값이라 하위 호환).
  - Stripe는 표현 불가로 판정(last-item-id 커서) → Python 탈출구가 의도된 경로. `docs/reference/api-coverage.md` 참고.
  - 검증 과정에서 실제 버그 2건 발견·수정: url에 심은 정적 쿼리 파라미터가 페이지네이션 파라미터에 의해 드롭되는 문제, Slack의 빈 문자열(`""`) 커서를 종료 신호로 인식하지 못해 무한루프에 빠지는 문제. 둘 다 회귀 테스트로 고정.
- [x] Commit: `test: real-world api coverage verification`

---

### Task 2.15: 실 API E2E (opt-in) + M2 마무리

- [x] `packages/pugio/tests/test_live_github.py` — `RUN_LIVE=1`일 때만: GitHub API 3페이지 수집, 중단·재개 1회. CI 기본 제외(스킵, 토큰 불필요). (계획 문서의 `tests/e2e/` 경로 대신 기존 패키지 테스트 디렉터리 `packages/pugio/tests/`에 배치 — 다른 pugio 테스트와 동일한 위치.)
- [x] `examples/`에 cursor·duckdb sink·validate 예제 YAML 추가.
- [x] 전체 게이트: `uv run ruff check . && uv run pyright && uv run pytest` + 커버리지 ≥ 80 확인.
- [x] `git commit`(M2-H 배치 — 커밋 메시지는 `docs: quickstart-adjacent examples + m2 wrap + live e2e opt-in`, 계획 문서 초안의 문구와 다르게 실제로는 두 개 커밋으로 분리됨(H1/H3)).

## M2 DoD

- [x] 페이지네이션 4종(offset/page/cursor/link)이 각각 재개 시나리오까지 통합 테스트로 커버
- [x] 시나리오 C(토큰 만료 → 갱신 → 완주) 자동 검증
- [x] 모든 sink가 공통 계약 스위트(멱등·원자성) 통과
- [x] 검증 위반 unit이 격리돼도 파이프라인이 완주하고, DLQ 재투입이 동작
- [x] 실전 API 5종의 YAML 표현 검증 완료 — `docs/reference/api-coverage.md`에 결과 기록
- [x] DatabaseSource로 SQLite→parquet 동기화 + 증분(늘어난 행) 재실행 검증
- [x] Python 탈출구로 커스텀 소스 1개가 파이프라인 완주
- [ ] **도그푸딩 개시 확인**: 실제 반복 작업 1개가 pugio로 매주 실행 중 (M1 직후 시작 — 04 진행 방식) — 운영 액션, 코드 배치 범위 밖 (미체크 유지)
