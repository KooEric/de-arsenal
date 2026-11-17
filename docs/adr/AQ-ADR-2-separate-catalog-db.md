# AQ-ADR-2 — 카탈로그는 별도의 `aquila.db`에 둔다

- 상태: 승인 (2026-11-17)
- 관련: P2-ADR-7(state DB는 single-host coordinator only), `packages/aquila/src/aquila/store.py`

## 맥락

파이프라인 상태 DB(`<state_dir>/<name>.db`)는 그 도구만 쓴다(P2-ADR-7). `StateStore`로
열면 forward 마이그레이션과 백업이 실행되므로, 다른 프로세스가 "읽으려고" 여는 것조차
상태를 바꿀 수 있다.

## 결정

- 카탈로그는 자기 전용 SQLite 파일(기본 `.arsenal/aquila.db`)에 쓴다. 스키마 버전은
  그 파일의 `user_version`으로 따로 관리한다(현재 v1).
- 다른 도구의 상태 DB는 `StateStore`가 아니라 **sqlite 읽기 전용 URI**(`?mode=ro`)로 연다.

## 결과

- 스캔은 상태 DB를 마이그레이션·백업·잠그지 않는다. `test_state_databases_are_never_modified`가
  바이트 단위로 확인한다.
- 실행 중인 러너와 동시에 스캔해도 안전하다. 단 WAL DB의 아직 체크포인트되지 않은 마지막
  커밋은 이번 스캔에서 안 보일 수 있고, 다음 스캔에서 반영된다.
- Pilum의 StateStore v4 마이그레이션과 번호가 충돌하지 않는다.
