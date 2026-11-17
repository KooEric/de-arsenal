# 0.3 → 0.4 업그레이드 가이드

0.4.0은 11개 배포 패키지를 함께 올리는 minor 릴리스다. 기존 배치 파이프라인
(Pugio 수집·Gladius 변환·`arsenal run`)의 **결과는 바뀌지 않는다** — unit id,
sink 파일명, 변환 SQL은 0.3과 같다. 바뀌는 것은 상태 DB 스키마와 패키지 핀이다.

## 1. 한꺼번에 설치한다

교차 패키지 핀이 `>=0.4,<0.5`로 이동했다. 일부만 올리면 resolver가 거부한다.

```bash
uv tool install --force de-arsenal            # 우산 CLI + 기본 도구
uv tool install --force "de-arsenal[streaming]"    # + Hasta
uv tool install --force "de-arsenal[distributed]"  # + Ballista (클러스터 런타임은 별도)
```

기본 설치는 Spark·Kubernetes·Iceberg·Kafka·obstore·psycopg를 끌어오지 않는다
(`packages/arsenal/tests/test_base_install.py`가 의존성 closure로 고정).

## 2. 상태 DB는 처음 열 때 자동 마이그레이션된다

| | 0.3 | 0.4 |
|---|---|---|
| 스키마 | `user_version` 없음(=0), 테이블 4개 | v3 — `runs`, `leases`, `stream_checkpoints`, `stream_batches` 추가 |
| 기존 테이블 | `units`·`cursors`·`schema_snapshots`·`lineage` | 변경 없음 |

0.4 프로세스(`pugio run`, `arsenal run`, `hasta …`, `ballista …` 무엇이든)가 0.3
상태 DB를 처음 열면:

1. 마이그레이션 **전에** 같은 디렉터리의 `backups/<db이름>-v0-<UTC시각>.sqlite`로
   SQLite online backup을 만든다.
2. 버전마다 하나의 immediate 트랜잭션으로 v1 → v2 → v3를 적용한다. 중간에 실패하면
   그 버전의 트랜잭션은 롤백되고 다음 실행에서 이어서 적용한다.

미리 확인하려면 DB 복사본을 0.4로 한 번 열어 본다.

```bash
cp state/<pipeline>.db /tmp/probe.db    # pugio: <state_dir>/<name>.db
python -c "from pathlib import Path; from arsenal_core.state import StateStore; s=StateStore(Path('/tmp/probe.db')); print(s.schema_version)"
```

### 롤백 경계

- **마이그레이션은 forward-only다.** 0.4는 자기보다 새로운 스키마를 거부하고
  다운그레이드 경로를 제공하지 않는다.
- 0.3으로 돌아가려면 **모든 러너를 멈추고 위 백업 파일을 원래 이름으로 복원**한다.
  백업 이후 0.4에서 완료된 unit은 0.3이 모르므로 다음 실행에서 다시 수집된다 —
  parquet sink는 같은 unit id → 같은 파일이라 중복 없이 덮어쓰고, duckdb/postgres는
  `merge_key` upsert라 결과가 같다.
- Hasta·Ballista는 0.3에 없으므로 자기 상태 DB(스트림 체크포인트·run 기록)를 0.3이
  읽지 않는다. 롤백 전에 스트림을 멈추고 진행 중인 Ballista run을 끝내 두면, 다시
  0.4로 올렸을 때 그 상태에서 그대로 재개된다.

## 3. 동작이 바뀐 것

- **`onager compact`가 거부할 수 있다.** Hasta 스트림이 쓰는 Parquet 디렉터리는
  러너가 lease를 잡고 있거나 써 두고 커밋하지 않은 batch가 있으면 `DatasetBusyError`로
  거부한다. 스트림을 멈추거나 다음 batch 커밋 뒤에 다시 실행한다. 스트림이 없는
  데이터셋은 영향 없다.
- **Gladius `compile_sql()`은 그대로 동작한다.** 내부가 portable `LogicalPlan`을
  거치도록 바뀌었지만 생성 SQL은 같다. Ballista로 보낼 변환에서만 `concat()`·raw
  `sql`·Python UDF step이 preflight에서 거부된다(`concat` 대신 `||`).

## 4. 새로 쓸 수 있는 것

- `arsenal.yaml`에 `streams:`(Hasta)와 `distributed:`(Ballista) 단계, `arsenal status`.
- `hasta run / checkpoint show / checkpoint reset` — [packages/hasta/README.md](../packages/hasta/README.md)
- `ballista plan --probe / run / reconcile` — [packages/ballista/README.md](../packages/ballista/README.md)
- 보장·비보장 범위: [08-limits.md](08-limits.md)
