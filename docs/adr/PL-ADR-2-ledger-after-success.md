# PL-ADR-2 — 원장 기록은 2xx 응답 뒤에만 한다

- 상태: 승인 (2026-11-18)
- 관련: `packages/pilum/`, [계획](../plans/2026-11-17-pilum-aquila-plan.md)

## 맥락

보내기 전에 기록하면 전송 실패가 영구 누락이 된다. 보낸 뒤에 기록하면 그 사이의 크래시가
재전송이 된다.

## 결정

레코드 하나의 2xx 응답을 받은 뒤에 그 레코드만 원장에 커밋한다(레코드당 커밋 1회).
재전송에는 같은 dispatch id를 `idempotency_header`로 다시 싣는다.

## 결과

- 전달 보장은 at-least-once다. 끝점이 멱등 키로 중복을 걸러 줄 때만 exactly-once가 된다.
  `pilum status`가 스펙에 멱등 header가 있는지 표시한다.
- Slack webhook은 멱등 키가 없어, 응답과 원장 기록 사이에 죽으면 메시지가 한 번 더 나간다.
- 크래시 경로는 `test_crash_after_response_resends_with_the_same_idempotency_key`가 고정한다.
