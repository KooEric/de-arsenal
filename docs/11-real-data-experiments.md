# 11. 실데이터 실험 결과 (2026-11-18)

테스트 스위트가 아니라 **실제 API와 실제 데이터**로 DE Arsenal의 약속(재개, 멱등, 검증, 단순함)이
지켜지는지 확인했다. 결론부터 말하면 크래시 내성과 멱등성은 모든 실험에서 지켜졌고, 테스트가
놓친 **조용한 데이터 손실 버그 2건**을 찾아 0.5.3에서 고쳤으며, 아직 고치지 않은 **설계 한계
2건**을 확인했다.

- 재현용 스펙: [`experiments/real-data/`](../experiments/real-data/README.md)
- 환경: Linux 4 vCPU, 15 GB RAM, Python 3.13, v0.5.2(버그 발견) → v0.5.3(수정 확인)
- 모든 숫자는 실제 실행 결과다. 추정치는 없다.

## 한눈에

| # | 실험 | 데이터 | 결과 |
|---|---|---|---|
| 1 | 수집 중 `kill -9` 후 재개 | GitHub API, 커밋 169건 | 중복 0, 누락 0. 완료 후 재실행 0.70초 no-op |
| 2 | link 페이지네이션 + 크래시 | GitLab API, 이슈 9,554건 (96페이지) | API `x-total` = 적재 9,554 = 고유 id 9,554 |
| 3 | 검증 게이트 + DLQ + 변환 | 공항 CSV 86,343행, 오염 3건 주입 | 오염 파일만 격리, 원천 수정 후 그 파일만 재처리 |
| 4 | 스트리밍 반복 크래시 | 로컬 cursor API, 24,000 이벤트 | `kill -9` 6회 후 누락 0, 중복 0 |
| 5 | 실행 간 스키마 변화 감지 | NYC 택시 2022-01 → 2024-01 | 컬럼 이름 변경 1개 + 타입 변경 6개를 짚어 적재 차단 |
| 6 | 대량 적재 + 크래시 | NYC 택시 2024 Q1, 955만 행 | 4.0초(초당 240만 행). 재개 후 행 수·금액 합계가 원본과 일치 |
| 7 | 다중 스키마 변환 | NYC 택시 5개월, 1,970만 행 | 5.96초에 1,920만 행 정제. 대소문자가 다른 컬럼도 합쳐짐 |
| 8 | 자연어 → SQL | Augur 평가 케이스 34건, Gemini 무료 티어 | 34/34 정답. 함정 질문도 통과했지만 평가 세트가 너무 쉽다 |
| — | 벤치마크 재현 | 합성 1 GB / 1,300만 행 | 4코어에서 0.376초 (M3 Pro 12코어 기록 0.105초) |

## 찾아서 고친 버그 (0.5.3)

두 버그 모두 에러 없이 "성공"으로 끝났다. 690개 테스트와 커버리지 89%로는 보이지 않았다.

### `github-issues` 레시피가 첫 페이지만 수집

- **현상:** 실제 GitHub API에서 이슈 21건 중 **6건(28.6%)**만 받고 성공 종료.
- **원인:** 레시피·예제·README가 `mode: offset, param: page`. offset 모드는 `0, size, 2×size…`를
  보내는데 GitHub의 `page`는 페이지 번호라서 `page=0` 다음 `page=5`로 건너뛴다.
- **숨은 원인:** e2e 테스트가 `page=0` 요청을 mock해서 버그 동작을 정답으로 고정하고 있었다.
- **수정:** `mode: page`로 변경. offset 모드에 `page`·`page_number`·`pageNo` 같은 파라미터를 쓰면
  실행 전 스펙 검증에서 고칠 방법과 함께 거부한다.
- **확인:** 수정 후 실제 API에서 21/21.

### CSV의 `NA`가 파일 분할 방식에 따라 NULL로 바뀜

- **현상:** 공항 CSV를 통파일로 넣으면 `continent='NA'`(북미)가 보존되는데, 대륙별로 나눠 넣으면
  **북미 39,842행(전체의 46%)의 `continent`가 NULL**.
