# 05. 테스트 계획

## 원칙

- **TDD**: 모든 기능은 실패하는 테스트부터. RED → GREEN → REFACTOR.
- **커버리지 80% 최소** (CI 게이트 `--cov-fail-under=80`). 단, 커버리지는 바닥이지 목표가 아니다 — 신뢰성 시나리오 테스트가 진짜 목표.
- **신뢰성이 제품이다**: "끊겨도 중복·누락 없음"은 마케팅 문구가 아니라 테스트로 증명되는 계약. 재개·멱등 시나리오 테스트가 이 저장소에서 가장 중요한 테스트다.
- 결정성: 시간·난수·네트워크에 의존하는 테스트 금지. 시계는 주입, 네트워크는 respx 모킹, sleep 대신 가짜 클록.

## 테스트 레벨

### 1. 단위 테스트 (`packages/*/tests/`)

각 패키지 안에 위치. 밀리초 단위로 빠르게, 외부 자원 없음.

| 대상 | 핵심 케이스 |
|---|---|
| `identity` | 같은 입력→같은 ID(hypothesis), 다른 입력→다른 ID, 포맷(16 hex) |
| `errors` | HTTP 상태→예외 매핑 표: 401→AuthExpired, 429/5xx→Retryable, 그 외 4xx→Fatal |
| `state.StateStore` | upsert 멱등, 상태 전이 규칙, pending 조회가 done 제외, 프로세스 재시작 시뮬레이션(재-open) |
| `spec` | 유효 YAML 파싱, 필수 필드 누락 시 필드 경로가 담긴 에러, `${ENV}` 치환, 알 수 없는 필드 거부 |
| `retry` | Retryable만 재시도, Fatal 즉시 전파, 최대 횟수 소진 |
| REST source | offset/page/cursor unit 열거, 응답→RecordBatch 변환, 인코딩(euc-kr 픽스처) |
| Parquet sink | 결정적 파일명, 같은 unit 2회 write→파일 1개·내용 동일 |
| 트랜스파일러 | step별 SQL 스냅샷, 식별자 인용, 인젝션 시도 입력 무해화 |

### 2. 통합 테스트 (`tests/integration/`)

실제 컴포넌트 조합. SQLite·DuckDB는 실물(in-process라 가볍다), HTTP는 respx, PostgreSQL은 testcontainers.

- **Runner × StateStore × Sink**: 5-unit 파이프라인 정상 완주 → units 전부 done, parquet 5개
- **재시도 경로**: 3번째 요청만 500 응답 → backoff 후 성공, attempts 기록 확인
- **인증 갱신 경로** (M2): 401 → refresh hook 호출 → 새 토큰으로 같은 unit 재시도 성공
- **검증 게이트** (M2): null 위반 배치 → quarantine 정책이면 DLQ에 격리 + 나머지 unit은 계속
- **DB sink 멱등** (M2): 같은 unit 2회 write → row 수 불변 (DuckDB in-process, PG는 testcontainers)
- **Pugio→Gladius 허브** (M3): Pugio가 쓴 parquet을 Gladius가 읽어 변환 — 스키마 보존 확인

### 3. 신뢰성 시나리오 테스트 (`tests/e2e/`) — 이 프로젝트의 심장

제품 약속을 그대로 재현하는 테스트. 마일스톤 DoD와 1:1 대응.

```python
def test_kill_and_resume_no_dup_no_loss(tmp_path, mock_api_100_pages):
    """시나리오 B: 수집 도중 죽어도, 재실행하면 이어서. 중복 0, 누락 0."""

    def crash_after(done_count: int) -> None:  # 37번째 unit 완료 직후 프로세스 사망 시뮬레이션
        if done_count >= 37:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        run_pipeline(spec, on_unit_complete=crash_after)

    report = run_pipeline(spec)  # 2차 실행: 같은 명령 그대로

    # 검증 1: 전체 행 = 소스 행과 정확히 일치 (중복·누락 0)
    total, distinct = duckdb.sql(
        f"SELECT count(*), count(DISTINCT id) FROM read_parquet('{tmp_path}/*.parquet')"
    ).fetchone()
    assert total == distinct == SOURCE_ROW_COUNT
    # 검증 2: 완료했던 unit은 다시 받지 않음 (재개 비용 ≤ 청크 1개 + 남은 것)
    assert report.skipped == 37
```

시나리오 목록:

| 테스트 | 약속 | 마일스톤 |
|---|---|---|
| kill→재실행: 중복·누락 0 | "끊겨도 이어서" | M1 |
| 완주 후 재실행: no-op (fetch 0회) | "재실행은 정상 동작" | M1 |
| 토큰 만료 중 갱신 후 완주 | "만료는 갱신 트리거" | M2 |
| 429 폭주 소스에서 감속 완주 | "rate limit 흡수" | M2 |
| 오염 데이터 격리 후 완주 + DLQ 재투입 | "나쁜 데이터 차단" | M2 |
| map/steps 결과 = 손 SQL 결과 (golden) | "선언이 SQL과 동치" | M3 |

### 4. 속성 기반 테스트 (hypothesis)

- `unit_id`: 임의 문자열 조합에 대해 결정성·충돌 저항·포맷 불변
- 트랜스파일러: 임의 step 체인에 대해 (a) 생성 SQL이 DuckDB에서 파싱 성공, (b) select→select 축약 등 최적화 전후 결과 동치
- StateStore: 임의 상태 전이 시퀀스 후 불변식(done은 되돌아가지 않음 등) 유지

### 5. 실환경 스모크 (opt-in)

- GitHub 공개 API 실수집 — `RUN_LIVE=1`일 때만. CI 기본 제외(외부 의존 플레이키 방지)
- 벤치마크: 1GB parquet 변환 시간 — 회귀 감지용 기록, 게이트는 아님

## 테스트 픽스처 전략

- `tests/fixtures/mock_api.py`: respx 기반 가짜 REST 서버 빌더 — 페이지 수·페이지 크기·실패 주입(N번째 요청에 지정 상태코드)·euc-kr 응답을 파라미터로 구성
- 크래시 주입: 러너에 테스트 전용 훅(`on_unit_complete` 콜백)을 두고 예외를 던져 프로세스 사망을 결정적으로 재현 — `kill -9` 실물 테스트는 별도 스크립트로 수동 검증 1회
- 시간: `Clock` 프로토콜 주입(rate limiter·backoff) — 테스트는 가짜 클록으로 sleep 없이 검증

## Sink 공통 계약 테스트

모든 sink 구현이 통과해야 하는 파라미터라이즈드 스위트 (새 sink 추가 시 자동 적용):

```python
@pytest.mark.parametrize("sink", all_sink_fixtures())
class TestSinkContract:
    def test_write_twice_same_unit_is_idempotent(self, sink): ...
    def test_partial_failure_leaves_no_visible_partial_data(self, sink): ...
    def test_concurrent_units_do_not_interfere(self, sink): ...
```

## CI 파이프라인

```text
push/PR → ruff check → pyright → pytest(unit+integration, cov≥80)
                                    └─ PG testcontainers job (Linux만)
main merge → 위 전체 + e2e 시나리오
release tag → 전체 + 스모크(RUN_LIVE) + uv build 검증
```

## 커버리지 예외

- CLI 출력 포맷팅 코드는 `# pragma: no cover` 허용 (로직은 커버 필수, 프린트만 예외)
- `Protocol` 정의 자체는 측정 제외
