# PL-ADR-5 — 첫 실행은 건수 상한을 넘으면 거부한다

- 상태: 승인 (2026-11-18)
- 관련: `packages/pilum/`, [계획](../plans/2026-11-17-pilum-aquila-plan.md)

## 맥락

원장이 비어 있는 첫 실행은 소스 전체를 보낸다. 필터를 빠뜨린 스펙은 전 고객에게 알림을 보낸다.

## 결정

원장이 비어 있고 보낼 건수가 `safety.max_first_dispatch`(기본 1,000)를 넘으면 거부한다.
`--confirm-initial-load`로만 해제한다. `pilum plan`은 아무것도 보내거나 쓰지 않고 건수와 키
샘플을 보여 준다.

## 결과

`pilum reset` 뒤의 실행도 원장이 비어 있으므로 같은 상한을 받는다.
