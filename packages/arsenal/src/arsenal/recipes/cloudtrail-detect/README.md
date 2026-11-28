# cloudtrail-detect 레시피

AWS CloudTrail 이벤트를 공통 `events` 테이블로 정리하고, 탐지 룰 5개를 실행한다.
스캐폴드 직후에도 `input/sample.jsonl`(합성 이벤트 40줄, 중복 1줄)로 바로 실행된다.
룰 실행에는 Vigil이 필요하다: `pip install de-vigil`.

1. 정리: `arsenal run` (`data/events`에 39행)
2. 룰 검증: `vigil test`
3. 탐지: `vigil run --events data/events --out data/alerts` (샘플이면 새 alert 6건, 다시 실행하면 0건)
4. 확인: `arsenal query "SELECT rule_id, event_id, actor_id FROM './data/alerts/*.parquet'"`
5. 빈 곳 확인: `vigil coverage --events data/events`

## 내 로그로

CloudTrail이 S3에 남긴 파일(`*.json`, `*.json.gz`)을 한 디렉터리에 내려받은 뒤:

```bash
rm -rf data .arsenal input/sample.jsonl
vigil unpack ./downloaded-logs --out input/cloudtrail.jsonl
arsenal run
vigil run --events data/events --out data/alerts
```

첫 줄은 샘플이 만든 결과를 지운다. `arsenal run`이 샘플을 `data/raw`에 이미 쌓았고, 변환은 `data/raw`
전체를 읽으므로 `input/sample.jsonl`만 지우면 샘플 39행과 alert 6건이 내 로그와 섞여 남는다.
`.arsenal/`에는 이미 읽은 입력 파일의 기록이 있다. 이것까지 지우지 않으면 `arsenal run`이 새 파일을
읽지 않고 건너뛰어 `data/raw`가 비고 변환이 실패한다. `rm -rf`는 이 프로젝트 디렉터리 안에서만 실행한다.

`vigil unpack`은 이벤트 하나를 `{"raw": "..."}` 한 줄로 쓴다. 로그 파일 검증을 켠 trail이 함께 남기는
`CloudTrail-Digest/` 아래 파일(`Records`가 없다)은 건너뛴다. 컬럼을 꺼내는 SQL은 `transform.yaml`에
그대로 있으니, 필요한 필드가 더 있으면 거기에 추가한다. `events`의 컬럼 13개는 지우거나 이름을 바꾸면 안 된다.

S3 룰(`aws-s3-bucket-exposure-change`)이 보는 이벤트 이름은 `PutBucketAcl`, `PutBucketPolicy`,
`DeleteBucketPublicAccessBlock`, `DeleteAccountPublicAccessBlock`, `PutBucketPublicAccessBlock`,
`PutAccountPublicAccessBlock`이 전부다. 공개로 바뀌었는지는 확인하지 않는다.

## 룰 추가

`rules/`에 YAML 파일을 하나 더 만든다. `tests.match`와 `tests.no_match`가 각각 하나 이상 없으면
`vigil test`가 실패한다. 여러 이벤트를 함께 봐야 하는 룰은 케이스를 이벤트 목록으로 적는다
(`aws-access-key-then-admin-policy.yaml` 참고).

## PR마다 룰 검증

아래 내용을 `.github/workflows/detections.yml`로 저장한다.

```yaml
name: detections
on:
  pull_request:
    paths: ["rules/**"]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uvx --from de-vigil vigil test
```
