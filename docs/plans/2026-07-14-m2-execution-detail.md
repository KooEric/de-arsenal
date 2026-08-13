# M2 실행 세부 계획 — 원자 작업 단위 (stall-proof)

> **Historical execution detail:** M2 구현 당시의 서브에이전트 실행 원장이다. 현재
> 제품 상태의 기준은 [docs/04-implementation-plan.md](../04-implementation-plan.md),
> 릴리스 검증은 [docs/release.md](../release.md), 실제 사용 검증은
> [docs/dogfooding.md](../dogfooding.md)다.

> **목적:** M2의 모든 남은 작업을 **Opus/Sonnet 서브에이전트가 막힘 없이(=fable 에스컬레이션 없이) 실행**할 수 있는 원자 단위로 쪼갠 실행서다.
> **상위 문서:** [2026-07-08-m2-pugio-complete.md](2026-07-08-m2-pugio-complete.md)(범위·근거). 이 문서는 그 태스크들을 **실행 단위(U-ID)**로 세분화한다.
> **원장:** `.superpowers/sdd/progress.md`. 각 U 완료 시 갱신.

## 이 문서를 막힘 방지 도구로 쓰는 법 (읽고 시작할 것)

서브에이전트가 막히는 3대 원인과, 이 문서가 그것을 제거하는 방식:

1. **모르는 API** → 아래 **"§0 고정 시그니처 레퍼런스"**에 M2에서 건드리는 모든 기존 API의 정확한 시그니처를 박아뒀다. 서브에이전트는 파일을 재탐색할 필요 없이 이 절만 신뢰하면 된다.
2. **미확정 설계 결정** → 각 U의 **"결정(확정)"** 항목에 판단이 필요한 지점을 **미리 다 내려**뒀다. 서브에이전트는 결정하지 말고 그대로 따른다.
3. **모호한 지시** → 각 U는 TDD 단계(RED 테스트 이름·핵심 단언 → GREEN 구현)와 정확한 완료 기준(명령어·기대 출력)을 포함한다.

**서브에이전트 브리핑 규칙:** 각 U를 dispatch할 때 (a) 이 문서의 §0 전체 + 해당 U 전체를 프롬프트에 넣고, (b) "결정(확정) 항목은 재론하지 말 것", (c) "막히면 하지 말고 즉시 무엇이 막았는지 보고 → 오케스트레이터가 fable로 승격"을 명시한다.

**공통 완료 기준 (모든 U 커밋 전 필수):**
```bash
uv run pytest -q          # 전부 통과, 커버리지 ≥ 80
uv run ruff check .       # clean
uv run ruff format .      # 후 --check . clean
uv run pyright            # 0 errors
```
push·merge 금지. 커밋만. 각 배치(묶음)마다 태스크 리뷰 1회 → Critical/Important는 fix 서브에이전트 1개에 일괄 → 재리뷰 → 원장 갱신.

---

## §0 고정 시그니처 레퍼런스 (현재 코드 기준 — 재탐색 불필요)

### arsenal_core.errors
```python
class ArsenalError(Exception): ...
class FatalError(ArsenalError): ...        # 재시도 무의미. 즉시 중단
class RetryableError(ArsenalError): ...    # backoff 재시도
class AuthExpiredError(RetryableError): ...# 인증 만료 = 갱신 트리거
def classify_http_status(status: int) -> type[ArsenalError] | None:
    # <400→None, 401→AuthExpiredError, 429/5xx→RetryableError, 그 외 4xx→FatalError
```

### arsenal_core.retry
```python
DEFAULT_MAX_ATTEMPTS = 5
def with_retry(fn: Callable[[], T], *, max_attempts=5, base_wait=1.0) -> T
# tenacity: RetryableError만 재시도, exponential+jitter, reraise=True
```

### arsenal_core.state (from arsenal_core.state import UnitSpec, StateStore)
```python
@dataclass(frozen=True)
class UnitSpec:
    unit_id: str; pipeline: str; unit_key: str; payload: dict[str, Any]
    @classmethod
    def create(cls, *, pipeline: str, source: str, unit_key: str, payload: dict) -> UnitSpec

@dataclass(frozen=True)
class UnitRecord: unit_id: str; status: str; attempts: int; last_error: str | None
@dataclass(frozen=True)
class UnitMetrics: row_count: int|None; byte_count: int|None; duration_ms: int|None

class StateStore:
    def __init__(self, db_path: Path)
    def register(self, unit: UnitSpec) -> None          # INSERT OR IGNORE (멱등)
    def mark_running(self, uid: str) -> None
    def mark_done(self, uid: str, *, row_count=None, byte_count=None, duration_ms=None) -> None
    def mark_failed(self, uid: str, error: str) -> None  # attempts += 1
    def status(self, uid: str) -> str | None
    def is_done(self, uid: str) -> bool
    def get(self, uid: str) -> UnitRecord                # 없으면 KeyError
    def metrics(self, uid: str) -> UnitMetrics
    def counts(self, pipeline: str) -> dict[str, int]    # status→count
    def close(self) -> None
```
**⚠️ 이미 스키마에 존재하나 메서드는 미구현인 테이블 (신규 CREATE 금지 — 메서드만 추가):**
```sql
cursors(pipeline, source, cursor, updated_at, PRIMARY KEY(pipeline, source))
schema_snapshots(pipeline, captured_at, schema_json)
units.status enum 에 'quarantined' 이미 포함
```

