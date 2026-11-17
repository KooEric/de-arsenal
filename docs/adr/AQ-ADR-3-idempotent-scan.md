# AQ-ADR-3 — 스캔은 멱등이다

- 상태: 승인 (2026-11-17)
- 관련: `packages/aquila/src/aquila/uri.py`, `store.py`

## 맥락

`aquila scan`은 cron·CI에서 반복 실행된다. 실행할 때마다 데이터셋이나 간선이 늘면
카탈로그를 믿을 수 없다. 같은 위치를 도구마다 다르게 적는 것(`out/`, `./out`,
`out/*.parquet`)도 중복의 원인이다.

## 결정

- `dataset_id = canonical_hash(normalize_uri(uri))[:16]`. 정규화: 스킴 소문자, 상대 경로는
  스캔 루트 기준 절대 경로, 끝 glob·`/` 제거, 쿼리·fragment·URL 자격증명 제거,
  `${VAR}`은 치환하지 않음. property test로 고정한다.
- 스키마 버전 키는 `(dataset_id, schema_hash)`, 간선 키는 `(upstream, downstream, via)`.
  모든 쓰기는 이 키에 대한 upsert다.
- 이번 스캔이 읽은 스펙의 간선 중 관측되지 않은 것은 지운다(입력이 바뀐 스펙).
  스캔 루트 안에서 사라진 스펙의 간선도 지운다.

## 결과

- 같은 입력 재스캔은 행 수를 바꾸지 않는다(`test_rescanning_the_same_repo_adds_no_rows`).
- 스키마가 A → B → A로 돌아오면 A는 한 버전으로 합쳐진다(`first_seen_at`은 처음, `last_seen_at`은 마지막).
- URL 쿼리에 실린 토큰은 저장되지 않는다.
- 심볼릭 링크·마운트가 달라 문자열이 다른 같은 위치는 서로 다른 데이터셋이 된다.
