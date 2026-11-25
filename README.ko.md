# DE Arsenal

한 대의 머신에서 도는 데이터 파이프라인용 CLI 도구 모음이다. API·파일·DB에서 Parquet로
수집하고, DuckDB로 변환하고, 결과를 쿼리한다. 파이프라인은 YAML 파일이고 띄울 서버가 없다.

[English](README.md) | **한국어**

[![PyPI](https://img.shields.io/pypi/v/de-arsenal?label=de-arsenal)](https://pypi.org/project/de-arsenal/)
[![Python](https://img.shields.io/pypi/pyversions/de-arsenal)](https://pypi.org/project/de-arsenal/)
[![CI](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml/badge.svg)](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

```bash
uv tool install de-arsenal          # 또는: pip install de-arsenal
arsenal init csv-cleanup            # input/에 샘플 CSV가 든 프로젝트를 만든다
arsenal run                         # 수집 후 변환
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

이 네 줄은 적힌 그대로 실행된다. CI가 변경마다 같은 `init`과 `run`을 실행한다
(`tests/e2e/test_recipes.py`).
자기 데이터로 하려면 `input/sample.csv`를 바꾼다.

## 쓰는 방법 세 가지

| 가진 것 | 쓰는 것 | 볼 곳 |
|---|---|---|
| 주기적으로 수집하고 정리할 소스 | YAML 스펙으로 된 프로젝트를 `arsenal run`으로 실행 | 위의 네 줄, 그다음 [예제](#예제) |
| 게시된 결과를 바꾸기 전에 검토해야 하는 파일이나 테이블 스냅샷 | `arsenal dataset`. YAML이 필요 없다 | [검토한 뒤 게시하기](#검토한-뒤-게시하기) |
| 말로 하는 요청 | 에이전트 스킬. 스펙을 대신 쓰고 실행한다 | [에이전트에게 말로 시키기](#에이전트에게-말로-시키기) |

세 방법 모두 작업 폴더에 평범한 파일을 남긴다. 스펙, 출력, 진행 기록이 전부 파일이다.

## 검토한 뒤 게시하기

계속 바뀌는 CSV·JSONL·Parquet·Excel 폴더가 있을 때, `arsenal dataset`은 새 파일이 무엇을 바꾸는지
먼저 보여 주고 그다음에 교체한다. 데이터셋 하나에 작업 폴더 하나를 쓴다.

```bash
arsenal dataset preview --files 'input/*.csv' --key id    # 아무것도 쓰지 않는다. 변경 내용과 version을 출력
arsenal dataset apply   --files 'input/*.csv' --key id --expect <version>
arsenal dataset preview                                    # 다음부터는 저장된 규칙을 쓴다
arsenal status                                             # 마지막 성공, 데이터 기간, 실패 여부
```

미리보기는 파일을 마지막 게시 결과와 비교해 `added`, `removed`를 세고, `--key`가 있으면
`updated`를 전후 값과 함께 보여 준다. `apply`는 게시된 스냅샷 전체를 한 트랜잭션으로
교체한다(`.arsenal/dataset.duckdb`의 `published` 테이블). `--expect`는 내가 본 미리보기 이후에
파일이나 게시 결과가 바뀌었으면 게시를 거부한다.

| 옵션 | 효과 |
|---|---|
| `--key <열>` | 이 열로 행을 맞춰서, 수정된 행이 `updated`로 보인다. 값이 유일하고 비어 있지 않아야 한다 |
| `--distinct` | 모든 열이 같은 행을 하나만 남긴다. 없으면 반복된 행을 그대로 둔다 |
| `--time-column <열>` | 데이터가 덮는 첫 시각과 마지막 시각을 보고한다 |
| `--measure <열>` | 숫자 열의 합계와 그 변화를 보고한다 |
| `--max-age-hours <n>` | 바뀐 입력이 이 시간 넘게 게시되지 않으면 `arsenal status`가 exit 1을 낸다 |
| `--postgres-table <schema.table>`과 `--postgres-dsn-env <변수>` | `apply`가 그 Postgres 테이블의 행도 스냅샷으로 한 트랜잭션 안에서 교체한다. `de-arsenal[postgres]`가 필요하고 스키마는 미리 있어야 한다 |

- Excel(`.xlsx`·`.xlsm`·`.xlsb`·`.xls`)은 `de-arsenal[excel]`이 필요하다. 첫 시트를 CSV처럼
  문자열로 읽어 앞자리 0이 남는다. `~$` 잠금 파일은 건너뛴다.
- 폴더에서 빠진 파일은 다음 게시에서 제외된다. 원본에서 지워진 행은 `removed`로 보고된다.
- 실패하거나 거부된 게시는 마지막 성공 결과를 그대로 둔다. `arsenal status`는 다음 성공한
  게시 전까지 `failed`를 보고한다.
- `--postgres-table`을 쓰면 Postgres 테이블을 먼저 쓴다. 여기서 실패하면 게시가 실패하고
  Postgres 테이블과 로컬 게시 결과 모두 이전 스냅샷으로 남는다. 오류 메시지에 DSN은 나오지
  않는다. 테이블에 걸린 뷰와 권한은 게시해도 유지된다. 이미 있는 테이블은 스냅샷과 열·타입이
  같아야 한다. Postgres 커밋 직후 로컬 커밋이 실패하는 경우 하나만 예외이며
  [docs/08-limits.md](docs/08-limits.md)에 적었다.
- 게시된 것과 열이나 타입이 다르면 거부한다. 한 스냅샷에서 모든 행이 비어 있는 열은 타입
  변경으로 보지 않는다.
- `de-arsenal[catalog]`이 있으면 `aquila scan` 뒤에
  `aquila lineage published --direction up --format mermaid`가 입력에서 게시 결과까지의 경로를
  행 수, 데이터 기간, 최근 시도와 함께 그린다.

DB 테이블을 이렇게 게시하려면 먼저 스냅샷 파일로 내보낸다.
[Cloudflare D1 예제](examples/real-world/d1-snapshot/README.md)에 읽기 전용 내보내기 스크립트와
운영 테이블로 돌린 기록이 있다.

## 에이전트에게 말로 시키기

> 이 폴더의 엑셀 파일들을 하나로 합치고 중복을 빼 줘.

에이전트 스킬이 데이터를 살펴보고, 스펙을 쓰고, 실행한 뒤 행 수로 보고한다. 스펙은 평범한
YAML로 남는다. 사람이 직접 고칠 수 있고, `arsenal check`가 아무것도 실행하지 않고 모든 스펙을
검증한다.

```text
/plugin marketplace add KooEric/de-arsenal
/plugin install de-arsenal@de-arsenal
```

이 두 줄은 Claude Code에 스킬 7개(`start`, `ingest`, `transform`, `ask`, `trust`, `operate`,
`doctor`)를 설치한다. 같은 문서로 ChatGPT·Claude·Codex용 휴대용 스킬 하나도 만들 수 있다. 설치
방법과 실행해 본 범위는 [plugins/de-arsenal/README.md](plugins/de-arsenal/README.md)에 있다.
반복 작업은 그 작업을 돌리는 머신에 프로젝트를 둔다.

## 하는 일

- **이어서 한다.** 페이지·파일·batch 하나의 쓰기가 끝날 때마다 기록한다. Ctrl-C나
  `kill -9`로 멈춘 뒤 다시 실행하면 끝난 단위는 건너뛴다.
- **중복을 만들지 않는다.** 출력 파일명과 upsert 키가 입력 단위에서 정해지므로, 같은
  단위를 다시 처리하면 자기 자신을 덮어쓴다.
- **SQL을 보여 준다.** `gladius compile`이 변환이 실행할 DuckDB SQL을 출력한다.
- **애매한 입력은 조용히 넘어가지 않는다.** 매칭되는 파일이 없는 경로, 파라미터와 맞지
  않는 페이지네이션 모드, 파일마다 타입이 다른 컬럼은 성공으로 끝나지 않고 에러나
  경고가 된다.

sink별·도구별로 무엇을 보장하고 무엇을 보장하지 않는지는 [docs/08-limits.md](docs/08-limits.md)에
있다. 단일 노드 도구다. 실제 API와 데이터에 무엇을 돌렸고, 무엇이 깨졌고, 무엇이 아직 증명되지
않았는지는 [실데이터로 검증한 것](#실데이터로-검증한-것)에 있다. 다른 도구와의 비교는 아직 없다.

## 도구

`arsenal`은 프로젝트 전체를 실행한다. 그 아래 도구들은 각자 따로 설치해 쓸 수 있고,
서로 Parquet 파일로만 데이터를 주고받는다.

| 도구 | 하는 일 | 패키지 | 상태 |
|---|---|---|---|
| **Arsenal** | 프로젝트 CLI: `init`, `check`, `run`, `status`, `query`. 파일을 검토하고 게시하는 `dataset`. 시작용 레시피 3종 | `de-arsenal` | stable |
| **Pugio** | 수집: REST, CSV/JSONL/Excel, DB, dlt 소스 → Parquet, DuckDB, Postgres, S3/GCS, Redshift·Iceberg(둘 다 experimental). `pugio transfer`: 행을 가져오지 않고 Hive/Redshift 쿼리 → S3 → Redshift `COPY`([experimental](examples/transfer/README.md)) | `pugio` | stable |
| **Gladius** | YAML 변환을 DuckDB SQL로 컴파일. 증분 실행, Python UDF, 즉석 쿼리 | `de-gladius` | stable |
| **Onager** | 로컬 Parquet의 small-file compaction과 격리 백필 | `de-onager` | beta |
| **Scutum** | 라이브러리: Arrow 데이터 계약과 파일 잠금 (Pugio가 사용) | `de-scutum` | beta |
| **Scorpio** | 라이브러리: freshness 판정과 스트림 상태 (`status` 명령들이 사용) | `de-scorpio` | beta |
| **Spatha** | 라이브러리: 의존성·신호·우선순위·실행 window로 지금 실행할 태스크를 고른다. 스케줄러 데몬은 없다 | `spatha` | beta |
| **Hasta** | 스트리밍: cursor API 폴링 마이크로배치, Postgres outbox CDC | `de-hasta` | beta |
| **Ballista** | Gladius 변환을 Spark/Kubernetes에서 실행하고 Iceberg에 커밋 | `de-ballista` | beta |
| **Aquila** | SQLite 파일 하나로 된 카탈로그: 데이터셋, 스키마 이력, lineage(`arsenal dataset` 게시 포함), 계약, 태그 | `de-aquila` | preview |
| **Augur** | 자연어 질문을 SQL로. eval 하네스 포함 | `de-augur` | experimental |
| **Pilum** | Reverse ETL: 바뀐 레코드를 HTTP 끝점과 Slack으로, 변경당 한 번 보낸다 | `de-pilum` | experimental |
| **Censor** | 감사: 프로젝트를 파일만 읽고 채점한다(스펙, 검증 규칙, 평문 비밀값, 스케줄, 에이전트 설정). `--tier standard`는 실행 상태와 데이터도 읽는다(실패 unit, 신선도, 중복 키, NULL). 파일·DuckDB·Postgres 데이터셋만 따로 평가할 수도 있다. 읽기만 하고, 고칠 곳을 근거와 함께 알려 준다. `arsenal eval`로도 실행 | `de-censor` | experimental |

**stable**: 같은 minor 안에서는 YAML 필드가 추가만 되고, 상태 파일은 앞으로 마이그레이션된다.
**beta**: 동작하고 테스트되지만 이 저장소 밖 사용 사례가 적다.
**preview / experimental**: 형식과 명령이 어느 릴리스에서든 바뀔 수 있다.
1.0 이전이며 장기 API 약속은 없다. [CHANGELOG](CHANGELOG.md) 참고.

## 설치

| 하고 싶은 것 | 설치 |
|---|---|
| 수집·변환·쿼리·레시피 | `uv tool install de-arsenal` |
| …+ Excel 입력 (`.xlsx`·`.xlsm`·`.xlsb`·`.xls`) | `uv tool install "de-arsenal[excel]"` |
| …+ Postgres sink, 또는 `arsenal dataset`의 Postgres 게시 | `uv tool install "de-arsenal[postgres]"` |
| …+ Redshift sink | `uv tool install "de-arsenal[redshift]"` |
| …+ `pugio transfer`의 Hive 소스 | `uv tool install "de-arsenal[redshift,hive]"` |
| …+ Iceberg sink (Trino에서 조회) | `uv tool install "de-arsenal[iceberg]"` |
| …+ 스트리밍·CDC (Hasta) | `uv tool install "de-arsenal[streaming]"` |
| …+ Spark on Kubernetes 실행 (Ballista) | `uv tool install "de-arsenal[distributed]"` |
| …+ 카탈로그 (Aquila) | `uv tool install "de-arsenal[catalog]"` |
| …+ reverse ETL (Pilum) | `uv tool install "de-arsenal[dispatch]"` |
| …+ `arsenal eval` 프로젝트 감사 (Censor) | `uv tool install de-arsenal --with de-censor` |
| …+ dbt·dlt 프로젝트 | `"de-arsenal[dbt]"` · `"de-arsenal[dlt]"` |
| 도구 하나만 | `pip install pugio` · `de-gladius` · `de-hasta` · `de-aquila` · `de-pilum` · `de-censor` · … |

기본 설치는 Spark·Kubernetes·Iceberg·Postgres 드라이버를 끌어오지 않는다
(`packages/arsenal/tests/test_base_install.py`가 확인한다).
`de-arsenal`을 설치하면 `pugio`와 `gladius`도 PATH에 올라온다(0.6.15+). `pugio status`와
`arsenal collect status`가 둘 다 된다. 0.6.14 이하의 `uv tool install de-arsenal`은 `arsenal`만 올렸으니
그 버전에서는 `arsenal collect …`, `arsenal transform …`을 쓴다.
소스에서 쓰려면 clone 후 `uv sync`, 명령 앞에 `uv run`을 붙인다.

## 예제

아래 블록은 모두 이 저장소를 clone한 상태에서 실행된다.

**오프라인으로 수집·변환** ([examples/quickstart](examples/quickstart)):

```bash
cd examples/quickstart
pugio run collect.yaml                 # orders.csv → ./data/orders
gladius compile transform.yaml         # SQL 출력
gladius run transform.yaml             # → ./data/orders_clean
gladius query "SELECT * FROM './data/orders_clean/*.parquet'"
```

**REST API에서 수집** (GitHub 토큰 필요):

```bash
export GITHUB_TOKEN=ghp_...
pugio run examples/github-issues.yaml
pugio status examples/github-issues.yaml --cost --max-age-seconds 86400
```

**cursor API에서 스트리밍** (`[streaming]`):

```bash
python examples/hasta/fake_orders_api.py &
hasta run examples/hasta/orders-stream.yaml      # Ctrl-C는 진행 중 batch를 커밋하고 멈춘다
hasta checkpoint show examples/hasta/orders-stream.yaml
```

**바뀐 레코드를 끝점으로 보내기** (`[dispatch]`):

```bash
python examples/pilum/fake_alerts_api.py &
export ALERTS_URL=http://127.0.0.1:8766/alerts
pilum plan examples/pilum/alerts.yaml            # 무엇이 나갈지만 보여 준다. 보내지 않는다
pilum run examples/pilum/alerts.yaml             # 2건 전송
pilum run examples/pilum/alerts.yaml             # 0건: 바뀐 것이 없다
```

**프로젝트 카탈로그** (`[catalog]`). 한 번 이상 실행한 프로젝트 디렉터리에서:

```bash
aquila scan
aquila search orders
```

Ballista는 Kubernetes 클러스터, 객체 스토리지, Iceberg 카탈로그가 있어야 한다. 준비 방법은
[packages/ballista/README.md](packages/ballista/README.md)에 있다.

주기적으로 실행하려면(cron, GitHub Actions, 컨테이너) [examples/deploy](examples/deploy)에서
시작한다.

`arsenal init` 레시피: `csv-cleanup`(CSV 폴더 → dedup·cast), `github-issues`(REST API →
정리된 테이블), `api-to-postgres`(REST API → 검증 → Postgres upsert, `[postgres]` 필요).

## 실데이터로 검증한 것

테스트가 전부 통과하는 것으로는 부족했다. 실데이터에서만 드러난 버그 때문에 릴리스가 세 번
필요했다. 이 절은 실제 API와 파일에 무엇을 돌렸고, 무엇이 깨졌고, 무엇이 아직 증명되지 않았는지의
기록이다. 실험 환경은 Linux 4 vCPU, 15 GB RAM이다. 모든 숫자는 실제 실행 결과이고, 재현용 스펙은
[experiments/real-data/](experiments/real-data/README.md), 전체 보고서는
[docs/11-real-data-experiments.md](docs/11-real-data-experiments.md)에 있다.

| | |
|---|---|
| 크래시를 주입한 실행 중 중복·누락으로 끝난 경우 | 4개 실험(1, 2, 4, 5) 중 **0** |
| 실데이터로 찾은 조용한 데이터 손실 버그 (모두 수정) | **5건** (0.5.3에서 2건, 0.6.0에서 3건) |
| 가장 큰 실제 적재 | 955만 행 4.0초, 1,970만 행 변환 5.96초 |
| 운영 DB 테이블을 검토·게시 경로로 | 3,365행. 실제 두 상태 사이에 추가 793, 수정 2,559, 삭제 0 |
| 자동 테스트 | 869개. CI가 Linux·macOS·Windows, Python 3.11·3.12에서 실행. 매주와 PR마다 최신 의존성 릴리스(DuckDB 2.x 포함)로도 실행 |

### 실험

**1. GitHub API 수집 중 강제 종료.** 커밋 169건을 페이지당 5건으로 받다가 `kill -9`.

```text
$ pugio status gh-commits.yaml        # kill -9 직후
done: 8
running: 1
$ pugio run gh-commits.yaml           # 즉시 재실행
error: pipeline 'gh-commits' is busy (lease held by pugio-6b4b… until 02:17:16)
$ pugio run gh-commits.yaml           # lease 만료 후
done: fetched=26 written=26 skipped=8 quarantined=0
$ pugio run gh-commits.yaml           # 완료 후 재실행
done: fetched=1 written=0 skipped=34 quarantined=0      # 0.70s
```

죽은 러너의 lease가 살아 있는 동안 이중 실행이 차단됐고, 재개 시 완료된 8페이지는 건너뛰었다.
최종 169행 = 고유 커밋 id 169개.

**2. GitLab API, 이슈 9,554건, `Link` 헤더 페이지네이션.** 8초 만에 `kill -9`.

```text
$ pugio status gl-issues.yaml         # kill -9 직후
done: 6
running: 1
$ pugio run gl-issues.yaml            # 재개
done: fetched=90 written=90 skipped=0 quarantined=0
$ count(*), count(DISTINCT id)  /  API 헤더 x-total
9554, 9554  /  9554
$ pugio run gl-issues.yaml            # 완료 후 재실행
done: fetched=0 written=0 skipped=0 quarantined=0       # 0.44s
```

Link 모드는 다음 페이지 URL을 미리 알 수 없어서 마지막 커서를 상태에 저장한다. 재개는 저장된
커서에서 이어 갔고 완료된 6페이지는 다시 요청하지 않았다. 96페이지, 39 MB, 초당 5요청 제한.

**3. 지저분한 공개 CSV와 검증 게이트.** 전 세계 공항 86,343행 × 13열. ICAO 코드 87.7%가 비어
있고, 이름은 여러 문자로 적혀 있고, 좌표는 `"lat, lon"` 문자열이다. 대륙별 7개 파일로 나누고 EU
파일 3행의 고도를 `999999`로 오염시켰다.

| 단계 | 결과 |
|---|---|
| 1차 실행 | EU 파일만 격리, 사유 `max:elevation_ft=3`. 나머지 6개 파일 73,701행 적재, 0.50초 |
| 원천 수정 후 | 정상 6개는 건너뛰고 EU만 재처리. 86,343행으로 원본과 같고 `ident` 중복 없음 |
| 변환 | filter, derive(좌표 파싱, ft→m), dedup, select가 CTE 쿼리 하나로 컴파일. 0.29초, 공항 4,572개 |
| 확인 쿼리 | 최고 고도 공항 Daocheng Yading (DCY) 4,411 m. 실제와 일치 |

**4. 스트림을 6번 강제 종료.** 로컬 cursor API(초기 2만 건, 이후 초당 250건)를 받으면서
0.8~2.5초 간격으로 `kill -9` 6회, 그 뒤 정상 종료.

```text
$ hasta status orders.yaml            # 크래시 직후
"status": "stalled", "reasons": ["a runner holds an expired lease (it likely died); it will be taken over"], "fence": 6
$ count(*), count(DISTINCT id), min(id), max(id)
rows=24000 distinct=24000 min=1 max=24000 gaps=0 dups=0
```

**5. NYC 택시 1,970만 행, 세 세대의 스키마** (2019, 2022, 2024).

| 항목 | 결과 |
|---|---|
| 실행 간 스키마 변화 (2022-01 다음 2024-01) | `schema_drift: block`이 차단. 이름이 바뀐 컬럼 1개와 타입이 바뀐 컬럼 6개를 짚었다 |
| 크래시를 넣은 대량 적재 (2024 Q1, 955만 행) | 4.0초, 초당 약 240만 행. 10개 단위 중 5개 완료 시점에 종료: 반쯤 쓰인 파일 없음. 재개 후 행 수(9,554,778)와 `total_amount` 합계(256,692,913.53)가 원본과 일치 |
| 5개 파일 전체 변환 | 1,970만 행 → 1,920만 행, 5.96초. `airport_fee`와 `Airport_fee`가 합쳐졌다 |

**6. 다른 하드웨어에서 벤치마크 재현** (합성 데이터, 웜 캐시, 네트워크 제외. 운영 예상치가 아니라
엔진의 상한이다).

| 워크로드 | M3 Pro 12코어 | Linux 4 vCPU |
|---|---:|---:|
| 1 GB, 1,300만 행 Parquet 변환 | 0.105 s | 0.376 s |
| 스트리밍 100만 이벤트 | 2.99 s | 3.26 s |
| 완료된 스트림 재실행 | 0.9 ms | 0.8 ms |

**7. 자연어 → SQL (Augur).** 같은 평가 34건을 두 모델로 돌렸다. 점수가 높은 것은 모델이
완벽하다는 뜻이 아니라 평가 세트(작은 테이블 3개)가 너무 쉽다는 뜻으로 읽는다.

| 모델 | 정답 | 실패 |
|---|---:|---|
| `gemini-3.1-flash-lite` | 34 / 34 | 없음 |
| `claude-sonnet-5-5` | 30 / 34 | WRONG_SHAPE 2, WRONG_RESULT 1, UNANSWERABLE 1 |

Sonnet의 실패 4건은 SQL이 틀렸다기보다 골든 SQL과 해석이 달랐다. 컬럼을 더 돌려준 것이 2건,
"카테고리별 매출"에 `status = 'paid'` 필터를 붙인 것이 1건, "COMPLETED 주문"을 `paid`로
매핑하지 않고 답 불가로 선언한 것이 1건이다(이 케이스가 잡으려는 함정). 실행 전체에 입력
14,655 · 출력 2,545 토큰, repair 0회.

**8. 운영 Cloudflare D1 테이블을 `arsenal dataset`으로** (PyPI의 0.6.7). 3,365행 × 20열의
카탈로그 테이블을 키 기준 스냅샷으로 내보냈다. 테이블의 실제 두 상태를 차례로 게시했고, DB에는
아무것도 쓰지 않았다.

| 단계 | 결과 |
|---|---|
| 과거 상태 게시 후 현재 상태 미리보기 | 2,572행 게시. 그다음 추가 793, 삭제 0, 수정 2,559 |
| 수정의 내용 | 비활성화(`active` 1 → 0) 384건, 이름 변경 43건, 시각만 바뀐 것 86건 |
| 변경 없이 다시 내보내기 | 파일이 바이트 단위로 같고 변경 0건 |
| 로컬 파일에서 2행 삭제, 2행 수정 | `removed` 2, `updated` 2. 키와 바뀐 필드를 짚었다 |
| JSONL 스냅샷에서 한 열을 전부 NULL로 | 0.6.7은 스키마 변경으로 거부했다. 0.6.8에서 수정: 수정 2,458건으로 보고 |

실제 데이터에는 지워진 행이 없어서 `removed`는 로컬 파일로만 확인했다. 상세와 내보내기
스크립트는 [examples/real-world/d1-snapshot](examples/real-world/d1-snapshot/README.md)에 있다.

### 실데이터만 찾아낸 버그

아래는 전부 exit code 0과 성공 메시지로 끝났다.

| 발견 | 무슨 일이 있었나 | 증거 | 수정 |
|---|---|---|---|
| `github-issues` 레시피가 한 페이지만 수집 | `mode: offset`이 페이지 번호 파라미터에 행 offset을 보내 page 0 다음에 page 5로 건너뛰었다. e2e 테스트가 `page=0`을 mock해 버그를 정답으로 고정하고 있었다 | 이슈 21건 중 6건만 수집 | 0.5.3: 21건 중 21건. 그런 스펙은 이제 검증 에러 |
| CSV의 `NA`가 파일 분할 방식에 따라 NULL | pyarrow는 `NA`를 null 토큰으로 본다. 컬럼 값이 전부 `NA`인 파일은 타입이 `null`로 추론됐다 | 북미 39,842행(46%)의 `continent` 손실 | 0.5.3: 빈 셀만 null |
| README 첫 네 줄이 실패 | `csv-cleanup` 레시피에 입력이 없었고, 수집은 0행으로 성공을 보고했고, 변환은 DuckDB 원문 에러로 죽었다 | 새 설치에서 exit 1 | 0.6.0: 샘플 데이터 포함, 빈 입력 경로는 에러 |
| CSV 컬럼의 앞자리 0이 사라짐 | 우편번호 컬럼이 정수로 추론됐다 | `01234`가 `1234`로 저장 | 0.6.0: 그런 컬럼은 문자열로 유지. `column_types`로 타입 고정 |
| 평범한 CSV가 Postgres에 들어가지 않음 | sink가 날짜, 시간대 없는 timestamp, 작은 정수, 전부 빈 컬럼을 거부했다 | 실제 Postgres 16에서 `unsupported arrow type` | 0.6.0: 네 가지 모두 적재 |

조용하지 않고 에러로 드러난 것이 하나 더 있다. `arsenal dataset`이 한 열이 전부 비어 있는 JSONL
스냅샷을 거부했고(실험 8), 0.6.8에서 고쳤다.

확인한 한계 두 가지도 0.6.0에서 다뤘다.

- **검증이 단위 전체를 격리했다.** NYC 데이터는 위반 행 1.2%가 모든 덩어리에 섞여 있어, 10개 단위가
  전부 격리되고 955만 행 중 0행이 적재됐다. `on_violation: split`은 통과한 행을 적재하고 위반 행만
  따로 남긴다.
- **한 실행 안의 파일 간 타입 차이를 보지 못했다.** 이제 Pugio가 파일명과 함께 경고하고,
  `schema_drift: block`이면 실패한다.

### 이 실험들이 보여 주는 것과 보여 주지 않는 것

- **보여 준 것:** 시도한 모든 시점의 크래시 뒤에, 재실행 결과가 원본과 같은 행이었고 중복이 없었다.
  페이지 번호형·link형 REST 페이지네이션, 대량 적재, 스트림에서 확인했다. 완료된 실행의
  재실행은 1초 미만의 no-op이다.
- **보여 준 것:** 커버리지 89%는 스펙과 실제 API·파일의 의미 사이에 있는 버그를 잡지 못했다. 그래서
  지금 테스트는 사용자가 받는 레시피와 README의 첫 명령을 그대로 실행한다.
- **보여 주지 않은 것:** 실제 네트워크 소스에서의 처리량. 가장 큰 API 실행이 9,554건이다. 정합성의
  증거이지 속도의 증거가 아니다.
- **보여 주지 않은 것:** 시간에 따른 동작. 위 실험은 모두 단발 실행이다. 몇 주 동안 스케줄로 돌린
  적은 없다.

### sink별 검증 수준

| sink | 무엇으로 검증했나 | 검증하지 못한 것 |
|---|---|---|
| Parquet (로컬) | 위 모든 실험의 실제 파일 | |
| DuckDB | 테스트의 실제 DuckDB 파일 | |
| Postgres | 실제 Postgres 16: 멱등, upsert, 타입 왕복. `arsenal dataset` 게시: 행 교체 시 뷰·권한 유지, 게시 실패 시 양쪽 모두 그대로 | 관리형 서비스 (RDS, Aurora) |
| Redshift (experimental) | `insert` 경로는 같은 SQL을 받는 Postgres에서 실행, `copy` 경로는 발행하는 SQL문 | **실제 Redshift 클러스터** |
| Iceberg (experimental) | SQLite 카탈로그와 로컬 저장소의 실제 Iceberg 테이블 | REST·Glue 카탈로그, S3, **Trino에서의 조회** |
| Trino용 `pugio ddl` | 출력되는 문장 | **Trino에서의 실행** |
| Pilum (experimental) | 실제 HTTP로 붙는 로컬 끝점. 응답과 원장 기록 사이의 크래시는 테스트에서 모사 | 실제 외부 API, Slack |
| Ballista | CI의 kind 클러스터, 3행: 정합성만 | 실제 클러스터, 성능 기준선 |

## 문서

| | |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | 명령 4개로 한 바퀴, 세 CLI의 명령, 운영 체크리스트 |
| [docs/tools.md](docs/tools.md) | 도구별 사용 시점과 짧은 시작 방법 |
| [plugins/de-arsenal/README.md](plugins/de-arsenal/README.md) | 에이전트 스킬: 설치, 스킬별 역할, 실행해 본 범위 |
| [docs/reference/](docs/reference/) | 코드에서 생성한 YAML 필드 전체 |
| [docs/08-limits.md](docs/08-limits.md) | 보장하는 것과 보장하지 않는 것 |
| [docs/11-real-data-experiments.md](docs/11-real-data-experiments.md) · [experiments/](experiments/real-data/README.md) | 실제 API·데이터(GitHub, GitLab, 공항 CSV, NYC 택시) 실험 결과, 찾은 버그와 확인한 한계 |
| [examples/real-world/](examples/real-world/) | GitHub, Notion, Slack, Stripe, 공공데이터포털 수집 스펙과 Cloudflare D1 스냅샷 내보내기 |
| [examples/deploy/](examples/deploy/README.md) | cron, GitHub Actions, 컨테이너로 주기 실행 |
| [CHANGELOG.md](CHANGELOG.md) · [0.7 업그레이드](docs/upgrade-0.7.md) | 변경 내역과 업그레이드 방법 |
| [docs/README.md](docs/README.md) | 설계 문서, 결정 기록, 릴리스 절차 |

레퍼런스와 CHANGELOG는 영어, 가이드와 설계 문서는 한국어다.

## 라이선스

[Apache-2.0](LICENSE)
