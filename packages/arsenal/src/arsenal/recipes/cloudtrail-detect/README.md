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

CloudTrail이 S3에 남긴 파일(`*.json.gz`)을 한 디렉터리에 내려받은 뒤:

```bash
rm input/sample.jsonl
vigil unpack ./downloaded-logs --out input/cloudtrail.jsonl
arsenal run
```

`vigil unpack`은 이벤트 하나를 `{"raw": "..."}` 한 줄로 쓴다. 컬럼을 꺼내는 SQL은 `transform.yaml`에
그대로 있으니, 필요한 필드가 더 있으면 거기에 추가한다. `events`의 컬럼 13개는 지우거나 이름을 바꾸면 안 된다.

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
