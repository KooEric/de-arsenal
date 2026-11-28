# Vigil 설계 (v0.8.0)

> 야간 순찰대. 정규화된 보안 로그에 탐지 룰을 실행하고, 룰이 비어 있는 곳을 알려 준다.

- 상태: 설계 승인 (2026-11-28), 구현 전
- 릴리스 대상: **v0.8.0** (신규 패키지 `de-vigil`, minor, experimental)
- 참고: [Sigma](https://github.com/SigmaHQ/sigma) (룰 메타데이터 구성), [MITRE ATT&CK](https://attack.mitre.org/) (기법 ID),
  [OCSF](https://schema.ocsf.io/) (필드명 대응표), [Censor 설계](2026-11-25-censor-design.md)

## 1. 목표와 범위

DE Arsenal 사용자가 보안 로그를 수집해 둔 레이크에서 "무엇이 탐지되고, 무엇이 탐지되지 않는가"를
명령 몇 개로 확인한다. 탐지 룰은 파일로 관리하고, 테스트가 없는 룰은 CI를 통과하지 못한다.

**첫 버전이 다루는 것**

| 항목 | 내용 |
|---|---|
| 로그 소스 | AWS CloudTrail (관리 이벤트, JSON) |
| 실행 방식 | 배치. Parquet 레이크를 DuckDB로 읽는다 |
| 룰 | YAML 파일(`.yaml`, `.yml`) 하나에 메타데이터, SELECT 한 개, 테스트 픽스처 |
| 결과 | alerts Parquet, 커버리지 리포트(JSON과 표) |

**하지 않는 것**

- 실시간 처리와 스트리밍. `vigil run`은 그 시점의 레이크를 한 번 읽는다.
- Sigma 룰 파일 호환. 메타데이터 구성만 참고한다.
- 같은 `--out`에 대한 동시 실행 보호. 한 출력 디렉터리에는 한 번에 한 실행만 돌린다(동시에 돌리면 같은 alert가 두 번 남을 수 있다).
- 룰 SQL의 메모리 한도와 시간 제한.
- 알림 전송. alerts는 Parquet으로 남고, 전송은 이후 Pilum 스펙으로 연결한다.
- Kubernetes 감사 로그 등 두 번째 로그 소스 (v0.8.0 범위 밖).
- LLM을 쓰는 매핑 초안 작성 (v0.8.0 범위 밖, 다음 단계 후보).
- OCSF 전체 스키마 적용. 공통 컬럼과 OCSF 필드명 대응표만 둔다.
- 수집과 변환. Pugio와 Gladius가 맡고, Vigil은 `events`를 읽기만 한다. 예외는 `vigil unpack` 하나로,
  Pugio가 읽지 못하는 CloudTrail 원본 형식(`{"Records": [...]}`, gzip)을 JSONL로 풀어 준다.

## 2. 구성 요소

```
packages/vigil/                         PyPI de-vigil, CLI `vigil`   룰 로드 · 테스트 · 실행 · 커버리지 · unpack
packages/arsenal/.../recipes/cloudtrail-detect/   수집·변환 스펙, 합성 샘플 로그, 룰 5개
schemas/vigil-rule.json                 룰 파일 계약
schemas/vigil-coverage.json             커버리지 리포트 stdout JSON 계약
docs/reference/vigil-ocsf-mapping.md    공통 컬럼 ↔ OCSF 필드명 대응표
```

- `de-vigil` 의존: `arsenal-core`, `duckdb`, `pyarrow`, `pydantic`, `pyyaml`, `typer`. `de-arsenal`에 의존하지 않는다.
  레이크 경로와 룰 디렉터리를 인자로 받으므로 de-arsenal 프로젝트가 아니어도 동작한다.
- `de-arsenal`은 `de-vigil`에 의존하지 않는다. 레시피는 스펙과 룰 파일만 담고, Vigil 설치 안내는
  레시피 README에 적는다(`arsenal` 코드는 Vigil을 알지 못한다).
- Censor 연결은 v0.8.0에 넣지 않는다. 다음 릴리스에서 Censor가 Vigil의 룰과 결과 파일을 읽어
  "탐지 준비도" 차원으로 채점한다(§9).

**데이터 흐름**

```
CloudTrail 원본 ─ vigil unpack ─▶ input/*.jsonl ─ pugio run ─▶ data/raw ─ gladius run ─▶ data/events
                                                                       │
                                   rules/*.yaml ─ vigil run ───────────┤
                                                                       ▼
                                                    data/alerts  +  vigil coverage
```

## 3. 공통 스키마 `events`

`vigil unpack`은 이벤트 하나를 `{"raw": "<이벤트 JSON 문자열>"}` 한 줄로 쓴다. 이벤트마다 중첩 구조가 달라도
수집 단계의 스키마는 문자열 컬럼 하나로 고정된다. 레시피의 Gladius 변환이 `raw`에서 값을 꺼내 아래 컬럼을
만든다. Vigil은 아래 컬럼이 있다고 가정하고, 없으면 종료 코드 2로 끝낸다.

| 컬럼 | 타입 | CloudTrail 원본 | OCSF 대응 |
|---|---|---|---|
| `event_id` | varchar | `eventID` | `metadata.uid` |
| `event_time` | timestamp (UTC) | `eventTime` | `time` |
| `source` | varchar | 상수 `cloudtrail` | `metadata.product.name` |
| `service` | varchar | `eventSource` | `api.service.name` |
| `action` | varchar | `eventName` | `api.operation` |
| `actor_type` | varchar | `userIdentity.type` | `actor.user.type` |
| `actor_id` | varchar | `userIdentity.arn` | `actor.user.uid` |
| `src_ip` | varchar | `sourceIPAddress` | `src_endpoint.ip` |
| `region` | varchar | `awsRegion` | `cloud.region` |
| `outcome` | varchar (`success`/`failure`) | `errorCode` 유무, 로그인 계열은 `responseElements`의 `ConsoleLogin`·`SwitchRole`·`CheckMfa` | `status` |
| `mfa_used` | boolean, NULL 허용 | `additionalEventData.MFAUsed`, 없으면 `userIdentity.sessionContext.attributes.mfaAuthenticated` | `actor.session.is_mfa` |
| `resource` | varchar, NULL 허용 | `requestParameters`의 대상 이름 | `resources[].name` |
| `raw` | varchar (JSON) | 이벤트 전체 | `raw_data` |

`raw`를 남기므로 공통 컬럼에 없는 값은 룰에서 `json_extract_string(raw, '$...')`로 읽는다.

## 4. 룰 파일

```yaml
id: aws-root-console-login
title: 루트 계정 콘솔 로그인
severity: high            # low | medium | high | critical
attack: [T1078.004]       # ATT&CK 기법 ID, 한 개 이상
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE action = 'ConsoleLogin' AND actor_type = 'Root' AND outcome = 'success'
tests:
  match:                  # 걸려야 하는 케이스, 한 개 이상
    - { action: ConsoleLogin, actor_type: Root, outcome: success }
  no_match:               # 걸리면 안 되는 케이스, 한 개 이상
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success }
```

- `query`는 SELECT 한 개여야 하고 결과에 `event_id`가 있어야 한다. 다른 문장은 로드 단계에서 거부한다
  (Augur의 SELECT-only 가드와 같은 규칙).
- `events` 외에는 읽지 못한다. `events`를 메모리 DuckDB 테이블로 올린 뒤 `enable_external_access=false`로
  잠근 연결에서 룰을 돌린다. 그래서 한 번에 읽는 이벤트는 그 기계의 메모리와 임시 공간에 들어가야 한다.
- 로드할 때 빈 `events`에 `query`를 돌려 본다. 없는 컬럼을 참조하거나 `event_id`를 내지 않으면 여기서 걸린다.
- 탐지는 `query` 결과의 `event_id`가 `events`의 이벤트와 이어지는 것을 말한다. `vigil test`와 `vigil run`이 같은 기준으로 센다.
- 케이스는 이벤트 하나(매핑)이거나 이벤트 여러 개(매핑의 목록)다. 케이스마다 그 이벤트만 든 `events`에
  `query`를 돌린다. `match` 케이스는 탐지가 한 건 이상, `no_match` 케이스는 0건이어야 통과한다.
- 픽스처에 적지 않은 컬럼은 NULL로 채우고, `event_id`와 `event_time`은 적지 않으면 자동으로 채운다.
- 여러 이벤트를 묶는 룰(집계, 시간 창)은 `query` 안에서 SQL로 쓴다. 결과 행마다 대표 `event_id`를 낸다.

**첫 룰 5개**

| id | 기법 | 조건 요약 |
|---|---|---|
| `aws-root-console-login` | T1078.004 | 루트 계정 콘솔 로그인 성공 |
| `aws-console-login-no-mfa` | T1078.004 | IAM 사용자 콘솔 로그인 성공, `mfa_used = false` |
| `aws-cloudtrail-logging-stopped` | T1685.002 | `StopLogging`, `DeleteTrail`, `UpdateTrail` 성공 |
| `aws-access-key-then-admin-policy` | T1098.001, T1098.003 | 같은 행위자가 `CreateAccessKey` 뒤 1시간 안에 `AdministratorAccess` 정책을 `AttachUserPolicy` |
| `aws-s3-bucket-exposure-change` | T1530 | `PutBucketAcl`, `PutBucketPolicy`, `DeleteBucketPublicAccessBlock`, `DeleteAccountPublicAccessBlock`, `PutBucketPublicAccessBlock`, `PutAccountPublicAccessBlock` 성공. 공개로 바뀌었는지는 확인하지 않고 공개 설정을 건드린 변경을 알린다 |

## 5. CLI

```bash
vigil test     [--rules DIR]                                  # 픽스처로 룰 검증
vigil run      [--rules DIR] --events GLOB --out DIR          # 룰 실행, alerts 기록
vigil coverage [--rules DIR] [--events GLOB] [--json-only]    # 커버리지 리포트
vigil unpack   SRC_DIR --out FILE.jsonl                       # CloudTrail 원본을 JSONL로
```

`--events`는 glob이거나 디렉터리다. 디렉터리면 그 아래 `*.parquet`을 전부 읽는다.

`--rules` 기본값은 `./rules`. 종료 코드: 0 정상, 1 검증 실패나 탐지 있음(아래), 2 사용법·입력 오류.

**`vigil test`**: 룰의 케이스마다 임시 `events`를 만들어 `query`를 돌린다(§4). 모든 케이스가 통과해야 룰이 통과한다. `tests`가 없거나 `match`, `no_match` 중 하나가 비어 있으면 실패. 하나라도 실패하면 종료 코드 1.

**`vigil run`**: 모든 룰을 `--events`에 실행해 `--out`에 Parquet으로 쓴다. alert 한 행의 컬럼은
`alert_id`, `rule_id`, `severity`, `attack`, `event_id`, `event_time`, `actor_id`, `detected_at`.
`alert_id`는 `rule_id`와 `event_id`의 해시이고, 이미 있는 `alert_id`는 다시 쓰지 않는다. 그래서 같은
레이크에 다시 실행해도 alerts가 늘지 않는다. 종료 코드는 새 alert 유무와 상관없이 0이고,
`--fail-on high`처럼 심각도를 주면 그 이상인 새 alert가 있을 때 1.

**`vigil coverage`**: 룰 파일만으로 기법별·로그 소스별 룰 수와 테스트 수를 낸다. `--events`를 주면
룰이 참조하는 공통 컬럼의 NULL 비율과 로그 소스별 마지막 이벤트 시각을 더한다. "룰이 없는 기법"은
패키지에 넣어 둔 기준 목록(CloudTrail로 관찰 가능한 ATT&CK 클라우드 기법)과 비교해 낸다.
목록 파일은 `packages/vigil/src/vigil/reference/cloudtrail-techniques.yaml`에 두고 출처와 작성일을 적는다.

**`vigil unpack`**: `SRC_DIR` 아래의 `*.json`, `*.json.gz`를 읽어 `Records`의 이벤트마다 한 줄씩 쓴다.
`CloudTrail-Digest` 디렉터리 아래의 다이제스트 파일은 건너뛴다. 그 밖에 `Records` 배열이 없는 파일이 있으면 그 파일을 알리고 종료 코드 2. 출력은 임시 파일에 쓰고 마지막에 옮긴다.

## 6. 레시피 `cloudtrail-detect`

`arsenal init cloudtrail-detect`가 만드는 것:

```
arsenal.yaml
collect.yaml            input/*.jsonl(`vigil unpack` 출력 형식)을 읽어 data/raw로
transform.yaml          raw에서 §3의 events로 변환
input/sample.jsonl      합성 이벤트 40건 안팎(룰 5개가 각각 한 번 이상 걸린다)
rules/*.yaml            §4의 룰 5개
README.md               실행 순서, 실제 로그를 넣는 법, PR마다 `vigil test`를 돌리는 워크플로 예시
```

샘플은 `scripts/gen_cloudtrail_sample.py`가 만들고, 만든 파일을 커밋한다. 워크플로 파일은 레시피에 넣지 않고
README에 붙여 넣을 내용으로 둔다(패키지 리소스에 점으로 시작하는 디렉터리를 싣지 않기 위해서다).

```
```

샘플 로그는 직접 만든 합성 이벤트만 넣는다. 계정 ID는 `123456789012`, IP는 문서용 대역
(`192.0.2.0/24`, `198.51.100.0/24`)을 쓴다. 공개 CloudTrail 데이터셋은 재배포하지 않고 README에
내려받아 `vigil unpack`으로 `input/`에 넣는 방법만 적는다.

## 7. 오류 처리

| 상황 | 동작 |
|---|---|
| 룰 YAML이 스키마에 맞지 않음 | 파일과 필드를 알리고 종료 코드 2. 다른 룰도 실행하지 않는다 |
| `id` 중복 | 두 파일 경로를 알리고 종료 코드 2 |
| `query`가 SELECT 한 개가 아니거나, 없는 컬럼을 참조하거나, `event_id`를 내지 않음 | 로드 단계에서 종료 코드 2 |
| `--events`에 맞는 파일이 없음 | 종료 코드 2 |
| `events`에 필수 컬럼이 없거나 타입이 다름 | 컬럼 이름(과 타입)을 알리고 종료 코드 2 |
| `--out`이 디렉터리가 아니거나 그 안의 Parquet을 alerts로 읽을 수 없음 | 룰을 실행하기 전에 경로를 알리고 종료 코드 2 |
| 실행 중 룰 하나의 SQL이 실패 | 그 룰을 실패로 기록하고 나머지를 계속 실행, 끝에 종료 코드 1 |
| alerts 쓰기 도중 실패 | 임시 파일에 쓰고 마지막에 옮기므로 기존 alerts는 그대로 남는다 |

## 8. 테스트

- 단위: 룰 로드와 스키마 검증, SELECT-only 가드, 픽스처로 `events` 만들기, `alert_id` 해시.
- 룰: 레시피에 든 룰 5개가 `vigil test`를 통과한다. `match`가 빈 룰은 실패하는지도 확인한다.
- e2e (`tests/e2e/test_recipes.py`에 추가): `arsenal init cloudtrail-detect` → `arsenal run` → `vigil test`
  → `vigil run`을 두 번 실행해 alerts 행 수가 같은지, 룰 5개가 각각 한 건 이상 탐지하는지 확인한다.
- 계약: `vigil coverage --json-only` 출력이 `schemas/vigil-coverage.json`에 맞는지 확인한다.
- `packages/vigil` 줄 커버리지 80% 이상.

## 9. Censor 연결 (다음 릴리스, v0.9.0)

Censor에 "탐지 준비도" 차원을 더한다. Censor는 읽기만 한다.

| 검사 | 근거 |
|---|---|
| 테스트가 없는 룰 | 룰 파일 경로 |
| `events`에 없는 컬럼을 참조하는 룰 | 룰 파일 경로와 컬럼 이름 |
| 룰이 있는데 이벤트가 끊긴 로그 소스 | 마지막 `event_time` |
| 기준 목록 대비 룰이 없는 기법의 비율 | 기법 ID 목록 |

## 10. 릴리스 (v0.8.0)

- 워크스페이스 패키지는 버전 하나를 같이 쓴다. 구현 계획에서는 `de-vigil`을 현재 버전(0.7.3)으로 추가하고,
  0.8.0으로 올리는 일과 PyPI 게시는 별도 릴리스 계획에서 한다.
- 신규 패키지 `de-vigil`, 상태 experimental. `docs/tools.md`, `README.md`, `README.ko.md`의 도구 표에 한 줄 추가.
- `docs/upgrade-0.8.md` 작성. 기존 사용자에게 영향이 가는 변경은 없다.
- 플러그인 스킬은 넣지 않는다. LLM 매핑 초안과 함께 다음 단계에서 다룬다.

## 11. 구현 순서 (계획 문서에서 세분화)

1. 패키지 뼈대, 룰 모델과 스키마, 로드·검증
2. `vigil test`
3. `vigil run`과 alerts 기록
4. `vigil coverage`와 기준 기법 목록
5. `vigil unpack`
6. 레시피 `cloudtrail-detect`(스펙, 합성 샘플, 룰 5개)와 e2e
7. 문서. 릴리스는 별도 계획
