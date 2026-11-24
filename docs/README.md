# 문서 색인

사용자용 문서는 [저장소 README](../README.md)에서 바로 연결된다. 여기는 나머지 전부다.

## 사용

| 문서 | 내용 |
|---|---|
| [quickstart.md](quickstart.md) | 샘플 CSV로 한 바퀴, 세 CLI의 명령, 운영 체크리스트 |
| [tools.md](tools.md) | 도구별 사용 시점과 빠른 시작 |
| [reference/](reference/) | YAML 필드 레퍼런스 (코드에서 생성, 수정 금지) |
| [08-limits.md](08-limits.md) | 보장하는 것과 보장하지 않는 것 |
| [../CHANGELOG.md](../CHANGELOG.md) | 버전별 변경 내역 (0.6.6 `arsenal check`, 0.6.7 `arsenal dataset`, 0.6.8 JSONL NULL 컬럼 수정, 0.6.9 Excel 대장과 DB 인증 오류, 0.6.10 dataset의 Postgres 게시, 0.6.11 DuckDB 2.0 호환) |
| [upgrade-0.6.md](upgrade-0.6.md) · [upgrade-0.5.md](upgrade-0.5.md) · [upgrade-0.4.md](upgrade-0.4.md) | 버전별 업그레이드 절차 |
| [../plugins/de-arsenal/README.md](../plugins/de-arsenal/README.md) | 에이전트 스킬: Claude Code 플러그인 설치, 휴대용 스킬 ZIP |
| [../examples/deploy/](../examples/deploy/README.md) | cron, GitHub Actions, 컨테이너로 주기 실행 |

## 실데이터 검증

| 문서 | 내용 |
|---|---|
| [11-real-data-experiments.md](11-real-data-experiments.md) · [../experiments/real-data/](../experiments/real-data/README.md) | 실제 API와 파일로 돌린 실험, 찾은 버그, 확인한 한계 (0.5.2~0.6.0) |
| [../examples/real-world/d1-snapshot/](../examples/real-world/d1-snapshot/README.md) | Cloudflare D1 테이블 → `arsenal dataset` 게시. 운영 테이블로 수정·삭제 전달을 확인한 기록과 한계 (0.6.7) |
| [../examples/real-world/](../examples/real-world/) | GitHub, Notion, Slack, Stripe, 공공데이터포털 수집 스펙 |

## 설계

| 문서 | 내용 |
|---|---|
| [00-overview.md](00-overview.md) · [01-scope.md](01-scope.md) · [roadmap.md](roadmap.md) | 비전, 범위, 원본 문제 정의 |
| [02-architecture.md](02-architecture.md) · [03-tech-stack.md](03-tech-stack.md) | 모노레포 구조, 공통 코어, 상태 모델, 기술 선택 |
| [07-cost-efficiency.md](07-cost-efficiency.md) · [09-oss-leverage.md](09-oss-leverage.md) | 비용 구조, 오픈소스에서 빌려 오는 것 |
| [adr/](adr/) | 결정 기록 (Aquila, Pilum) |

## 개발·운영

| 문서 | 내용 |
|---|---|
| [06-conventions.md](06-conventions.md) · [05-testing-plan.md](05-testing-plan.md) | 코딩·커밋·CI 컨벤션, 테스트 전략 |
| [release.md](release.md) · [reference/packaging.md](reference/packaging.md) | 릴리스 절차, 패키징 노트 |
| [dogfooding.md](dogfooding.md) | 2주 도그푸딩 runbook |
| [10-augur-eval-protocol.md](10-augur-eval-protocol.md) · [augur-analysis-template.md](augur-analysis-template.md) | Augur 평가 절차와 분석 글 틀 |

## 작업 기록

[04-implementation-plan.md](04-implementation-plan.md)과 [plans/](plans/)는 마일스톤별 작업 계획과
당시의 진행 기록이다. 현재 동작의 기준 문서가 아니다. 현재 동작은 위의 레퍼런스와
[08-limits.md](08-limits.md)를 본다.