### pugio.sources.base
```python
@dataclass(frozen=True)
class FetchResult:
    batch: pa.RecordBatch | None   # None = 데이터 없음
    exhausted: bool                # True = 마지막 unit
    # ← A2에서 next_cursor: str | None = None 추가

class Source(Protocol):
    def units(self) -> Iterator[UnitSpec]: ...
    def fetch(self, unit: UnitSpec) -> FetchResult: ...
    # 선택적 close(self) -> None — 러너가 getattr로 방어 호출
```

### pugio.runner
```python
def _build_source(spec: PipelineSpec) -> Source   # spec.source.type 분기
def run_pipeline(spec, *, on_unit_complete=None, max_attempts=5) -> RunReport
# 루프: units()→register→is_done skip→mark_running→with_retry(fetch)→(except→mark_failed→raise)
#       →sink.write(멱등 먼저)→mark_done(불변식)→exhausted면 break
# finally: getattr(source,"close",None) 호출 후 store.close()
# 현재 sink 생성: sink = ParquetSink(Path(spec.sink.path))   ← F1에서 build_sink로 교체
```

### pugio.sinks
```python
class ParquetSink:
    def __init__(self, path: Path)
    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None  # {dir}/{unit_id}.parquet, tmp+os.replace
# sinks/__init__.py 는 Sink, ParquetSink 만 export ← F1에서 build_sink 추가
```

### arsenal_core.spec.models (현재 상태)
```python
class PaginationSpec(_Frozen): mode: Literal["offset"]; param="offset"; size_param="limit"; size=100
class RateLimitSpec(_Frozen): rps: float
class RestSourceSpec(_Frozen): type=Literal["rest"]; url; headers={}; pagination; rate_limit=None; encoding="utf-8"
class FileSourceSpec(_Frozen): type="file"; path:str; format="auto"; encoding="utf-8"
class SplitSpec(_Frozen): key:str; chunk:int=Field(default=100_000, gt=0)
class DatabaseSourceSpec(_Frozen): type="database"; dialect=Literal["postgres","mysql","sqlite"]; dsn_env; table; split
class PythonSourceSpec(_Frozen): type="python"; target:str; options={}
SourceSpec = Annotated[RestSourceSpec|FileSourceSpec|DatabaseSourceSpec|PythonSourceSpec, Field(discriminator="type")]
class SinkSpec(_Frozen): type=Literal["parquet"]; path:str (+ _coerce_path_to_str before-validator)
class PipelineSpec(_Frozen): name; state_dir=Path(".arsenal"); source; sink
```
**전역 규칙:** 모델은 `_Frozen`(frozen, extra="forbid") 상속. 경로 필드는 URI 보존 위해 `str`. 파이프라인 생성은 `PipelineSpec.model_validate({...})`, REST 스펙 단독 생성은 `RestSourceSpec(type="rest", ...)`. `SourceSpec(...)` 직접 호출 불가.

### spec.loader
```python
def load_pipeline(path: Path) -> PipelineSpec  # yaml.safe_load + env 치환 + model_validate
# ValidationError→FatalError(_clean_loc로 discriminator 태그 경로 정리), FileNotFound/OSError→FatalError
_UNION_TAGS = {"rest","file","database","python"}  # 새 union 태그 추가 시 여기도 갱신
```

### 테스트 관례 (docs/05 계승)
- respx로 HTTP 목 (실계정·네트워크 불요). 패턴은 `packages/pugio/tests/test_rest_source.py` 참조.
- **sleep 없는 테스트**: 시간 의존 코드엔 `Clock` 프로토콜 주입 + `FakeClock`.
- Path 인자를 pydantic 모델에 직접 keyword로 넣지 말 것(pyright strict + dataclass_transform 충돌) → `model_validate({...})` 사용.
- 테스트 파일당 하나의 소스/기능, AAA 구조, 행동 단언(스모크 금지).

---

## 배치·U 의존 그래프 (한눈에)

```
M2-A (스펙 토대: A1..A6)  ── 모든 후속의 입력
   ├─► M2-B (페이지네이션: B1..B8)   [rest.py, runner, store]
   ├─► M2-C (encoding+rate: C1..C4)  [rest.py, +ratelimit.py]
   ├─► M2-D (인증: D1..D4)           [rest.py, runner, +auth]
   ├─► M2-E (검증+DLQ: E1..E6)       [runner, +validate, +dlq, store]
   └─► M2-F (DB sink: F1..F5)        [sinks, runner]
          │
          ▼
        M2-G (DB커넥터+스냅샷: G1..G3)
          ▼
        M2-H (실전검증+마무리: H1..H3)
```
**rest.py 경합:** B·C·D가 모두 rest.py를 수정 → 병렬 dispatch 금지, **B→C→D 순차**(각 머지 후 다음이 최신 rest.py 위에서). E·F는 rest.py를 안 건드려 B와 진짜 병렬 가능.

---

# 배치 M2-A — 스펙 확장 (모델 토대)

> 순수 모델 확장. **하위 호환이 유일한 리스크** → 완료 증거 = 기존 전 테스트 통과. 파일: `arsenal_core/spec/models.py`, `pugio/sources/base.py`, 테스트 `arsenal-core/tests/test_spec_m2.py`. A1~A5는 한 배치로 묶어 구현 후 A6에서 회귀+커밋.

