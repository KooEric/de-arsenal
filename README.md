# DE Arsenal

> **노트북 한 대가 데이터 플랫폼이 된다.** 설치 하나, 명령 하나로 수집→검증→변환→쿼리.

데이터 엔지니어와 분석가가 매일 부딪히는 문제들 — 새벽에 끊긴 수집, 만료된 토큰, 쿼리도 못 하는 CSV 뭉치 — 에 대한 **원클릭 솔루션**. 서버도, 클러스터도, DAG 코드도 없다.

```bash
uv tool install de-arsenal        # (PyPI 배포 예정 — 현재는 소스 설치, 아래 "시작하기" 참고)
arsenal init csv-cleanup           # 레시피로 시작 (github-issues · csv-cleanup · api-to-postgres)
arsenal run                        # 수집→검증→변환. 끊겨도 재실행하면 이어서
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

> **상태: v0.2.0 릴리스 준비.** 수집·변환·우산 CLI와 P1의 dlt/dbt, schema drift, cloud sink, UDF, 관측성·오케스트레이션·백필·계약 패키지가 동작한다. 변환 패키지의 PyPI 배포명은 `de-gladius`이며 import와 CLI는 `gladius`를 유지한다.

Databricks·Snowflake 수준의 **완성도**를, 그들과 정반대의 **형태**로. 거대 플랫폼이 백 가지를 80점으로 하는 동안, 우리는 한 가지를 100점으로. 신뢰성(멱등·재개·검증)이 기본값이고, 마진 없는 비용 구조([docs/07](docs/07-cost-efficiency.md))가 아키텍처에서 나온다.

## 무기 체계 (내부 아키텍처)

사용자는 `arsenal` 하나만 알면 된다. 그 아래는 문제 영역별 독립 도구들 — 각각 따로 설치·사용 가능하고(락인 없음), Arrow/Parquet 허브로 느슨하게 맞물린다.

| 무기 | 담당 영역 | 우선순위 | 상태 |
|---|---|---|---|
| **Arsenal** (우산 CLI) | 단일 진입점 — init/run/query, 원클릭 레시피 3종 | P0 (M4) | ✅ v0.2.0 준비 |
| **Pugio** | 수집·전송 (ETL 엔진) | P0 + P1 | ✅ v0.2.0 준비 |
| **Gladius** | 변환·쿼리 (핵심 처리) | P0 + P1 | ✅ v0.2.0 준비 |
| **Spatha** | 오케스트레이션 (의존성·스케줄링) | P0(멱등 코어)/P1 | ✅ signal DAG · priority · window · lock |
| **Scutum** | 데이터 품질·검증·보호 | P0(멱등 가드)/P1 | ✅ contract · DLQ 정책 · lock retry/backoff |
| **Scorpio** | 관측성 (모니터링·lineage) | P1 | ✅ lineage · freshness alert · cost 요약 |
| **Onager** | 백필·대규모 재처리 | P1 | ✅ isolated backfill · small-file compaction |
| **Hasta** | 스트리밍·CDC | P2 | 📋 계획 |
| **Pilum** | 디스패치·reverse ETL | P2 | 📋 계획 |
| **Ballista** | 대규모 분산 처리 | P2 | 📋 계획 |
| **Aquila** | 카탈로그·거버넌스 (군단기 — 자산의 기준점) | P2 | 💡 제안 |

> Databricks 제품군의 저비용 고효율 미니 버전을 지향한다 — 대응표는 [docs/00-overview.md](docs/00-overview.md), 비용 구조는 [docs/07-cost-efficiency.md](docs/07-cost-efficiency.md).

## 제품 철학

네 가지를 동시에 만족시킨다. 하나를 위해 나머지를 희생하지 않는다.

- **가볍게** — 한 도구만 집어 바로 쓸 수 있다. `pip install pugio` 하나로 시작.
- **빠르게** — 벌크·벡터화·병렬. 작다고 느리지 않다.
- **정확하게** — 멱등·재개·검증이 기본값. 끊겨도 중복·누락 없음.
- **누구나 쉽게** — 선언형(YAML + 가끔 SQL). 분석가도 바로 쓴다. DAG 코드 없음.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | 비전·포지셔닝·설계 원칙 |
| [docs/01-scope.md](docs/01-scope.md) | 구축 범위 — 마일스톤 M0~M4, P1/P2 이후 계획, Out of Scope |
| [docs/02-architecture.md](docs/02-architecture.md) | 모노레포 구조, 공통 코어, 상태 모델, 멱등 전략 |
| [docs/03-tech-stack.md](docs/03-tech-stack.md) | 기술 스택 선정과 근거, 대안 비교 |
| [docs/04-implementation-plan.md](docs/04-implementation-plan.md) | 마일스톤별 작업 분해(WBS)와 완료 기준 |
| [docs/05-testing-plan.md](docs/05-testing-plan.md) | 테스트 전략 — 단위/통합/E2E, 신뢰성 시나리오 |
| [docs/06-conventions.md](docs/06-conventions.md) | 코딩·커밋·브랜치·PR·CI 컨벤션 |
| [docs/07-cost-efficiency.md](docs/07-cost-efficiency.md) | 저비용 고효율 설계 — 비용 구조, 증분 처리, 효율 목표치 |
| [docs/08-limits.md](docs/08-limits.md) | 정직한 한계선 — 단일 노드 처리 envelope, 미지원 범위, sink별 멱등 보장 |
| [docs/09-oss-leverage.md](docs/09-oss-leverage.md) | 오픈소스 차용 전략 — 차용 지도, dlt·dbt 인터롭, 쓸만함 판정 기준 |
| [docs/tools.md](docs/tools.md) | 도구별 사용 시점·빠른 시작·장점 비교 |
| [docs/plans/](docs/plans/) | 마일스톤별 상세 TDD 구현 계획 |
| [docs/roadmap.md](docs/roadmap.md) | 원본 로드맵 (문제 정의 전체) |
| [docs/release.md](docs/release.md) | v0.2.0 버전·빌드·PyPI 릴리스 절차 |

## 시작하기 (소스에서)

v0.2.0 (P1) 릴리스 준비 — 수집·변환·쿼리·우산 CLI와 확장 패키지가 동작한다. 릴리스 절차는 [docs/release.md](docs/release.md)를 따른다.

```bash
uv sync

