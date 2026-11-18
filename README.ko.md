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
있다. 단일 노드 도구다. 측정된 수치는 노트북에서 1 GB Parquet를 웜 캐시로 변환한 DuckDB
벤치마크 하나뿐이고([benchmarks/RESULTS.md](benchmarks/RESULTS.md)), 수집 벤치마크와 다른
도구와의 비교는 아직 없다.

## 도구

`arsenal`은 프로젝트 전체를 실행한다. 그 아래 도구들은 각자 따로 설치해 쓸 수 있고,
서로 Parquet 파일로만 데이터를 주고받는다.

| 도구 | 하는 일 | 패키지 | 상태 |
|---|---|---|---|
| **Arsenal** | 프로젝트 CLI: `init`, `run`, `status`, `query`, 시작용 레시피 3종 | `de-arsenal` | stable |
| **Pugio** | 수집: REST, CSV/JSONL/Excel, DB, dlt 소스 → Parquet, DuckDB, Postgres, S3/GCS | `pugio` | stable |
| **Gladius** | YAML 변환을 DuckDB SQL로 컴파일. 증분 실행, Python UDF, 즉석 쿼리 | `de-gladius` | stable |
| **Onager** | 로컬 Parquet의 small-file compaction과 격리 백필 | `de-onager` | beta |
| **Scutum** | 라이브러리: Arrow 데이터 계약과 파일 잠금 (Pugio가 사용) | `de-scutum` | beta |
| **Scorpio** | 라이브러리: freshness 판정과 스트림 상태 (`status` 명령들이 사용) | `de-scorpio` | beta |
| **Spatha** | 라이브러리: 의존성·신호·우선순위·실행 window로 지금 실행할 태스크를 고른다. 스케줄러 데몬은 없다 | `spatha` | beta |
| **Hasta** | 스트리밍: cursor API 폴링 마이크로배치, Postgres outbox CDC | `de-hasta` | beta |
| **Ballista** | Gladius 변환을 Spark/Kubernetes에서 실행하고 Iceberg에 커밋 | `de-ballista` | beta |
| **Aquila** | SQLite 파일 하나로 된 카탈로그: 데이터셋, 스키마 이력, lineage, 계약, 태그 | `de-aquila` | preview |
| **Augur** | 자연어 질문을 SQL로. eval 하네스 포함 | `de-augur` | experimental |
| **Pilum** | Reverse ETL: 바뀐 레코드를 HTTP 끝점과 Slack으로, 변경당 한 번 보낸다 | `de-pilum` | experimental |

**stable**: 같은 minor 안에서는 YAML 필드가 추가만 되고, 상태 파일은 앞으로 마이그레이션된다.
**beta**: 동작하고 테스트되지만 이 저장소 밖 사용 사례가 적다.
**preview / experimental**: 형식과 명령이 어느 릴리스에서든 바뀔 수 있다.
1.0 이전이며 장기 API 약속은 없다. [CHANGELOG](CHANGELOG.md) 참고.

## 설치

| 하고 싶은 것 | 설치 |
|---|---|
| 수집·변환·쿼리·레시피 | `uv tool install de-arsenal` |
| …+ Postgres sink | `uv tool install "de-arsenal[postgres]"` |
| …+ 스트리밍·CDC (Hasta) | `uv tool install "de-arsenal[streaming]"` |
| …+ Spark on Kubernetes 실행 (Ballista) | `uv tool install "de-arsenal[distributed]"` |
| …+ 카탈로그 (Aquila) | `uv tool install "de-arsenal[catalog]"` |
| …+ reverse ETL (Pilum) | `uv tool install "de-arsenal[dispatch]"` |
| …+ dbt·dlt 프로젝트 | `"de-arsenal[dbt]"` · `"de-arsenal[dlt]"` |
| 도구 하나만 | `pip install pugio` · `de-gladius` · `de-hasta` · `de-aquila` · `de-pilum` · … |

기본 설치는 Spark·Kubernetes·Iceberg·Postgres 드라이버를 끌어오지 않는다
(`packages/arsenal/tests/test_base_install.py`가 확인한다).
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

`arsenal init` 레시피: `csv-cleanup`(CSV 폴더 → dedup·cast), `github-issues`(REST API →
정리된 테이블), `api-to-postgres`(REST API → 검증 → Postgres upsert, `[postgres]` 필요).

## 문서

| | |
|---|---|
| [docs/tools.md](docs/tools.md) | 도구별 사용 시점과 짧은 시작 방법 |
| [docs/reference/](docs/reference/) | 코드에서 생성한 YAML 필드 전체 |
| [docs/08-limits.md](docs/08-limits.md) | 보장하는 것과 보장하지 않는 것 |
| [CHANGELOG.md](CHANGELOG.md) · [0.6 업그레이드](docs/upgrade-0.6.md) | 변경 내역과 업그레이드 방법 |
| [docs/README.md](docs/README.md) | 설계 문서, 결정 기록, 릴리스 절차 |

레퍼런스와 CHANGELOG는 영어, 가이드와 설계 문서는 한국어다.

## 라이선스

[Apache-2.0](LICENSE)
