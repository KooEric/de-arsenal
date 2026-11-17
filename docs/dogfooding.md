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

## 제품군 도그푸딩 런너

`scripts/dogfood.py`는 DE Arsenal을 제품별로 따로 호출하면서도 하나의 데이터셋을
끝까지 흘려보내는 수동 검증용 런너다. 단위 테스트만 통과하는지 보는 것이 아니라,
수집 → 상태 저장 → 품질 계약 → freshness → small-file 정리 → 의존성 실행 → 변환 →
쿼리까지 실제 사용 순서로 확인한다.

### 오프라인 더미 API

계정·Docker·네트워크 없이 실행한다.

```bash
./.venv/bin/python scripts/dogfood.py --mode dummy --workdir .dogfood
```

더미 API는 다음을 일부러 발생시킨다.

- cursor pagination 3페이지
- 두 번째 페이지의 일시적 `503` → Pugio retry
- 첫 페이지 완료 직후의 결정적 crash → 같은 상태 DB로 resume
- 완료 후 재실행 → 추가 HTTP 요청과 추가 파일이 없는 no-op
- 중복 주문 1건 → Gladius `dedup`
- 3개의 작은 Parquet 파일 → Onager dry-run 후 실제 compaction

산출물은 `.dogfood/project` 아래에 남는다. 마지막에 출력되는 `arsenal query`,
`pugio status`, `gladius compile` 명령을 그대로 다시 실행해 각 단계를 들여다볼 수
있다.

### 실시간 API

외부 시스템에 쓰기를 하지 않는 GitHub Issues `GET`만 사용한다. 공유 IP의 익명
rate limit을 소모하지 않도록 `GITHUB_TOKEN`을 요구하며, 최근 7일의 open 이슈만
읽는다.

```bash
GITHUB_TOKEN=ghp_... \
  ./.venv/bin/python scripts/dogfood.py \
  --mode live \
  --repo octocat/Hello-World \
  --workdir .dogfood-live
```

토큰은 GitHub read-only 범위면 충분하다. 실제 운영 API를 연결할 때도 읽기 전용
토큰과 작은 대상(기간·페이지 크기·저장 경로)을 먼저 사용한다. 실시간 모드는
네트워크와 API 응답 스키마에 의존하므로 CI의 기본 테스트로 사용하지 않는다.

### 제품별 확인 지점

| 제품 | 런너에서 확인하는 것 |
|---|---|
| `arsenal-core` | SQLite 상태, metrics, lineage, 결정적 재개 상태 |
| `Pugio` | REST 수집, retry, cursor resume, Parquet sink, status/cost |
| `Scutum` | 정상 Arrow batch와 schema 위반 batch의 계약 판정 |
| `Scorpio` | 마지막 완료 시각 기반 freshness |
| `Onager` | compaction 계획 확인 후 안전한 디렉터리 교체 |
| `Spatha` | `collect → compact → transform` 의존성 순서 |
| `Gladius` | 선언을 SQL로 컴파일, 변환, Parquet 쿼리 |
| `Arsenal` | recipe scaffold, umbrella run, umbrella query |

기본 명령은 변경하지 않는 범위의 검증이다. 실시간 API 모드도 GitHub에 쓰지 않으며,
생성되는 파일은 지정한 `--workdir` 아래에만 둔다.
