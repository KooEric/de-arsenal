# csv-cleanup 레시피

`input/`의 CSV 파일들을 모아 중복 제거 + 타입 캐스팅된 테이블로 만든다.
스캐폴드 직후에도 `input/sample.csv`(4행, 중복 1행)로 바로 실행된다.

1. 실행: `arsenal run`
2. 확인: `arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 20"` (샘플이면 3행)
3. 내 데이터로: `input/sample.csv`를 지우고 CSV를 넣은 뒤 `transform.yaml`의 dedup 키를 맞춘다

우편번호처럼 앞자리 0이 있는 컬럼(`02139`)은 문자열로 유지된다. 타입을 직접 정하려면
`collect.yaml`의 `source.column_types`를 쓴다 (예: `{ zip: string, amount: float64 }`).
