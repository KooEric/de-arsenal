# PL-ADR-1 — dispatch id는 이름·레코드 키·내용으로만 만든다

- 상태: 승인 (2026-11-18)
- 관련: `packages/pilum/`, [계획](../plans/2026-11-17-pilum-aquila-plan.md)

## 맥락

재실행이 같은 레코드를 다시 보내지 않으려면 "이미 보낸 것"을 식별하는 결정적 ID가 필요하다.
계획 초안은 destination도 해시에 넣었다. Hasta 0.5.2에서 header 값(토큰)이 revision에
들어가 있어 토큰 교체가 batch를 중복시킨 버그가 있었다.

## 결정

`dispatch_id = hash(dispatch 이름, 레코드 키, 내용 해시)[:32]`. destination의 URL·header는
넣지 않는다. 삭제는 내용 자리에 `delete:<마지막으로 보낸 내용 해시>`를 쓴다.

## 결과

- 토큰을 교체하거나 끝점 URL을 옮겨도 재전송이 일어나지 않는다
  (`test_rotating_the_token_does_not_resend`).
- 한 스펙은 destination 하나만 가진다. 같은 데이터를 두 곳에 보내려면 이름이 다른 스펙 둘을 쓴다.
- 끝점을 새 시스템으로 바꿔 전량을 다시 보내야 하면 `pilum reset --confirm`을 쓴다.
