# Vigil (`de-vigil`)

> 야간 순찰대. 정규화된 보안 로그에 탐지 룰을 실행하고, 룰이 비어 있는 곳을 알려 준다. (experimental)

탐지 룰을 YAML 파일로 관리한다. 룰 하나는 메타데이터, `events`를 읽는 SELECT 한 개, 그리고 걸려야 하는
케이스와 걸리면 안 되는 케이스로 이루어진다. 테스트가 없는 룰은 `vigil test`를 통과하지 못한다.

## 설치

```bash
pip install de-vigil
```

## 사용 예

```bash
arsenal init cloudtrail-detect && arsenal run      # 합성 CloudTrail 샘플 → data/events
vigil test                                          # 룰마다 픽스처로 검증
vigil run --events data/events --out data/alerts    # 새 alert만 기록 (다시 실행하면 0건)
vigil coverage --events data/events                 # 룰이 없는 ATT&CK 기법, 컬럼 NULL 비율
vigil unpack ./cloudtrail-logs --out input/ct.jsonl # CloudTrail 원본(*.json.gz)을 JSONL로
                                                    # (CloudTrail-Digest/ 아래 다이제스트 파일은 건너뜀)
```

## 룰 파일

```yaml
id: aws-root-console-login
title: 루트 계정 콘솔 로그인
severity: high            # low | medium | high | critical
attack: [T1078.004]       # ATT&CK 기법 ID
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE action = 'ConsoleLogin' AND actor_type = 'Root' AND outcome = 'success'
tests:
  match:
    - { action: ConsoleLogin, actor_type: Root, outcome: success }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success }
```

- `query`는 SELECT 한 개이고 결과에 `event_id`가 있어야 한다. `events` 말고는 읽지 못한다.
- 룰 파일은 `*.yaml`과 `*.yml`을 모두 읽는다.
- 탐지는 `event_id`가 `events`에 있는 이벤트다. `vigil test`와 `vigil run`이 같은 문장으로 센다. 그래서
  `NULL AS event_id`를 돌려주는 룰이나 `event_id`가 NULL인 픽스처는 탐지로 세지 않는다.
- 케이스는 이벤트 하나이거나 이벤트 목록이다. 케이스마다 그 이벤트만 든 `events`에서 따로 실행한다.
- `events`의 컬럼은 [OCSF 대응표](../../docs/reference/vigil-ocsf-mapping.md)에 있다.

## 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 정상 |
| 1 | 룰 테스트 실패, 실행 중 룰 오류, 또는 `--fail-on` 심각도 이상의 새 alert |
| 2 | 사용법이나 입력 오류 (룰 파일 오류, 이벤트 파일 없음, 필수 컬럼 없음이나 자료형 불일치, `--out`이 디렉터리가 아니거나 읽을 수 없는 Parquet이 있음, 쓰기 실패) |

## 한계

- 배치만 한다. `vigil run`은 `events`를 메모리 DuckDB 테이블로 올려 읽으므로, 한 번에 읽는 이벤트가
  그 기계에 들어가야 한다.
- 로그 소스는 CloudTrail 관리 이벤트 하나다.
- 커버리지의 기준 기법 목록은 직접 고른 20개이고 ATT&CK 전체가 아니다.
- 같은 `--out`에 `vigil run`을 동시에 두 개 돌리면 같은 alert가 두 번 기록될 수 있다. 출력 디렉터리마다
  한 번에 하나만 실행한다.
- 룰 SQL에는 메모리 한도와 시간 제한이 없다. 아주 큰 조인은 DuckDB가 실행한 디렉터리의 `./.tmp`에 임시
  파일을 쓸 수 있다.
- `events`의 `event_time`은 `TIMESTAMP`(나노초 `TIMESTAMP_NS`도 허용)여야 한다. 문자열이나 시간대가 있는
  타임스탬프는 종료 코드 2로 거부한다.
- `mfa_used`와 `outcome`은 레시피의 `transform.yaml`이 정한 규칙으로 만든다. 그 밖의 로그를 쓰면 같은 규칙으로
  채워야 룰이 맞게 동작한다. 규칙은 [OCSF 대응표](../../docs/reference/vigil-ocsf-mapping.md)에 있다.
