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
- 케이스는 이벤트 하나이거나 이벤트 목록이다. 케이스마다 그 이벤트만 든 `events`에서 따로 실행한다.
- `events`의 컬럼은 [OCSF 대응표](../../docs/reference/vigil-ocsf-mapping.md)에 있다.

## 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 정상 |
| 1 | 룰 테스트 실패, 실행 중 룰 오류, 또는 `--fail-on` 심각도 이상의 새 alert |
| 2 | 사용법이나 입력 오류 (룰 파일 오류, 이벤트 파일 없음, 필수 컬럼 없음) |

## 한계

- 배치만 한다. `vigil run`은 `events`를 메모리 DuckDB 테이블로 올려 읽으므로, 한 번에 읽는 이벤트가
  그 기계에 들어가야 한다.
- 로그 소스는 CloudTrail 관리 이벤트 하나다.
- 커버리지의 기준 기법 목록은 직접 고른 20개이고 ATT&CK 전체가 아니다.