- **원인:** pyarrow CSV 기본값은 `NA`·`N/A`·`null`을 null 토큰으로 본다. 파일 안의 값이 전부 `NA`면
  컬럼 타입이 `null`로 추론돼 값이 사라진다. 섞여 있으면 문자열로 추론돼 보존된다.
- **단서:** `schema_drift: block`은 놓쳤지만 `aquila history`에는 `continent: string → null`이
  기록돼 있었다.
- **수정:** 빈 셀만 null로 읽는다. 필요하면 새 `null_values` 필드로 토큰을 지정한다.
- **확인:** 수정 후 북미 39,842행과 나미비아(`NA`) 303건 보존. 빈 고도 셀 14,980건은 여전히 NULL.

## 확인했지만 아직 고치지 않은 한계

### 검증 게이트가 단위 전체를 격리한다 → 실데이터에서 적재 0건

NYC 택시 2024 Q1에 `total_amount >= 0`, `0 <= trip_distance <= 200` 규칙을 걸었다.

| 품질 지표 (955만 행) | 건수 |
|---|---:|
| `total_amount < 0` (환불·취소) | 115,895 (1.2%) |
| `trip_distance = 0` | 215,764 |
| `trip_distance > 200` | 170 |
| 하차 시각 ≤ 승차 시각 | 2,801 |
| `passenger_count` NULL | 751,962 |

위반은 1~2%인데 **10개 단위(row group, 약 100만 행씩) 모두 격리되고 적재 0건**이었다. 검증은
행이 아니라 단위 단위로 판정하고, 실데이터는 어느 덩어리에나 환불 건이 섞여 있기 때문이다.
API 페이지처럼 단위가 작으면 문제가 드러나지 않는다.
**개선 방향:** 위반 행만 DLQ로 보내고 나머지는 적재하는 행 단위 정책(`on_violation: split`).

### 스키마 변화 감지가 한 실행 안의 파일 간 차이를 못 본다

| 상황 | 결과 |
|---|---|
| 실행 간: NYC 2022-01을 적재한 뒤 같은 파이프라인으로 2024-01 | **차단.** `added=Airport_fee; removed=airport_fee; changed=DOLocationID,PULocationID,RatecodeID,VendorID,passenger_count,store_and_fwd_flag` |
| 한 실행 안: 공항 CSV 7개 파일 중 하나만 `continent`가 null 타입 | **통과.** 실행의 첫 batch만 이전 실행과 비교한다 (`docs/08-limits.md`에 문서화된 한계) |

**개선 방향:** 단위마다 직전 단위의 스키마와 비교. Aquila는 이미 파일 footer로 같은 변화를 잡고 있다.

## 실험별 상세

### 1. GitHub API: 수집 중 강제 종료

본인 레포 커밋 169건을 페이지당 5건(34페이지)으로 받다가 `kill -9`.

```text
$ pugio status gh-commits.yaml        # kill -9 직후
done: 8
running: 1
$ pugio run gh-commits.yaml           # 즉시 재실행
error: pipeline 'gh-commits' is busy (lease held by pugio-6b4b… until 02:17:16)
$ pugio run gh-commits.yaml           # lease 만료 후
done: fetched=26 written=26 skipped=8 quarantined=0
$ pugio run gh-commits.yaml           # 완료 후
done: fetched=1 written=0 skipped=34 quarantined=0      # 0.70s
```

죽은 러너의 lease가 살아 있는 동안 이중 실행을 막았고, 재개 시 완료된 8페이지를 건너뛰었다.
최종 169행 = 고유 sha 169개.

### 2. GitLab API: link 페이지네이션 9,554건

gitlab-runner 이슈 전체(2014~2026)를 `Link` 헤더를 따라 100건씩, 초당 5요청 제한으로 받다가 8초 만에 `kill -9`.

```text
$ pugio status gl-issues.yaml         # kill -9 직후
done: 6
running: 1
$ pugio run gl-issues.yaml            # 재개
done: fetched=90 written=90 skipped=0 quarantined=0
$ count(*), count(DISTINCT id)  /  API 헤더 x-total
9554, 9554  /  9554
$ pugio run gl-issues.yaml            # 완료 후
done: fetched=0 written=0 skipped=0 quarantined=0       # 0.44s
```

