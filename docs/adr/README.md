# Architecture Decision Records

결정 하나당 파일 하나. 상태는 `제안` → `승인` → (필요하면) `대체됨: ADR-…`.
P2 설계 단계의 결정(P2-ADR-1~)은 [P2 설계 문서](../plans/2026-08-22-p2-hasta-ballista-design.md)의
표에 있고, 이 디렉터리는 Aquila부터 시작한다.
PL-ADR-3은 2026-11-17 계획의 결정(StateStore v4)을 바꾼 것이다.

| ADR | 제목 | 상태 |
|---|---|---|
| [AQ-ADR-1](AQ-ADR-1-pull-model.md) | Aquila는 pull 모델 — 스캔이 읽고, 아무도 Aquila에 쓰지 않는다 | 승인 (2026-11-17) |
| [AQ-ADR-2](AQ-ADR-2-separate-catalog-db.md) | 카탈로그는 파이프라인 StateStore와 분리된 `aquila.db` | 승인 (2026-11-17) |
| [AQ-ADR-3](AQ-ADR-3-idempotent-scan.md) | 스캔은 멱등 — 결정적 키, 재스캔은 행을 늘리지 않는다 | 승인 (2026-11-17) |
| [AQ-ADR-4](AQ-ADR-4-augur-boundary.md) | Aquila가 기준 원천, Augur는 읽기 전용 소비자 | 승인 (2026-11-17) |
| [PL-ADR-1](PL-ADR-1-dispatch-id.md) | dispatch id는 이름·레코드 키·내용으로만 만든다 | 승인 (2026-11-18) |
| [PL-ADR-2](PL-ADR-2-ledger-after-success.md) | 원장 기록은 2xx 응답 뒤에만 한다 | 승인 (2026-11-18) |
| [PL-ADR-3](PL-ADR-3-separate-ledger-file.md) | 원장은 StateStore가 아닌 전용 SQLite 파일에 둔다 | 승인 (2026-11-18) |
| [PL-ADR-4](PL-ADR-4-mirror-opt-in.md) | 삭제 전송은 mirror 모드와 비율 상한 뒤에 둔다 | 승인 (2026-11-18) |
| [PL-ADR-5](PL-ADR-5-first-dispatch-limit.md) | 첫 실행은 건수 상한을 넘으면 거부한다 | 승인 (2026-11-18) |