### U-A1 · PaginationSpec 모드 확장
- **목표:** offset 전용 → offset/page/cursor/link 4종을 표현하는 스펙.
- **결정(확정):**
  - `mode: Literal["offset","page","cursor","link"]`.
  - offset/page 공용 필드 유지(`param`,`size_param`,`size`) + `start_page:int=1`(page 시작번호).
  - cursor 전용: `cursor_param:str|None=None`, `cursor_path:str|None=None`(응답 점표기).
  - 공용 신규: `record_path:str|None=None`(envelope 응답에서 레코드 배열 위치, 점표기. None=응답 자체가 배열). offset/page/cursor/link 전부에 유효.
  - link 전용 필드는 불필요(헤더에서 추출) — mode만 분기.
  - `@model_validator(mode="after")`로 `mode=="cursor"`면 `cursor_param and cursor_path` 필수, 아니면 ValueError("cursor mode requires cursor_param and cursor_path").
- **TDD:** `test_spec_m2.py`
  - `test_cursor_mode_requires_cursor_fields` — `PaginationSpec(mode="cursor", cursor_param="after")` → `pytest.raises(ValidationError, match="cursor_path")` (model_validator ValueError는 pydantic이 ValidationError로 감쌈).
  - `test_page_mode_defaults` — `PaginationSpec(mode="page")` → `start_page==1`.
  - `test_offset_unchanged` — 기존 `PaginationSpec(mode="offset")` 여전히 유효(하위호환).
- **함정:** 기존 M1/draft에서 `PaginationSpec(mode="offset", size=..)` 호출들이 깨지면 안 됨 — 신규 필드는 전부 기본값.

### U-A2 · FetchResult.next_cursor 추가
- **목표:** cursor/link 재개용 필드.
- **결정:** `next_cursor: str | None = None` 을 dataclass 마지막에 추가(기본값 → M1 코드 무수정). link 모드에선 "다음 페이지 전체 URL"을 이 필드에 담는다(cursor와 동일 취급).
- **TDD:** `test_fetch_result_next_cursor_defaults_none` — `FetchResult(batch=None, exhausted=True).next_cursor is None`.
- **함정:** 필드 순서 — 기존 위치(batch, exhausted) 뒤에 append.

### U-A3 · SinkSpec union화
- **목표:** parquet 단일 → parquet/duckdb/postgres 판별 union. **구현은 F, 여기선 스펙만.**
- **결정(확정):**
  ```python
  class ParquetSinkSpec(_Frozen):
      type: Literal["parquet"]; path: str
      @field_validator("path", mode="before")  # 기존 _coerce_path_to_str 그대로 이식
  class DuckDBSinkSpec(_Frozen):
      type: Literal["duckdb"]; path: str; table: str; merge_key: list[str]
  class PostgresSinkSpec(_Frozen):
      type: Literal["postgres"]; dsn_env: str; table: str; merge_key: list[str]
  SinkSpec = Annotated[ParquetSinkSpec|DuckDBSinkSpec|PostgresSinkSpec, Field(discriminator="type")]
  ```
  - 기존 `class SinkSpec`(parquet)의 필드·validator를 `ParquetSinkSpec`으로 이동. `SinkSpec`은 union 별칭이 됨.
  - `PipelineSpec.sink: SinkSpec` 은 그대로(별칭 참조).
  - path는 전부 `str`(URI 보존).
- **TDD:**
  - `test_parquet_sink_still_loads` — `PipelineSpec.model_validate` M1 예제(sink type=parquet) 여전히 통과.
  - `test_duckdb_sink_discriminates` — `TypeAdapter(SinkSpec).validate_python({"type":"duckdb","path":"o.db","table":"t","merge_key":["id"]}).merge_key==["id"]`.
  - `test_s3_uri_path_preserved` — `ParquetSinkSpec(type="parquet", path="s3://b/x").path == "s3://b/x"`.
- **함정:** `runner.py`의 `ParquetSink(Path(spec.sink.path))`는 지금은 sink가 parquet뿐이라 동작 — **F1 전까지 runner는 손대지 않는다**(union이 돼도 파이프라인 스펙이 parquet면 `spec.sink`가 `ParquetSinkSpec`이라 `.path` 접근 OK). pyright가 union에서 `.path` 접근을 문제삼으면 F1에서 팩토리로 해소되므로, A 단계에서 runner에 임시 `if spec.sink.type=="parquet"` 가드가 필요할 수 있음 — pyright 결과 보고 최소 대응.

### U-A4 · AuthSpec + RestSourceSpec.auth
- **목표:** 인증 선언(구현은 D).
- **결정(확정):**
  ```python
  class AuthSpec(_Frozen):
      type: Literal["static_token","oauth2_client_credentials"]
      token_env: str | None = None          # static
      token_url: str | None = None           # oauth2
      client_id_env: str | None = None
      client_secret_env: str | None = None
      expiry_buffer_s: int = 60              # 만료 60초 전 선제 갱신
  # RestSourceSpec 에 필드 추가: auth: AuthSpec | None = None
  ```
  - `RestSourceSpec`에만 추가(union base 불가). file/database/python은 인증 없음.
- **TDD:** `test_oauth2_auth_spec_defaults` — `AuthSpec(type="oauth2_client_credentials", token_url=..., client_id_env=..., client_secret_env=...).expiry_buffer_s==60`. `test_rest_spec_auth_optional` — auth 없이도 RestSourceSpec 생성됨.

### U-A5 · ValidateSpec + PipelineSpec.validation
- **목표:** 검증 규칙 선언(구현은 E).
- **결정(확정):**
  ```python
  class ValidateRule(_Frozen):
      field: str; not_null: bool=False; unique: bool=False
      min: float|None=None; max: float|None=None
  class ValidateSpec(_Frozen):
      rules: list[ValidateRule]
      on_violation: Literal["block","quarantine","warn"] = "quarantine"
  # PipelineSpec:
  #   validation: ValidateSpec | None = Field(None, alias="validate")
  #   model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
  ```
  - YAML은 `validate:` 키, 코드는 `spec.validation`. `validate`는 pydantic BaseModel.validate와 충돌하므로 반드시 alias.