link 모드는 다음 URL을 미리 알 수 없어서 마지막 커서를 상태에 저장한다. 재개는 저장된 커서에서
바로 이어 갔다(완료된 6페이지 재요청 없음). 96페이지, 39 MB.

GitHub에서는 같은 실험이 불가능했다. GitHub의 `Link` 헤더가 `/repositories/{숫자ID}/…` 경로를
주는데 실험 환경 프록시가 그 경로를 막았다. 이때 Pugio는 해당 페이지를 `failed`로 기록하고 받은
데이터는 그대로 두었다.

### 3. 공항 CSV: 검증 게이트, DLQ, 변환

86,343행 × 13열(8.8 MB). icao 코드 87.7% 결측, 다국어 이름, 좌표는 `"lat, lon"` 문자열. 매일
파일이 떨어지는 상황을 흉내 내 대륙별 7개 파일로 나누고 EU 파일 3행의 고도를 `999999`로 오염.

| 단계 | 결과 |
|---|---|
| 1차 실행 | EU 파일만 격리, 사유 `max:elevation_ft=3`. 나머지 6개 파일 73,701행 적재, 0.50초 |
| 원천 수정 후 | 정상 6개는 건너뛰고 EU만 재처리. 최종 86,343행 = 원본, `ident` 중복 0 |
| Gladius 변환 | filter → derive(좌표 파싱, ft→m) → dedup → select가 CTE SQL 하나로 컴파일, 0.29초, 4,572개 공항 |
| 검증 쿼리 | 최고 고도 공항 Daocheng Yading(DCY) 4,411 m, 실제와 일치 |

### 4. Hasta 스트리밍: 반복 크래시

초기 2만 건 + 초당 250건씩 생기는 로컬 cursor API를 받으면서 0.8~2.5초 간격으로 `kill -9` 6회,
이후 15초 정상 실행 후 SIGINT.

```text
$ hasta status orders.yaml            # 크래시 직후
"status": "stalled", "reasons": ["a runner holds an expired lease (it likely died); it will be taken over"], "fence": 6
$ ^C
received SIGINT: finishing the current batch, then releasing the lease
$ count(*), count(DISTINCT id), min(id), max(id)
rows=24000 distinct=24000 min=1 max=24000 gaps=0 dups=0
```

### 5~7. NYC 택시: 1,970만 행

| 파일 | 행 수 | 스키마 (2024-03 대비) |
|---|---:|---|
| 2019-01 | 7,696,617 | `airport_fee` INTEGER(이름 소문자), `passenger_count` DOUBLE, ID 컬럼 BIGINT |
| 2022-01 | 2,463,931 | `airport_fee` DOUBLE(이름 소문자), `passenger_count` DOUBLE |
| 2024-01~03 | 9,554,778 | 기준 (`Airport_fee`, `passenger_count` BIGINT) |

Pugio에는 Parquet 소스가 없어서 [`nyc_source.py`](../experiments/real-data/nyc-taxi/nyc_source.py)
(row group 하나 = 단위 하나, 30줄)를 `type: python` 탈출구로 붙였다.

- **실행 간 스키마 변화(5):** 위 "확인했지만 아직 고치지 않은 한계" 표 참고. 실행 간 변화는 정확히 차단했다.
- **대량 적재(6):** 2024 Q1을 10개 단위로 4.0초(초당 약 240만 행). 5개 단위 완료 시점에
  `kill -9` → 반쯤 쓰인 파일 없음 → 재개 시 5개 skip, 5개 적재 →
  **행 수 9,554,778, `total_amount` 합계 256,692,913.53이 원본과 정확히 일치.**
- **다중 스키마 변환(7):** 5개 파일을 한 번에 Gladius로 정제(유효 운행 필터, 운행 시간·속도 파생)해
  1,970만 → 1,920만 행을 **5.96초**에. `airport_fee`와 `Airport_fee`는 이름 대소문자 무시로 합쳐졌다.

