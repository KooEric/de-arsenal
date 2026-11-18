# 실데이터 실험 — 재현용 스펙

결과와 해석은 [docs/11-real-data-experiments.md](../../docs/11-real-data-experiments.md)에 있다.
이 폴더에는 실험에 쓴 스펙과 커스텀 소스만 둔다. 원천 데이터와 산출물(`raw/`, `drops/`,
`data/`, `.arsenal/`)은 커밋하지 않는다. 각 폴더 안에서 실행한다.

비밀값은 어디에도 적지 않는다. GitHub 실험은 `GITHUB_TOKEN`, Augur는 `OPENAI_API_KEY`
환경변수로만 받는다.

| 폴더 | 데이터 | 준비 |
|---|---|---|
| `github/` | GitHub REST API (이 레포의 commits, issues) | `export GITHUB_TOKEN=...` |
| `gitlab/` | GitLab API (gitlab-runner 이슈 9,554건) | 없음 (공개 API) |
| `airports/` | [datasets/airport-codes](https://github.com/datasets/airport-codes) CSV 86,343행 | 아래 스크립트로 `drops/` 생성 |
| `nyc-taxi/` | [NYC TLC Yellow Taxi](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) Parquet 5개월, 1,970만 행 | 아래 스크립트로 `raw/` 생성 |
| `hasta/` | 로컬 cursor API (`examples/hasta/fake_orders_api.py`) | API를 먼저 띄운다 |

## 데이터 받기

```bash
# airports: 대륙별 7개 파일로 나누고, EU 파일 3행의 고도를 999999로 오염시킨다
cd airports && mkdir -p raw drops
curl -sLo raw/airports.csv https://raw.githubusercontent.com/datasets/airport-codes/main/data/airport-codes.csv
python - <<'PY'
import duckdb
for c in ["NA", "AS", "EU", "SA", "AF", "OC", "AN"]:
    duckdb.sql(f"COPY (SELECT * FROM read_csv('raw/airports.csv', all_varchar=true) "
               f"WHERE continent='{c}') TO 'drops/airports_{c}.csv' (HEADER)")
duckdb.sql("COPY (SELECT * REPLACE (CASE WHEN row_number() OVER () IN (10,20,30) THEN '999999' "
           "ELSE elevation_ft END AS elevation_ft) FROM read_csv('drops/airports_EU.csv', "
           "all_varchar=true)) TO 'drops/eu.csv' (HEADER)")
PY
mv drops/eu.csv drops/airports_EU.csv

# nyc-taxi
cd nyc-taxi && mkdir -p raw
for m in 2019-01 2022-01 2024-01 2024-02 2024-03; do
  curl -sfo raw/yellow_tripdata_$m.parquet \
    https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_$m.parquet
done
```

## 실행

```bash
# 1. GitHub: 도중에 kill -9 → lease 만료(약 60초) 후 재실행 → 중복 검사
pugio run github/gh-commits.yaml
gladius query "SELECT count(*), count(DISTINCT sha) FROM './data/gh_commits/*.parquet'"

# 2. GitLab: link 페이지네이션, 결과를 API의 x-total 헤더와 비교
pugio run gitlab/gl-issues.yaml

# 3. airports: 검증 게이트 → 원천 수정 → 재실행 → 변환
pugio run airports/airports-gate.yaml && pugio dlq list airports/airports-gate.yaml
gladius run airports/airports-transform.yaml

# 4. NYC: 실행 간 스키마 변화, 대량 적재, 검증 게이트, 다중 스키마 변환
cd nyc-taxi && export PYTHONPATH=$PWD
pugio run drift-2022.yaml && pugio run drift-2024.yaml     # 두 번째가 block된다
pugio run bulk.yaml                                         # 도중에 kill -9 해 본다
pugio run gate.yaml && pugio dlq list gate.yaml
gladius run clean.yaml

# 5. Hasta: API를 띄우고 hasta run을 여러 번 kill -9, 마지막에 Ctrl-C
python ../../examples/hasta/fake_orders_api.py --port 8799 --initial 20000 --every 0.2 --batch 50 &
hasta run hasta/orders-stream.yaml

# 6. Augur: OpenAI 호환 API (예: Gemini 무료 티어)
python ../../packages/augur/eval/make_fixture.py ./data
augur index ../../packages/augur/eval/sources.yaml
export OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
export OPENAI_API_KEY=...      # 레포·셸 히스토리에 남기지 않는다
augur eval ../../packages/augur/eval/cases.yaml --provider openai --model gemini-3.1-flash-lite
```