- **TDD:**
  - `test_validate_rule_shape` — `ValidateRule(field="id", not_null=True, unique=True)` → `min is None`.
  - `test_pipeline_validate_alias` — `PipelineSpec.model_validate({... "validate": {"rules":[{"field":"id","not_null":True}]}})` → `spec.validation.rules[0].field=="id"`.
  - `test_pipeline_without_validate` — validation 없으면 `spec.validation is None`.
- **함정:** `populate_by_name=True`를 PipelineSpec에만 추가(다른 모델 영향 없음). extra="forbid" 유지 확인.

### U-A6 · 하위호환 회귀 + 커밋
- **완료 기준:** 공통 게이트 통과 + **기존 전 테스트(draft 135개)가 하나도 안 깨짐**. 신규 테스트로 커버리지 상승.
- **커밋:** `feat: m2 spec fields — pagination modes, auth, validate, sink union, next_cursor`

---

# 배치 M2-B — 페이지네이션 3종 + 커서 영속 (최우선 가치)

> 파일: `pugio/sources/rest.py`, `pugio/runner.py`, `arsenal_core/state/store.py`, 테스트 `test_rest_pagination.py`·`test_rest_cursor.py`·`test_state_cursor.py`. **rest.py 경합 배치 1번** — C·D보다 먼저.

### U-B1 · rest.py `_request_params` 리팩터 (offset 무회귀)
- **목표:** fetch()의 파라미터 구성을 헬퍼로 추출 — 모드 분기의 토대. **행동 변화 0.**
- **결정:** `_request_params(self, unit) -> dict[str, str|int]` 추출. offset 분기만 먼저.
- **TDD:** 기존 `test_rest_source.py` 전부 통과(회귀 가드). 신규 `test_request_params_offset` — offset unit → `{param: offset, size_param: size}`.

### U-B2 · page 모드
- **결정:** `units()`에서 `mode=="page"`면 `itertools.count(start_page, 1)`로 페이지 번호, `unit_key=f"page={n}"`, payload=`{"page":n}`. `_request_params`에 page 분기: `{p.param: payload["page"], p.size_param: p.size}`. 종료는 offset과 동일(`len(rows)<size`면 exhausted).
- **TDD (respx):** `test_page_mode_sends_incrementing_pages` — responder가 page 파라미터 수집, `page<=2`면 데이터·else 빈배열 → `seen==[start_page, start_page+1, start_page+2]`.

### U-B3 · `_dig` 점표기 + record_path
- **목표:** envelope 응답(`{"data":[...], "meta":{...}}`)에서 레코드 배열 추출.
- **결정:** `_dig(obj: Any, path: str) -> Any` 헬퍼(`"meta.next"` → `obj["meta"]["next"]`, 없으면 None). fetch()에서 `rows = resp_json if p.record_path is None else _dig(resp_json, p.record_path)`. record_path가 가리키는 곳이 list 아니면 FatalError.
- **TDD:** `test_dig_nested`, `test_record_path_extracts_array` — `record_path="data"` 응답에서 배열만 파싱.

### U-B4 · cursor 모드 (소스 측)
- **목표:** 다음 unit이 이전 응답에 의존 — lazy 커서 추적.
- **결정(확정):**
  - `__init__`에 `initial_cursor: str|None=None` 추가. `self._pending_cursor = initial_cursor`.
  - `units()` cursor 분기: `while True: yield UnitSpec.create(..., unit_key=f"cursor={self._pending_cursor}", payload={"cursor": self._pending_cursor}); ` — 단, generator가 다음 값을 알려면 fetch가 `_pending_cursor`를 갱신해야 함. **패턴:** units()는 `cur = self._pending_cursor; yield unit(cur); if self._last_exhausted: return`. fetch()가 `self._pending_cursor = next_cursor`, `self._last_exhausted = (next_cursor is None)` 설정. (offset/page/link는 이 상태 안 씀.)
  - `_request_params` cursor 분기: cursor가 None(첫 요청)이면 cursor_param 생략, 있으면 `{p.cursor_param: cur, p.size_param: p.size}`.
  - fetch(): 응답에서 `next_cursor = _dig(resp_json, p.cursor_path)`, rows는 record_path로. `FetchResult(batch, exhausted=(next_cursor is None), next_cursor=next_cursor)`.
- **TDD (respx):** `test_cursor_mode_follows_next` — 3페이지 체인(`""→c1→c2→None`), `[r.next_cursor for r in results]==["c1","c2",None]`. `test_cursor_resume_from_initial` — `initial_cursor="c1"` → 첫 unit payload cursor=="c1".

### U-B5 · StateStore.get_cursor / set_cursor
- **목표:** 커서 영속(테이블 이미 존재!).
- **결정:** 
  ```python
  def get_cursor(self, pipeline: str, source: str) -> str | None
  def set_cursor(self, pipeline: str, source: str, cursor: str) -> None
      # INSERT ... ON CONFLICT(pipeline, source) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at
  ```
- **TDD:** `test_state_cursor.py::test_cursor_roundtrip_and_upsert` — get None → set "abc" → set "def" → get=="def".
- **함정:** `cursors` 테이블은 이미 `_SCHEMA`에 있음 — CREATE 추가 금지.