# 1) 수집 (pugio) — 중간에 죽여도(Ctrl-C) 재실행하면 이어서, 완주 후 재실행은 no-op
export GITHUB_TOKEN=ghp_...
uv run pugio run examples/github-issues.yaml
uv run pugio status examples/github-issues.yaml   # unit 상태 요약 (done/pending/failed/quarantined)
uv run pugio status examples/github-issues.yaml --cost --max-age-seconds 86400

# 2) 변환 (gladius) — 선언형 map/steps가 SQL로 컴파일되어 DuckDB에서 실행
uv run gladius compile examples/transform.yaml    # 생성될 SQL을 그대로 확인 (마법 없음)
uv run gladius run examples/transform.yaml
uv run gladius query "SELECT * FROM './data/issues_clean/*.parquet' LIMIT 10"   # 미니 DWH

# 3) 우산 CLI (arsenal) — 레시피 스캐폴드 → 수집→변환 일괄
uv run arsenal init --list                        # 레시피 목록
uv run arsenal init csv-cleanup                    # 프로젝트 스캐폴드
uv run arsenal run                                 # arsenal.yaml 순서대로 수집→변환
uv run arsenal query "SELECT ..."
```

원클릭 레시피 3종: **github-issues**(API→정리된 테이블), **csv-cleanup**(CSV 뭉치→dedup/cast), **api-to-postgres**(API→검증 게이트→PG upsert). `api-to-postgres`는 postgres extra가 필요하다: `uv tool install "de-arsenal[postgres]"` (또는 소스에서 `pugio[postgres]`).

지원하는 YAML 필드 전체는 코드에서 자동 생성된다 — [docs/reference/pipeline-schema.md](docs/reference/pipeline-schema.md) · [transform-schema.md](docs/reference/transform-schema.md). 변경 이력은 [CHANGELOG.md](CHANGELOG.md), 정직한 한계선은 [docs/08-limits.md](docs/08-limits.md).

## 라이선스

[Apache-2.0](LICENSE)
