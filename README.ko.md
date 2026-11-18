# DE Arsenal

> **노트북 한 대가 데이터 플랫폼이 된다.** 설치 하나, 명령 하나로 수집 → 검증 → 변환 → 쿼리.

[English](README.md) | **한국어**

[![PyPI](https://img.shields.io/pypi/v/de-arsenal?label=de-arsenal)](https://pypi.org/project/de-arsenal/)
[![Python](https://img.shields.io/pypi/pyversions/de-arsenal)](https://pypi.org/project/de-arsenal/)
[![CI](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml/badge.svg)](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

새벽 3시에 죽은 수집 작업, 돌던 중에 만료된 토큰, 아무도 쿼리하지 못하는 CSV 폴더 —
DE Arsenal은 이런 문제를 선언형 명령 하나로 처리한다. 서버도, 클러스터도, DAG 코드도 없다.

```bash
uv tool install de-arsenal          # 또는: pip install de-arsenal
arsenal init csv-cleanup            # 레시피로 시작
arsenal run                         # 수집 → 검증 → 변환. 끊겨도 다시 실행하면 이어서
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

Databricks·Snowflake 수준의 **완성도**를, 정반대의 **형태**로. 거대 플랫폼이 백 가지를 80점으로 할 때
우리는 한 가지를 100점으로 한다. 멱등·재개·검증이 기본값이고, 마진 없는 비용 구조는 아키텍처에서
나온다([근거](docs/07-cost-efficiency.md)).

## 현황 — v0.5.0

| | |
|---|---|
| **안정** | 수집·변환·우산 CLI와 P1 확장 전부 — dlt/dbt 인터롭, schema drift 정책, 클라우드 sink, Python UDF, 관측성, 오케스트레이션, 백필, 데이터 계약 |
| **Beta** (0.4) | **Hasta** — 폴링 마이크로배치 스트리밍·Postgres outbox CDC · **Ballista** — portable 변환을 Spark/Kubernetes에서 실행, Iceberg 출력 |
| **Preview** (0.5) | **Aquila** — 파일 하나로 된 카탈로그: 데이터셋·스키마 이력·도구 간 lineage·계약·태그 |
| **실험** | **Augur** — 자연어 질문을 SQL로, eval 하네스 포함 |
| **업그레이드** | [0.4 → 0.5](docs/upgrade-0.5.md) (상태 마이그레이션 없음) · [0.3 → 0.4](docs/upgrade-0.4.md) · [CHANGELOG](CHANGELOG.md) |

## 필요한 만큼 설치

도구마다 따로 설치할 수 있고, 우산 패키지는 배치 코어만 끌어온다. Spark·Kubernetes·Iceberg·Kafka
클라이언트 같은 무거운 런타임은 기본 설치에 절대 들어가지 않는다.

| 하고 싶은 것 | 설치 |
|---|---|
| 수집·변환·쿼리·레시피 | `uv tool install de-arsenal` |
| …+ Postgres sink | `uv tool install "de-arsenal[postgres]"` |
| …+ 스트리밍·CDC (Hasta) | `uv tool install "de-arsenal[streaming]"` |
| …+ Spark on Kubernetes 실행 (Ballista) | `uv tool install "de-arsenal[distributed]"` |
| …+ 카탈로그 (Aquila) | `uv tool install "de-arsenal[catalog]"` |
| …+ dbt·dlt 프로젝트 | `"de-arsenal[dbt]"` · `"de-arsenal[dlt]"` |
| 도구 하나만 | `pip install pugio` · `de-gladius` · `de-hasta` · `de-aquila` · … |

## 빠른 시작

**1. Pugio로 수집.** 중간에 죽여도(Ctrl-C) 다시 실행하면 이어서 한다. 다 끝난 뒤 다시 실행하면 아무 일도 없다.

```bash
export GITHUB_TOKEN=ghp_...
pugio run examples/github-issues.yaml
pugio status examples/github-issues.yaml --cost --max-age-seconds 86400
```

**2. Gladius로 변환.** 선언형 단계가 SQL로 컴파일되고, DuckDB에서 실행되기 전에 그 SQL을 읽어 볼 수 있다.

```bash
gladius compile examples/transform.yaml      # 실행될 SQL 그대로 — 마법 없음
gladius run examples/transform.yaml
gladius query "SELECT * FROM './data/issues_clean/*.parquet' LIMIT 10"
```

**3. 아니면 Arsenal이 프로젝트 전체를** `arsenal.yaml` 순서대로 돌린다.

```bash
arsenal init --list          # github-issues · csv-cleanup · api-to-postgres
arsenal init csv-cleanup
arsenal run
arsenal status               # 스트림 상태·분산 run·카탈로그 — JSON, 경보가 있으면 exit 1
```

| 레시피 | 파이프라인 |
|---|---|
| `github-issues` | REST API → 정리된 테이블 |
| `csv-cleanup` | CSV 폴더 → dedup / cast |
| `api-to-postgres` | REST API → 검증 게이트 → Postgres upsert (`[postgres]` 필요) |

**소스에서 쓰려면:** clone 후 `uv sync`, 위 명령 앞에 `uv run`을 붙인다.

## 배치 너머

**스트리밍** — cursor API나 Postgres outbox를, 크래시에도 살아남는 체크포인트로 받는다.

```bash
python examples/hasta/fake_orders_api.py &           # 실습용 로컬 cursor API
hasta run examples/hasta/orders-stream.yaml          # Ctrl-C는 진행 중 batch를 커밋하고 멈춘다
hasta checkpoint show examples/hasta/orders-stream.yaml
```

**분산 실행** — Gladius 변환을 Spark/Kubernetes에서 돌리고, 결과는 run마다 정확히 한 번 Iceberg에 커밋된다.

```bash
ballista plan big.yaml --profile spark.yaml --probe  # 실제 클러스터·스토리지·카탈로그부터 확인
ballista run big.yaml --profile spark.yaml --input-version auto
```

**카탈로그** — 레포가 만드는 모든 데이터셋을 한곳에. 어떤 도구도 Aquila에 의존하지 않고, 스캔은 상태 DB에 쓰지 않는다.

```bash
aquila scan
aquila lineage orders_daily --direction up --format mermaid
aquila history orders                                # 스키마 버전별 diff
aquila tag orders pii=true && aquila search pii
```

## 무기 체계

사용자는 `arsenal` 하나만 알면 된다. 그 아래는 문제마다 하나씩인 독립 도구들이고, Arrow/Parquet 허브로
느슨하게 맞물려 락인이 없다.

| 무기 | 하는 일 | 패키지 | 상태 |
|---|---|---|---|
| **Arsenal** | 우산 CLI — `init` / `run` / `status` / `query`, 원클릭 레시피 3종 | `de-arsenal` | ✅ 안정 |
| **Pugio** | 수집·적재 — REST·파일·DB·dlt, 재개 가능하고 멱등한 sink | `pugio` | ✅ 안정 |
| **Gladius** | 선언형 변환을 DuckDB SQL로 컴파일, 증분 실행·UDF·즉석 쿼리 | `de-gladius` | ✅ 안정 |
| **Scutum** | 데이터 계약, DLQ 정책, 재시도·backoff가 있는 파일 잠금 | `de-scutum` | ✅ 안정 |
| **Scorpio** | freshness 경보, lineage, 스트림 상태와 run 요약 | `de-scorpio` | ✅ 안정 |
| **Spatha** | 오케스트레이션 — 신호를 아는 DAG, 우선순위, 실행 window, 잠금, 명령 태스크 | `spatha` | ✅ 안정 |
| **Onager** | 격리된 백필과 small-file compaction, 실행 중인 스트림 옆에서도 안전 | `de-onager` | ✅ 안정 |
| **Hasta** | 스트리밍·CDC — 폴링 마이크로배치, Postgres outbox, fenced 체크포인트 | `de-hasta` | 🧪 beta |
| **Ballista** | 분산 변환 — Spark on Kubernetes, Iceberg 커밋, DuckDB↔Spark 동치성 | `de-ballista` | 🧪 beta |
| **Aquila** | 카탈로그·거버넌스 — 데이터셋 레지스트리, 스키마 이력, lineage, 계약, 검색 | `de-aquila` | 🧪 preview |
| **Augur** | 레이크 위의 자연어 → SQL(스키마 RAG), 실패 모드 eval 하네스 | `de-augur` | 🧪 실험 |
| **Pilum** | Reverse ETL — webhook·HTTP API·Slack으로 멱등 발사 | — | 📋 계획 (0.6) |

Databricks 제품군과의 대응표: [docs/00-overview.md](docs/00-overview.md).

## 원칙

네 가지 약속을 동시에 지킨다. 하나를 위해 다른 하나를 희생하지 않는다.

- **가볍게** — 도구 하나만 집어 바로 쓴다. `pip install pugio` 하나로도 완결된 시작이다.
- **빠르게** — 벌크·벡터화·병렬. 작다고 느리지 않다.
- **정확하게** — 멱등·재개·검증이 기본값. 끊겨도 중복도 누락도 없다.
- **누구나 쉽게** — YAML, 가끔 SQL. 분석가가 직접 쓰고, 작성할 DAG 코드가 없다.

무엇을 보장하는지, 그리고 무엇을 보장하지 않는지도 똑같이 분명하게: [docs/08-limits.md](docs/08-limits.md).

## 문서

설계 문서는 한국어로 쓰여 있고, YAML 레퍼런스와 CHANGELOG는 언어 중립이다.

| 여기서 시작 | |
|---|---|
| [docs/tools.md](docs/tools.md) | 도구별 사용 시점과 빠른 시작 |
| [docs/reference/pipeline-schema.md](docs/reference/pipeline-schema.md) · [transform-schema.md](docs/reference/transform-schema.md) | 코드에서 생성한 YAML 필드 전체 |
| [docs/08-limits.md](docs/08-limits.md) | 정직한 한계선 — 단일 노드 envelope, sink·도구별 보장 |
| [CHANGELOG.md](CHANGELOG.md) · [업그레이드 0.5](docs/upgrade-0.5.md) · [업그레이드 0.4](docs/upgrade-0.4.md) | 변경 내역과 업그레이드 방법 |

| 설계 | |
|---|---|
| [00-overview](docs/00-overview.md) · [01-scope](docs/01-scope.md) · [roadmap](docs/roadmap.md) | 비전·포지셔닝·범위, 원본 문제 정의 |
| [02-architecture](docs/02-architecture.md) · [03-tech-stack](docs/03-tech-stack.md) | 모노레포 구조, 공통 코어, 상태 모델, 기술 선택 |
| [07-cost-efficiency](docs/07-cost-efficiency.md) · [09-oss-leverage](docs/09-oss-leverage.md) | 비용 구조, 오픈소스에서 빌려 오는 것과 그 이유 |

| 개발·운영 | |
|---|---|
| [04-implementation-plan](docs/04-implementation-plan.md) · [plans/](docs/plans/) | 마일스톤별 작업 분해와 상세 TDD 계획 |
| [05-testing-plan](docs/05-testing-plan.md) · [06-conventions](docs/06-conventions.md) | 테스트 전략, 코딩·커밋·CI 컨벤션 |
| [10-augur-eval-protocol](docs/10-augur-eval-protocol.md) · [dogfooding](docs/dogfooding.md) | Augur 평가 절차, 2주 도그푸딩 runbook |
| [release](docs/release.md) | 버전·빌드·PyPI 릴리스 절차 |
| [11-real-data-experiments](docs/11-real-data-experiments.md) · [experiments/](experiments/real-data/README.md) | 실제 API·데이터(GitHub, GitLab, 공항 CSV, NYC 택시 1,970만 행) 실험 결과, 찾은 버그와 확인한 한계 |

## 라이선스

[Apache-2.0](LICENSE)
