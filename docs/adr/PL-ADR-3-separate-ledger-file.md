# PL-ADR-3 — 원장은 StateStore가 아닌 전용 SQLite 파일에 둔다

- 상태: 승인 (2026-11-18)
- 관련: `packages/pilum/`, [계획](../plans/2026-11-17-pilum-aquila-plan.md)

## 맥락

2026-11-17 계획은 원장을 StateStore v4 마이그레이션(`dispatch_ledger`, `dispatch_dlq`)으로
두기로 했다. 그러면 0.6으로 올리는 모든 파이프라인 상태 DB가 v3→v4로 마이그레이션·백업되고,
0.5로의 롤백에 복원 절차가 생긴다. Pilum은 0.6에서 experimental이다.

## 결정

계획을 바꾼다. 원장은 dispatch마다 `<state_dir>/<name>.pilum.db` 파일 하나이고 스키마 버전은
그 파일의 `user_version`으로 관리한다(현재 v1). StateStore는 v3 그대로다. 동시 실행 방지는
옆의 `<name>.pilum.lock`에 건 SQLite 배타 트랜잭션으로 한다.

## 결과

- 실험 도구를 설치해도 기존 파이프라인 상태에는 아무 일도 일어나지 않는다. AQ-ADR-2와 같은 원칙이다.
- `de-pilum`은 `arsenal-core`만 의존하고 Pugio·Scutum을 끌어오지 않는다.
- StateStore의 lease·백업 프레임워크는 재사용하지 못한다. Pilum이 안정 단계로 갈 때 원장을
  StateStore로 옮길지 다시 결정한다.
