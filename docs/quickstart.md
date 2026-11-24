# 빠른 시작과 운영 체크리스트

## 한 바퀴 돌기

샘플 CSV로 수집 → 변환 → 쿼리 → 검증까지 명령 4개면 된다.

```bash
arsenal init csv-cleanup     # input/sample.csv가 든 프로젝트를 만든다
arsenal run                  # collect.yaml로 수집한 뒤 transform.yaml로 변환
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
arsenal check                # 모든 스펙을 실행 없이 검증
```

정상이면 이렇게 끝난다.

```text
$ arsenal run
  collect collect.yaml: fetched=1 written=1 skipped=0
  transform transform.yaml: data/clean
done: 1 pipeline(s), 1 transform(s)

$ arsenal check
ok     collect.yaml: file -> parquet
ok     transform.yaml: compiles
all specs valid
```

자기 데이터로 하려면 `input/sample.csv`를 바꾸고 `collect.yaml`·`transform.yaml`을 고친다.

## 명령은 세 CLI로 나뉜다

| CLI | 맡는 일 | 명령 |
|---|---|---|
| `arsenal` | 프로젝트 전체 (`arsenal.yaml`) | `init`, `check`, `run`, `status`, `query`, `dataset preview` / `apply` / `status` |
| `pugio` | 수집. 스펙 파일 하나 단위 | `run <spec>`, `status <spec>`, `dlq list <spec>`, `dlq retry <spec> --unit <id>`, `ddl <spec>` |
| `gladius` | 변환. 스펙 파일 하나 단위 | `compile <spec>`, `run <spec>`, `query <sql>` |

`arsenal run`은 `arsenal.yaml`의 `pipelines:`를 `pugio run`으로, `transforms:`를 `gladius run`으로
차례로 실행한다. 한 단계만 다시 돌리거나 상태를 자세히 볼 때 `pugio`·`gladius`를 직접 쓴다.
`gladius compile`은 변환이 실행할 DuckDB SQL을 출력한다.

## 운영 체크리스트

테스트하면서 실제로 부딪힌 문제들이다.

- [ ] **실패는 `arsenal status`로, 오래된 데이터는 `pugio status --max-age-seconds`로 감시한다.**
      0.6.12부터 `arsenal status`는 `pipelines:`의 수집 파이프라인마다 unit 상태를 보여 주고, `failed`나
      `quarantined` unit이 있으면 `alert: true`에 exit 1을 낸다. 다만 마지막 성공이 얼마나 오래됐는지는 보지
      않으므로, 수집 스펙마다 `pugio status <spec> --max-age-seconds <n>`도 함께 건다(기준을 넘으면 exit 1).
      **0.6.11 이하**에서는 `arsenal status`가 수집 파이프라인을 보지 않아 수집이 실패해도 exit 0을 낸다.
      그 버전에서는 `pugio status <spec>` 출력의 `failed:`·`quarantined:` 줄을 직접 확인한다.
      `pugio status`는 freshness 초과 때만 exit 1을 내기 때문이다.
- [ ] **`schema_drift: block`을 쓰면 `source.column_types`를 고정한다.** 타입은 파일마다 추론하므로
      빈 값이나 숫자로만 된 문자열 하나로 타입이 바뀌고, `block`이면 그 실행이 실패한다. 우편번호·코드처럼
      문자열이어야 하는 컬럼은 특히 고정한다.
- [ ] **같은 프로젝트를 동시에 실행하지 않는다.** cron 간격이 실행 시간보다 짧으면 겹친다. 겹친 쪽은
      `pipeline ... is busy`(수집) 또는 `transform ... is busy`(변환) 오류로 멈추고, 데이터는 깨지지
      않지만 그 회차는 건너뛴다. 0.6.12까지는 변환 단계에서 겹치면 한쪽이 `FileNotFoundError`
      traceback으로 죽었다(데이터는 정상).
- [ ] **실패한 파일을 지우면 다음 실행 뒤 알림이 풀린다(0.6.13+).** 폴더를 끝까지 읽은 실행이 끝나면 없어진
      파일의 실패 기록은 `withdrawn`이 된다. 0.6.12까지는 같은 이름의 파일이 다시 적재돼야 풀렸다.
- [ ] **비정상 종료 뒤에는 약 1분 기다린다.** `kill -9`, OOM, 전원 차단 뒤에는 실행 락(lease, TTL 60초)이
      만료될 때까지 재실행이 `is busy`로 거절된다. 1분 뒤 다시 돌리면 끝난 unit은 건너뛰고 이어서 한다.
      Ctrl-C는 락을 바로 푼다.
- [ ] **서버 2대는 서버마다 독립 프로젝트로 운영한다.** 진행 상태와 락은 프로젝트 폴더의 SQLite 파일
      (`.arsenal/<프로젝트 이름>.db`)에 있고, SQLite 락은 NFS 같은 네트워크 파일시스템에서 믿을 수 없다.
      두 서버가 한 프로젝트 폴더를 공유하지 말고, 서버마다 프로젝트 폴더와 출력 경로를 따로 둔다.

보장 범위 전체는 [08-limits.md](08-limits.md), 주기 실행 설정은
[examples/deploy/](../examples/deploy/README.md)에 있다.