### U-B6 · runner cursor 재개 배선
- **목표:** cursor 모드일 때 마지막 완료 커서부터 재개.
- **결정(확정):**
  - `_build_source`의 rest 분기: cursor 모드면 `initial = store.get_cursor(spec.name, src.url)`를 `RestSource(..., initial_cursor=initial)`로 주입. **그러려면 `_build_source`가 store를 받아야 함** → 시그니처를 `_build_source(spec, store)`로 변경(호출부도).
  - 루프에서 `mark_done` 직후: `if result.next_cursor is not None: store.set_cursor(spec.name, src.url, result.next_cursor)`. 순서 = 쓰기→done→커서전진(불변식 연장).
  - **주의:** cursor 모드는 done-skip이 무의미(매번 새 커서). initial_cursor 주입으로 frontier에서 시작하는 방식이라 register/is_done 경로는 그대로 둬도 무해(커서가 unit_key라 매번 새 unit_id).
- **TDD:** `test_cursor_crash_resume` — 3페이지 중 2페이지 후 `on_unit_complete`로 크래시 주입 → 재실행이 `after=c1`(store에 저장된 커서)부터 요청함을 respx 호출 기록으로 검증.

### U-B7 · link 모드 (cursor 메커니즘 재사용)
- **목표:** RFC 5988 Link 헤더(GitHub·Shopify).
- **결정(확정):** "커서 값 = 다음 페이지 전체 URL"인 cursor의 특수형. 차이는 추출 위치뿐:
  ```python
  _LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
  def _next_from_link_header(resp) -> str|None: m=_LINK_NEXT.search(resp.headers.get("link","")); return m.group(1) if m else None
  ```
  - fetch() link 분기: `unit.payload["cursor"]`(=URL)이 있으면 그 URL로 직접 GET(파라미터 재구성 없음), 없으면 `self._spec.url`. next_cursor = `_next_from_link_header(resp)`. record_path/exhausted 동일.
  - units()/재개는 cursor와 완전히 동일 경로 재사용.
- **TDD:** `test_link_mode_follows_header` — GitHub 스타일 Link 헤더 3페이지 목 + `test_link_resume`.

### U-B8 · 배치 리뷰 + 커밋
- **커밋들(논리 단위):** B1~B2 `feat: page pagination mode` / B3~B7 `feat: cursor and link pagination with persistent resume`. (또는 한 배치 리뷰 후 2커밋.)
- **DoD:** 페이지네이션 4종이 각각 재개 시나리오까지 테스트 커버.

---

# 배치 M2-C — 인코딩 + rate limiter

> 파일: `pugio/sources/rest.py`, 신규 `arsenal_core/ratelimit.py`, 테스트 `test_rest_encoding.py`·`test_ratelimit.py`. **rest.py 경합 배치 2번 (B 머지 후 착수).**

### U-C1 · 인코딩 디코딩
- **결정:** fetch()에서 `resp.json()` 대신 `json.loads(resp.content.decode(self._spec.encoding))`. `UnicodeDecodeError`/`LookupError`(잘못된 인코딩명)→`FatalError(f"cannot decode response as {encoding}: ...")`.
- **TDD (respx):** `test_euc_kr_response_decoded` — euc-kr 바이트 응답 + `encoding="euc-kr"` → `batch.to_pylist()[0]["이름"]=="김철수"`. `test_bad_encoding_name_is_fatal`.

### U-C2 · TokenBucket (arsenal_core/ratelimit.py 신규)
- **결정(확정):** 상위 플랜 Task 2.4 Step 2의 `Clock` Protocol + `TokenBucket` 구현을 **그대로** 사용(검증됨). `acquire()`(rps 상한, penalty 대기), `penalize(seconds)`(429 Retry-After).
- **TDD (FakeClock 주입):** `test_token_bucket_throttles_to_rps`(2rps, 4회 → 총 sleep≈1.0s), `test_penalize_respects_retry_after`(penalize(30) 후 acquire → sleep≥30).
- **함정:** sleep 없는 테스트 — 반드시 FakeClock 주입. 실시간 sleep 테스트 금지.

### U-C3 · rest.py rate_limit 연동
- **결정:** `__init__`에서 `spec.rate_limit` 있으면 `self._bucket = TokenBucket(spec.rate_limit.rps, clock=clock)`(clock 주입 가능하게 `__init__`에 `clock=None` 추가). fetch() 맨 앞 `if self._bucket: self._bucket.acquire()`. 429 응답이면 `Retry-After` 헤더 파싱→`self._bucket.penalize(sec)` 후 기존 classify가 RetryableError 발생(with_retry가 다음 acquire에서 자연 대기).
- **TDD:** `test_rate_limit_acquires_before_each_request`(FakeClock, N요청 → acquire 호출수), `test_429_penalizes_bucket`.

### U-C4 · 커밋
- **커밋:** `feat: source encoding + token bucket rate limiter with 429 adaptation`

---

# 배치 M2-D — 인증 갱신 루프

> 파일: `pugio/auth/__init__.py`(구현), `pugio/sources/rest.py`, `pugio/runner.py`, 테스트 `test_auth.py`. **rest.py 경합 배치 3번 (C 머지 후).**

### U-D1 · Auth providers
- **결정(확정):** 상위 플랜 Task 2.5 Step 2 그대로:
  ```python
  class AuthProvider(Protocol):
      def headers(self) -> dict[str,str]: ...
      def refresh(self) -> None: ...
  class StaticTokenAuth:  # refresh()는 FatalError("static token expired; rotate the secret")
  class OAuth2ClientCredentials:  # expiry_buffer_s 전 선제 재발급, refresh()=즉시 _fetch_token, Clock 주입
  def build_auth(spec: AuthSpec | None, client: httpx.Client, *, clock=None) -> AuthProvider | None
  ```