| 월 | 운행 수 | 평균 결제액 | 중앙 속도(mph) | 팁 비율 |
|---|---:|---:|---:|---:|
| 2019-01 | 7,585,097 | $15.64 | 10.1 | 15.6% |
| 2022-01 | 2,409,503 | $19.18 | 11.1 | 21.7% |
| 2024-01 | 2,861,415 | $27.29 | 9.7 | 21.4% |
| 2024-02 | 2,896,198 | $27.17 | 9.5 | 20.6% |
| 2024-03 | 3,442,400 | $27.72 | 9.6 | 19.2% |

2019년 1월 대비 2024년 3월 평균 결제액은 77% 올랐다.

### 8. Augur: 자연어 → SQL (Gemini 무료 티어)

Augur는 원래 Anthropic·OpenAI만 지원했다. 무료로 평가하려고 `OPENAI_BASE_URL`로 OpenAI 호환 API를
쓸 수 있게 했다(이번 변경). 평가 세트는 레포의
[`cases.yaml`](../packages/augur/eval/cases.yaml) 34건이다. 고객·상품·주문 3개 합성 테이블 위에서
L1 단일 집계부터 L5 함정(답할 수 없는 질문, 없는 값, 한국어 질문)까지 나뉜다.

| 모델 | 결과 |
|---|---|
| `gemini-3.1-flash-lite`, 검색 폭 3 | **34/34 정답 (100%)**, repair 0회. 중앙 지연 1.6초, 입력 8,860 토큰, 출력 905 토큰 |
| `gemini-flash-latest` (= gemini-3.8-flash) | 측정 불가. 무료 한도가 **하루 20요청**이라 19건에서 429로 멈췄다 |

| 난이도 | 건수 | 정답 | 중앙 지연 |
|---|---:|---:|---:|
| L1 단일 테이블 집계 | 6 | 6 | 1.1 s |
| L2 필터 + 그룹 | 10 | 10 | 1.1 s |
| L3 JOIN | 7 | 7 | 2.6 s |
| L4 시간·윈도우 | 5 | 5 | 2.8 s |
| L5 함정 | 6 | 6 | 4.7 s |

L5에서 확인한 것:
- 답할 수 없는 질문 3건(배송비, 직원, 삭제 요청)에는 SQL을 지어내지 않고 `UNANSWERABLE`을 냈다.
- 데이터에 없는 값 `COMPLETED`는 샘플값을 보고 `paid`로 매핑했다.
- 한국어 질문 2건도 영문 스키마에서 맞는 테이블을 찾았다.

**해석:** 100%는 모델이 완벽하다는 뜻이 아니라 **이 평가 세트가 변별력이 없다**는 뜻으로 읽는다.
테이블 3개, 컬럼 수십 개짜리 작은 스키마라 검색과 생성이 모두 쉽다. 다음 단계는
[평가 프로토콜](10-augur-eval-protocol.md)대로 실패가 나는 지점(큰 스키마, 모호한 컬럼명)에
케이스를 늘리거나, Spider·BIRD 같은 공개 벤치마크로 측정하는 것이다.

### 벤치마크 재현

[`benchmarks/`](../benchmarks/RESULTS.md) 스크립트를 다른 하드웨어에서 그대로 돌렸다.

| 워크로드 | M3 Pro 12코어 (기존 기록) | Linux 4 vCPU (이번) |
|---|---:|---:|
| 합성 1 GB / 1,300만 행 Parquet 변환 | 0.105 s | 0.376 s (3,460만 행/s) |
| 스트리밍 100만 이벤트 | 2.99 s | 3.26 s (30.7만 ev/s) |
| 완료된 스트림 재실행 | 0.9 ms | 0.8 ms |

변환은 코어 수에 거의 비례해 느려졌다. 합성 데이터·warm cache·네트워크 제외라 엔진의 상한으로 읽는다.

## 하지 못한 것

- **위키피디아 API:** 실험 시점에 실험 환경의 공용 IP가 Wikimedia 요청 한도(429 "too many requests")에 걸려 있었다.
- **Postgres sink, Ballista 실클러스터:** 이번 범위 밖(테스트 33개가 컨테이너·Kubernetes 필요로 skip).
- **장기 운영:** 모두 단발 실행이다. [도그푸딩 2주 운영](dogfooding.md)은 아직 하지 않았다.
