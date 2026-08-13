# v0.2.0 도그푸딩 runbook

목표는 실제 반복 업무 하나를 최소 2주 동안 DE Arsenal로 운영하면서, 제품 약속인
재개·멱등·운영 단순화가 실제로 유효한지 확인하는 것이다. 데모 데이터나 일회성
성공은 이 기준을 대체하지 않는다.

## 권장 첫 업무

다음 조건을 만족하는 업무를 하나 고른다.

- 하루 또는 주 단위로 반복된다.
- 공개 API 또는 내부에서 허가받은 파일/DB를 원본으로 쓴다.
- 결과가 Parquet 또는 DuckDB/DuckDB SQL로 바로 소비된다.
- 실패했을 때 재실행해도 문제가 없는 업무여야 한다.
- 계정 비밀값은 YAML이 아니라 환경 변수로 제공할 수 있어야 한다.

가장 권장하는 시작은 공개 GitHub Issues API를 매일 수집해 Parquet로 저장하고,
Gladius로 정리한 뒤 freshness를 확인하는 흐름이다. 인증 없이도 시작할 수 있고,
페이지네이션·재실행·변환을 모두 경험할 수 있다.

## 시작 절차

```bash
uv tool install de-arsenal
mkdir de-arsenal-dogfood && cd de-arsenal-dogfood
arsenal init github-issues
arsenal run
arsenal query "SELECT count(*) FROM './data/**/*.parquet'"
```

반복 실행은 운영 환경에 맞는 scheduler(cron, GitHub Actions, 기존 scheduler)에서
`arsenal run` 하나로 시작한다. scheduler를 새로 만드는 것은 이 도그푸딩의 범위가
아니다.

## 매 실행 확인

```bash
arsenal run
arsenal collect status collect.yaml --cost --max-age-seconds 86400
```

`collect`는 Arsenal 안에 노출된 Pugio 서브앱이다. Pugio를 직접 설치한 환경에서는
같은 명령을 `pugio status collect.yaml --cost --max-age-seconds 86400`로 실행한다.

다음을 운영 기록에 남긴다.

| 항목 | 기록할 내용 |
|---|---|
| 실행 시각·결과 | 성공/실패와 소요 시간 |
| 수집량 | unit 수, row 수, byte 수 |
| 재실행 결과 | no-op 또는 재개한 unit 수 |
| 데이터 품질 | DLQ 발생, schema drift, freshness alert 여부 |
| 사용자 마찰 | 이해하기 어려운 명령·문서·오류 메시지 |

## 반드시 한 번 검증할 실패 경로

안전한 개발/테스트 데이터에서만 다음을 실행한다.

1. 수집 도중 실행을 중지한다.
2. 같은 `arsenal run`을 다시 실행한다.
3. 완료 데이터가 중복되지 않고 남은 작업만 진행됐는지 확인한다.

실제 운영 데이터에서 의도적으로 실패를 만들 수 없다면, 별도 복제본이나 공개 API
대상으로 검증한다.

## 2주 종료 회고

아래 형식으로 GitHub issue 또는 Discussion에 기록한다.

```markdown
## Dogfood: <업무 이름>

- 기간: YYYY-MM-DD ~ YYYY-MM-DD
- 원본/주기: <API·파일·DB> / <daily·weekly>
- 실행: <scheduler와 명령>
- 결과 소비자: <대시보드·분석·파일>

### 효과
- 수동 작업에서 줄어든 단계:
- 재실행 또는 장애 경험:
- 비용/속도 관찰:

### 마찰
- 설치·설정:
- YAML/CLI:
- 오류 메시지·문서:
- 누락 기능:

### 다음 조치
- [ ] 버그
- [ ] 문서 개선
- [ ] UX 개선
- [ ] 기능 제안
```

제품 변경은 이 회고에서 반복되는 마찰을 우선한다. 단발성 요청은 문서 보완으로
충분한지 먼저 판단하고, 재현 가능한 운영 문제만 기능 로드맵에 반영한다.