- **TDD:** `test_static_token_headers`(env→Bearer), `test_static_refresh_is_fatal`, `test_oauth2_refreshes_before_expiry`(respx token endpoint 목 + FakeClock: expires_in=100/buffer60 → 40s 재발급 안함, 61s 재발급).

### U-D2 · rest.py auth 병합
- **결정:** `__init__`에 `auth: AuthProvider | None = None`. fetch()에서 요청 헤더에 `**(self._auth.headers() if self._auth else {})` 병합. 401은 기존 classify가 `AuthExpiredError` 발생 → 러너가 처리(D3).
- **TDD:** `test_auth_headers_merged_into_request`.

### U-D3 · runner refresh 루프
- **결정(확정):** `_build_source`에서 rest면 `auth = build_auth(src.auth, client, clock=...)` 만들어 RestSource에 주입하고, 러너 루프의 fetch 호출을 감싼다:
  ```python
  try:
      result = with_retry(functools.partial(source.fetch, unit), max_attempts=max_attempts)
  except AuthExpiredError:
      if auth is None: raise
      auth.refresh()
      result = with_retry(functools.partial(source.fetch, unit), max_attempts=max_attempts)  # unit당 1회 갱신
  ```
  - auth 핸들을 러너가 알아야 함 → `_build_source`가 `(source, auth)` 튜플 반환하거나, source에 `auth` 속성 노출. **결정: `_build_source`는 source만 반환하고, auth는 `getattr(source, "_auth", None)`로 러너가 접근**(rest만 보유). 또는 더 깔끔히 source에 `on_auth_expired()` 훅. **확정: getattr 방식**(가장 적은 변경, close() 선례와 동일 철학).
- **TDD:** `test_401_triggers_refresh_and_same_unit_retry`(FlippingProvider: old→401, refresh후 new→200, `report.written==1`, `refresh_count==1`).
- **함정:** AuthExpiredError는 RetryableError 서브클래스 → with_retry가 이미 재시도함. **그래서 401이 max_attempts만큼 재시도된 뒤에야 밖으로 나온다.** 갱신 없이 무의미 재시도를 피하려면: AuthExpiredError는 with_retry 대상에서 제외하고 러너 루프가 직접 잡도록 조정하거나, refresh 콜백을 fetch 재시도 안으로 넣는 설계 중 택1. **확정:** `with_retry`의 재시도 대상을 `RetryableError but not AuthExpiredError`로 좁히는 대신, 러너에서 AuthExpiredError를 첫 발생 시 즉시 잡게 하려면 fetch를 with_retry로 감싸되 AuthExpiredError는 tenacity가 재시도하지 않도록 별도 처리 필요 — **이 지점이 D의 유일한 설계 난소.** 구현자는 다음 중 하나를 택하고 테스트로 증명: (i) `with_retry`에 `retry_on`을 파라미터화해 auth 배치에서만 AuthExpired 제외, (ii) 러너가 fetch를 두 겹(안쪽 with_retry는 순수 RetryableError, 바깥은 AuthExpired 1회 캐치)으로 감싸기. **권장 (ii)** — retry.py를 안 건드림. 막히면 fable.

### U-D4 · 커밋
- **커밋:** `feat: auth providers with expiry-triggered refresh`

---

# 배치 M2-E — 검증 게이트 + DLQ (Scutum 코어)

> 파일: 신규 `pugio/validate/gate.py`, 신규 `pugio/dlq.py`, `arsenal_core/state/store.py`, `pugio/runner.py`, `pugio/cli.py`, 테스트 `test_validate_gate.py`·`test_dlq.py`. **rest.py 무관 → B와 병렬 가능.**

### U-E1 · 벡터화 검증 게이트
- **결정(확정):** 상위 플랜 Task 2.6 Step 2 `check(batch, rules) -> GateReport` 그대로(pyarrow.compute, 행 루프 금지). `Violation(rule, field, count)`, `GateReport.ok`. 없는 필드는 FatalError(schema 위반).
- **TDD:** `test_not_null_violation_detected`, `test_range_and_unique`, `test_clean_batch_passes`, `test_missing_field_is_fatal`.

### U-E2 · StateStore.mark_quarantined
- **결정:** `def mark_quarantined(self, uid: str, reason: str) -> None` — `_set_status(uid,"quarantined", reason)` 재사용(단 attempts bump는 quarantine엔 불필요 → `_set_status` 시그니처에 영향 주지 말고 별도 UPDATE 작성). `quarantined` 조회용 `def quarantined(self, pipeline) -> list[UnitRecord]`.
- **TDD:** `test_mark_quarantined_sets_status`, `test_quarantined_lists_units`.
- **함정:** `quarantined` status는 이미 enum에 있음. `is_done`은 여전히 False여야(격리 unit은 done 아님).

### U-E3 · DLQ 파일 쓰기 (pugio/dlq.py)
- **결정(확정):** `write_dlq(state_dir, pipeline, unit, batch, violations) -> None` — `{state_dir}/dlq/{pipeline}/{unit_id}.parquet` + 동명 `.json`(violations 상세·unit_key·시각). `read_dlq_reason(path)`. DLQ 파일은 증거이지 재적재 소스 아님(retry는 재fetch).
- **TDD:** `test_dlq_writes_parquet_and_reason_json`.

### U-E4 · runner 배선
- **결정(확정):** write 직전 `if spec.validation: report = check(batch, spec.validation.rules)`. 정책:
  - `block` → FatalError(중단).
  - `warn` → typer.echo 경고 후 정상 write·mark_done.
  - `quarantine` → `write_dlq(...)` + `store.mark_quarantined(uid, reason)` + **write 건너뜀** + 파이프라인 계속(break 아님, continue).
  - **주의:** on_violation 정책 접근은 `spec.validation.on_violation`.
