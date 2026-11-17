# P2 Distributed Build Checkpoint

작성일: 2026-08-23

이 문서는 de-arsenal P2(Hasta/Ballista) 분산 작업을 다음 세션에서 재개하기 위한 체크포인트다.

## Orca 실행 정보

- Run: `run_5a8e4472cf19`
- Coordinator terminal: `term_d9f16ba7-ca03-4e37-9b15-c3b125b598c2`
- Worker model policy:
  - Sol max: StateStore migration, shared execution foundation, Ballista execution contract
  - Terra xhigh: Hasta, CDC, Gladius implementation and tests
  - Luna xhigh: Pugio sink capabilities, integration, docs, smoke checks
- Runtime at checkpoint: healthy/reachable (`c4db1adb-e1fd-472c-b473-3d6a0ba98ff5`)

## 완료된 작업

### P2-0B — shared foundation (Sol max)

- `arsenal-core`에 engine-neutral execution models/protocols/capabilities/artifact proofs 추가
- deterministic canonical hashing 추가
- StateStore v2 forward migration, backup, fenced lease, run/stream batch/checkpoint commit 추가
- 기존 v0.2 unit/cursor 동작 보존
- 검증: `uv run pytest -q` 당시 349 passed, 21 skipped, coverage 89.34%

### P2-3A — Gladius LogicalPlan v1 (Terra xhigh)

- immutable portable LogicalPlan v1 추가
- 기존 `compile_sql()` 호환 유지 및 DuckDB renderer 연결
- portable subset preflight/rejection 추가
- raw SQL/Python UDF/DuckDB-only 표현식은 local-only로 보존하고 portable 실행에서 거부
- Gladius 테스트 48개, Ruff, Pyright 통과

### P2-1A — Hasta REST/microbatch (Terra xhigh)

- typed StreamSpec/CDC-ready models
- deterministic microbatch ID
- fenced lease/checkpoint/write commit
- Pugio/Scutum contract/schema/DLQ 연동
- CLI `--once`/`--max-batches`

### P2-2 — Hasta CDC v1 (Terra xhigh)

- immutable Postgres outbox envelope(I/U/D), ordered sequence checkpoint
- DuckDB/Postgres current-state transactional apply
- event-ledger idempotency 및 crash/retry coverage
- strict table-cursor safety와 hard-delete 경고
- compatibility `ChangeEvent` export 유지
- 검증: Hasta/Pugio focused/full suite 192 passed, 23 skipped; Ruff 및 targeted Pyright 통과

### P2-0C — Pugio sink contract (Luna xhigh)

- local Parquet/raw object Parquet/DuckDB/Postgres capability 선언
- legacy sink API를 보존하는 `SinkContractAdapter`
- 성공 write 후에만 OutputProof 생성
- write-twice/partial-failure contract 테스트
- 검증: Pugio 177 passed, 22 Docker-dependent skipped; targeted Ruff/format/Pyright 통과

### P2-3B — Ballista v1 skeleton (Sol max)

- strict execution profiles와 capability/environment preflight
- canonical immutable manifest/proof
- Spark Kubernetes lifecycle interface
- transactional worker boundary 및 CLI scaffolding
- mocked recovery tests
- 의도적으로 남긴 범위: live remote blob adapter, real cluster probe, concrete Iceberg committer, kind/MinIO/catalog E2E
- 검증: Ballista 19 tests, core/Gladius/Ballista 145 tests, targeted Ruff, strict Pyright, uv lock check 통과

## 통합 워커가 완료한 추가 정리

P2-4 Luna xhigh 워커가 다음을 수행했으며, 마지막 완료 보고는 아직 받지 못했다.

- 전체 회귀: `392 passed, 24 skipped`, coverage `84.31%`
- workspace package version을 `0.3.0`으로 정렬하고 내부 dependency range를 `>=0.3,<0.4`로 정렬
- root optional extras 추가: `streaming = ["de-hasta"]`, `distributed = ["de-ballista"]`
- `uv lock` 재생성: 141 packages resolved
- `scripts/gen_schema_docs.py`에 Hasta StreamSpec와 Ballista profile schema/reference 생성 연결
- 생성된 산출물:
  - `schemas/stream.json`
  - `schemas/ballista-profile.json`
  - `docs/reference/stream-schema.md`
  - `docs/reference/ballista-profile-schema.md`
- generated schema check에서 artifacts가 최신임을 확인
- 초기 전체 Pyright 28개 오류를 새 P2/state/CDC 표면 중심으로 축소했으며, canonical.py 및 기존 Onager test typing을 보정하는 중이었다.

## 현재 미완료/복구 필요

- P2-4 task: `task_8a5682fc4560`
- P2-4 dispatch: `ctx_6945f7c82b53`
- 마지막 워커 터미널: `term_90fdeb2a-30c8-4471-b1f2-6561fc7bc8cf` (GPT-5.6 Luna xhigh)
- 워커가 응답 스트림 DNS 오류로 `Reconnecting...` 상태가 되었고, `worker_done`을 보내지 못했다.
- 일시정지 시도 결과 dispatch는 `stop_unknown`/capability revoked 상태가 되었고, 외부 터미널 자체는 닫히지 않았다. 재개 시 새 dispatch 또는 동일 터미널 재개 여부를 먼저 확인한다.
- 워크스페이스 변경은 보존되어 있다. 다음 재개 시:
  1. `orca status --json`으로 runtime 확인
  2. `orca orchestration worker-show --dispatch ctx_6945f7c82b53` 확인
  3. Luna 터미널에서 현재 변경을 재검토하고 남은 focused/full validation 실행
  4. P2-4 범위 밖 redesign 없이 필요한 최소 수정만 수행
  5. `worker_done`을 정확히 한 번 전송하고 coordinator에서 ack/release
  6. 최종적으로 `task-list`에서 P2-4가 completed인지 확인

## 주의사항

- 아직 commit/push는 수행하지 않았다.
- Docker 의존 테스트와 실제 외부 클러스터/스토리지 E2E는 환경상 skip 또는 skeleton 범위다.
- 다음 세션에서는 이미 완료된 P2-0B/P2-1A/P2-2/P2-0C/P2-3A/P2-3B를 재구현하지 말고, P2-4 복구와 최종 검증에 집중한다.
