# csv-cleanup 레시피

로컬 CSV 파일들을 모아 중복 제거 + 타입 캐스팅된 테이블로 만든다.

1. 바꿀 것: `input/` 디렉터리에 정리할 CSV를 넣고, `transform.yaml`의 dedup 키를 데이터에 맞게 조정
2. 실행: `arsenal run`
3. 확인: `arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 20"`