- **TDD:** `test_quarantine_isolates_unit_and_run_continues` — 2번째 unit에 null → `counts=={"done":2,"quarantined":1}`, DLQ 파일 1개.

### U-E5 · CLI dlq 서브커맨드
- **결정(확정):** `pugio dlq list <yaml>`(격리 unit 표: unit_key/사유), `pugio dlq retry <yaml> --unit <id>`(상태→pending 복귀 + DLQ 파일 제거 → 다음 run이 재fetch). typer sub-typer `dlq_app`를 `app.add_typer(dlq_app, name="dlq")`.
- **TDD:** `test_dlq_list_shows_quarantined`, `test_dlq_retry_requeues_unit`(retry 후 status=="pending", 파일 삭제됨).

### U-E6 · 배치 리뷰 + 커밋
- **커밋:** E1~E4 `feat: vectorized validation gate with quarantine policy`, E5 `feat: dead letter queue list/retry`. DoD: 위반 unit 격리돼도 완주 + retry 동작.

---

# 배치 M2-F — DB 멱등 sink

> 파일: 신규 `pugio/sinks/duckdb.py`·`pugio/sinks/postgres.py`, `pugio/sinks/__init__.py`, `pugio/runner.py`, `pugio/pyproject.toml`, 테스트 `test_duckdb_sink.py`·`test_postgres_sink.py`. **rest.py 무관 → B와 병렬 가능.**

### U-F1 · build_sink 팩토리 + runner 교체
- **결정(확정):**
  ```python
  # sinks/__init__.py
  def build_sink(spec: SinkSpec) -> Sink:
      if spec.type == "parquet": return ParquetSink(Path(spec.path))
      if spec.type == "duckdb":  return DuckDBSink(spec)
      if spec.type == "postgres":return PostgresSink(spec)
      raise FatalError(f"unknown sink type: {spec.type}")
  ```
  - runner: `sink = ParquetSink(Path(spec.sink.path))` → `sink = build_sink(spec.sink)`. A3의 임시 pyright 가드 제거.
- **TDD:** `test_build_sink_parquet_returns_parquet_sink` 등 각 타입.

### U-F2 · DuckDBSink (temp→delete+insert 트랜잭션)
- **결정(확정):** 상위 플랜 Task 2.8 Step 2 구현 그대로. `write(unit, batch)`: `duckdb.connect(path)` → `register("staging", Table.from_batches([batch]))` → `CREATE TABLE IF NOT EXISTS ... LIMIT 0` → `BEGIN; DELETE ... WHERE EXISTS(staging 키 매칭); INSERT ... SELECT * FROM staging; COMMIT`. 예외 시 ROLLBACK + RetryableError. `_quote_ident` 재사용(gladius의 것 참조 또는 자체).
- **TDD (계약 스위트):** `test_write_twice_same_unit_row_count_unchanged`(멱등), `test_merge_updates_changed_rows`(upsert 의미).
- **함정:** merge_key 컬럼 인용, DELETE에 별칭 안 씀(방언 호환).

### U-F3 · PostgresSink (testcontainers)
- **결정(확정):** 상위 플랜 Task 2.9. `pyproject.toml`에 `[project.optional-dependencies] postgres=["psycopg[binary]>=3.1"]`, dev에 `testcontainers[postgres]`. temp table→copy→`INSERT ... ON CONFLICT (keys) DO UPDATE`. `_arrow_to_pg_type`(int64→bigint, string→text, timestamp→timestamptz, double→double precision, bool→boolean, 그 외 FatalError).
- **TDD:** `pytest.importorskip("testcontainers.postgres")` → Docker 없으면 skip. DuckDB와 동일 계약 스위트 파라미터라이즈.
- **⚠️ 외부 의존:** Docker 필요. **CI/로컬에 Docker 없으면 이 U는 skip 처리하고 "Docker 환경에서 재검증 필요"로 원장 기록** — 구현·타입은 완성하되 실통과는 환경 가용 시.

### U-F4 · 공통 계약 스위트
- **결정:** `tests/_sink_contract.py`에 파라미터라이즈 가능한 계약 함수(멱등·원자성) 정의, parquet/duckdb/postgres가 공유.
- **TDD:** 세 sink가 동일 계약 통과(postgres는 Docker 가용 시).

### U-F5 · 커밋
- **커밋:** F1~F2·F4 `feat: duckdb sink with transactional merge idempotency`, F3 `feat: postgres sink with on-conflict upsert`. **보너스:** 이 배치 후 M4의 api-to-postgres 레시피 부활 가능(검증 게이트 필요 시 E 선행).

---

# 배치 M2-G — DatabaseSource 실커넥터 + 스키마 스냅샷

> 파일: `pugio/sources/database.py`, `arsenal_core/state/store.py`, `pugio/runner.py`, 테스트. **A 이후 언제든. 재-핀 대상: 아래 결정은 F 산출물과 무관하나 실행 전 database.py 현행 재확인.**

### U-G1 · postgres/mysql 커넥터 (DuckDB scanner)
- **결정(확정):** draft의 sqlite3 경로는 **그대로 유지**. dialect가 postgres/mysql이면 DuckDB scanner 경로 분기:
  ```python
  con = duckdb.connect()
  con.execute(f"ATTACH '{dsn}' AS src (TYPE {dialect.upper()}, READ_ONLY)")
  # units(): SELECT min/max(key) FROM src.table → 키범위. fetch(): WHERE key>=? AND key<?
  ```
  - sqlite는 기존 stdlib 경로, postgres/mysql은 duckdb scanner — dialect로 분기하는 두 백엔드.
  - 에러 분류: 기존 `_raise_classified` 재사용 또는 duckdb.Error→분류.
