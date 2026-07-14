# DE Arsenal

> **노트북 한 대가 데이터 플랫폼이 된다.** 설치 하나, 명령 하나로 수집→검증→변환→쿼리.

데이터 엔지니어와 분석가가 매일 부딪히는 문제들 — 새벽에 끊긴 수집, 만료된 토큰, 쿼리도 못 하는 CSV 뭉치 — 에 대한 **원클릭 솔루션**. 서버도, 클러스터도, DAG 코드도 없다.

```bash
uv tool install de-arsenal        # 설치 한 번 (v0.1 예정)
arsenal init csv-cleanup           # 레시피로 시작
arsenal run                        # 수집→검증→변환. 끊겨도 재실행하면 이어서
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

Databricks·Snowflake 수준의 **완성도**를, 그들과 정반대의 **형태**로. 거대 플랫폼이 백 가지를 80점으로 하는 동안, 우리는 한 가지를 100점으로. 신뢰성(멱등·재개·검증)이 기본값이고, 마진 없는 비용 구조([docs/07](docs/07-cost-efficiency.md))가 아키텍처에서 나온다.

## 무기 체계 (내부 아키텍처)

사용자는 `arsenal` 하나만 알면 된다. 그 아래는 문제 영역별 독립 도구들 — 각각 따로 설치·사용 가능하고(락인 없음), Arrow/Parquet 허브로 느슨하게 맞물린다.

| 무기 | 담당 영역 | 우선순위 | 상태 |
|---|---|---|---|
| **Arsenal** (우산 CLI) | 단일 진입점 — init/run/query, 원클릭 레시피 | P0 (M4) | 🚧 뼈대 |
| **Pugio** | 수집·전송 (ETL 엔진) | P0 | 🚧 개발 중 |
| **Gladius** | 변환·쿼리 (핵심 처리) | P0 | 🚧 개발 중 |
| **Spatha** | 오케스트레이션 (의존성·스케줄링) | P0(멱등 코어)/P1 | 📋 계획 |
| **Scutum** | 데이터 품질·검증·보호 | P0(멱등 가드)/P1 | 🚧 코어 내장 |
| **Scorpio** | 관측성 (모니터링·lineage) | P1 | 📋 계획 |
| **Onager** | 백필·대규모 재처리 | P1 | 📋 계획 |
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
| [docs/09-oss-leverage.md](docs/09-oss-leverage.md) | 오픈소스 차용 전략 — 차용 지도, dlt·dbt 인터롭, 쓸만함 판정 기준 |
| [docs/plans/](docs/plans/) | 마일스톤별 상세 TDD 구현 계획 |
| [docs/roadmap.md](docs/roadmap.md) | 원본 로드맵 (문제 정의 전체) |

## 개발 상태에서 시작하기

M1 완료 — REST→Parquet 수집이 재개·멱등 보장과 함께 동작한다 (uv tool 배포는 M4).

```bash
uv sync

export GITHUB_TOKEN=ghp_...                     # examples/github-issues.yaml이 참조하는 시크릿
uv run pugio run examples/github-issues.yaml    # 수집 — 중간에 죽여도(Ctrl-C) 재실행하면 이어서, 완주 후 재실행은 no-op
uv run pugio status examples/github-issues.yaml # unit 상태 요약 (done/pending/failed/quarantined 수)
```

변환·쿼리(`gladius run` / `gladius query`)는 아직 스텁이다 — M2/M3에서 구현 예정([docs/04-implementation-plan.md](docs/04-implementation-plan.md)).

## 라이선스

TBD (Apache-2.0 예정)