- **TDD:** sqlite 기존 테스트 유지. postgres는 testcontainers 1케이스(Docker 없으면 skip). `test_units_are_key_ranges` 형태.
- **⚠️ 외부 의존:** postgres/mysql 실검증은 Docker/testcontainers. 없으면 skip + 원장 기록.

### U-G2 · 스키마 스냅샷
- **결정(확정):** `StateStore.snapshot_schema(pipeline, schema_json)`(변경 시에만 append — 직전과 같으면 no-op), `last_schema(pipeline) -> str|None`. 테이블 `schema_snapshots` 이미 존재. 러너: 각 run 첫 non-empty batch에서 `json.dumps(schema 필드명·타입·nullable)` → snapshot_schema. **감지·정책은 P1 — 여기선 기록만.**
- **TDD:** `test_snapshot_appends_only_on_change`(같은 스키마 2회 → 1행), `test_last_schema_returns_latest`.

### U-G3 · 커밋
- **커밋:** `feat: database postgres/mysql via duckdb scanner + schema snapshot recording`

---

# 배치 M2-H — 실전 API 5종 검증 + M2 마무리

> 파일: `examples/real-world/*.yaml`, `docs/reference/api-coverage.md`, `tests/e2e/`, 테스트. **B·C·D·F 완료 후(모든 기능이 있어야 표현 가능).**

### U-H1 · 실전 API 5종 YAML + respx 계약 테스트
- **결정(확정):** 상위 플랜 Task 2.14 표 그대로 — GitHub(link+시간당 rate), Stripe(cursor+record_path:data), 공공데이터포털(euc-kr+page+키 파라미터), Notion(cursor+3rps+POST 검색 → method 필드 필요성 판정), Slack(cursor next_cursor+429). 각 YAML을 respx 목으로 계약 테스트(실계정 불요).
- **결정:** POST 검색 API(Notion)에서 `method` 필드가 필요하면 → RestSourceSpec에 `method:Literal["GET","POST"]="GET"`+`body` 추가를 **H에서 스펙 확장으로 처리**(A로 소급 아님, 여기서 판정·구현).
- **TDD:** 5개 respx 계약 테스트.

### U-H2 · api-coverage.md 기록
- **결정:** 각 API의 표현 가능/불가(→Python 탈출구 판정)를 정직하게 기록. 숨기지 않음.

### U-H3 · live E2E(opt-in) + examples + 최종 게이트 + 커밋
- **결정:** `tests/e2e/test_live_github.py`(`RUN_LIVE=1`일 때만, CI 기본 제외). `examples/`에 cursor·duckdb sink·validate 예제 추가. 최종 게이트 통과 확인.
- **커밋:** H1 `test: real-world api coverage verification`, H3 `feat: m2 complete — production-ready pugio`.
- **M2 DoD 최종 체크:** 상위 문서 "M2 DoD" 8개 항목 전부 충족 확인.

---

## 실행 순서 요약 (착수 큐)

```
1. M2-A (A1→A5 구현 → A6 회귀+커밋)                    [단일 배치, ~25분]
2. M2-B (B1→B7 → B8 리뷰+커밋)                         [rest.py #1, ~50-60분]
3. M2-C (C1→C3 → C4)                                   [rest.py #2, B머지후, ~35분]
4. M2-D (D1→D3 → D4)                                   [rest.py #3, C머지후, ~35분]
   ※ E·F는 rest.py 무관 → 2~4와 병렬 dispatch 가능
5. M2-E (E1→E5 → E6)                                   [~45분]
6. M2-F (F1→F4 → F5)                                   [~50분, Postgres는 Docker 가용시]
7. M2-G (G1→G2 → G3)                                   [~35분, PG는 Docker 가용시]
8. M2-H (H1→H3)                                         [~40분, 마지막]
```

## 모델 라우팅

- **기본:** 구현·리뷰 = Sonnet. 대부분의 U는 결정이 확정돼 있어 Sonnet으로 충분.
- **Opus 권장 U:** U-B4(cursor 상태 추적), U-B6(재개 배선), U-D3(auth 재시도 설계 난소), U-F2(트랜잭션 MERGE) — 결정은 확정했으나 상태·트랜잭션 추론이 얽히는 지점. 처음부터 Opus로 dispatch하면 fable 승격 확률↓.
- **fable:** 위 U에서도 막히면 그때만 승격(예상 빈도 매우 낮음 — 설계가 선확정됐으므로).
- 각 배치 완료 → 태스크 리뷰(Sonnet) → fix 일괄 → 재리뷰 → 원장. M2 전체 완료 후 whole-branch 최종 리뷰 = 가장 강한 모델.

## 외부 의존 체크리스트 (사람이 준비해야 진행되는 것)

- [ ] **Docker** — M2-F(Postgres sink), M2-G(postgres/mysql 커넥터), M2-H(선택) 실검증용. 없으면 해당 U는 구현·타입 완성 + `pytest.importorskip` skip + 원장에 "Docker 재검증 필요" 기록.
- [ ] **GitHub 토큰** — M2-H live E2E(`RUN_LIVE=1`). 없으면 respx 계약 테스트까지만.
- 이 둘을 제외한 전 구간은 respx/sqlite/FakeClock 목으로 네트워크·도커 없이 완주 가능.
