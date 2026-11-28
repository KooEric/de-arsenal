# Vigil Plan 2: `cloudtrail-detect` Recipe and Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `arsenal init cloudtrail-detect` scaffolds a project that goes from synthetic CloudTrail events to six alerts with `arsenal run` → `vigil test` → `vigil run`, and the docs list Vigil.

**Architecture:** The recipe is plain files under `packages/arsenal/src/arsenal/recipes/cloudtrail-detect/`; `arsenal init` copies whatever is there, so no code in `arsenal` changes. Pugio loads `{"raw": "..."}` JSONL lines, one Gladius `sql` step extracts the `events` columns from `raw`, and five rule files read `events`. A committed script generates the sample.

**Tech Stack:** YAML specs (Pugio, Gladius), DuckDB SQL, pytest + `typer.testing.CliRunner`.

Spec: [2026-11-28-vigil-design.md](2026-11-28-vigil-design.md). Requires [Plan 1](2026-11-28-vigil-plan1-engine.md) merged or on the same branch.

## Global Constraints

- All constraints of Plan 1 apply (ruff 100 columns, pyright strict, exit codes, commit message format).
- `de-arsenal` must not depend on `de-vigil`. The recipe ships files only. Tests under `tests/e2e/` may import both, because the workspace installs both.
- The sample holds synthetic events only: account id `123456789012`, IPs from `192.0.2.0/24` and `198.51.100.0/24`. No real CloudTrail data is committed.
- No directory or file starting with `.` inside the recipe (package resources).
- `events` columns and their order are fixed by `vigil.events.EVENT_COLUMNS`: `event_id, event_time, source, service, action, actor_type, actor_id, src_ip, region, outcome, mfa_used, resource, raw`.
- Expected detections on the sample: `aws-root-console-login` 1, `aws-console-login-no-mfa` 1, `aws-cloudtrail-logging-stopped` 1, `aws-access-key-then-admin-policy` 1, `aws-s3-bucket-exposure-change` 2. Total 6.
- Run e2e tests with `uv run pytest tests/e2e/test_recipes.py -q --no-cov -k cloudtrail`.

## File Structure

```
scripts/gen_cloudtrail_sample.py                      writes the sample (deterministic)
packages/arsenal/src/arsenal/recipes/cloudtrail-detect/
  arsenal.yaml, collect.yaml, transform.yaml, README.md
  input/sample.jsonl                                  40 lines, 39 distinct events
  rules/aws-root-console-login.yaml
  rules/aws-console-login-no-mfa.yaml
  rules/aws-cloudtrail-logging-stopped.yaml
  rules/aws-access-key-then-admin-policy.yaml
  rules/aws-s3-bucket-exposure-change.yaml
tests/e2e/test_recipes.py                             + 2 tests
packages/vigil/README.md, docs/tools.md, README.md, README.ko.md
docs/reference/vigil-ocsf-mapping.md
```

---

### Task 1: Sample, collect and transform specs

**Files:**
- Create: `scripts/gen_cloudtrail_sample.py`
- Create: `packages/arsenal/src/arsenal/recipes/cloudtrail-detect/{arsenal.yaml,collect.yaml,transform.yaml,input/sample.jsonl}`
- Modify: `tests/e2e/test_recipes.py`

**Interfaces:**
- Produces: after `arsenal init cloudtrail-detect --dest D` and `arsenal run --project D`, `D/data/events/*.parquet` holds 39 rows with the `events` columns. Test helper `_cloudtrail_project(tmp_path: Path) -> Path` in `tests/e2e/test_recipes.py` returns such a `D`.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_recipes.py`:

```python
EVENT_COLUMNS = [
    "event_id", "event_time", "source", "service", "action", "actor_type", "actor_id",
    "src_ip", "region", "outcome", "mfa_used", "resource", "raw",
]  # fmt: skip


def _cloudtrail_project(tmp_path: Path) -> Path:
    dest = tmp_path / "ct-proj"
    init_result = runner.invoke(app, ["init", "cloudtrail-detect", "--dest", str(dest)])
    assert init_result.exit_code == 0, init_result.output
    run_result = runner.invoke(app, ["run", "--project", str(dest)])
    assert run_result.exit_code == 0, run_result.output
    return dest


def test_cloudtrail_detect_recipe_builds_events(tmp_path: Path) -> None:
    dest = _cloudtrail_project(tmp_path)
    con = duckdb.connect()
    try:
        events = f"read_parquet('{dest}/data/events/*.parquet')"
        columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {events}").fetchall()]
        total, distinct = con.execute(
            f"SELECT count(*), count(DISTINCT event_id) FROM {events}"
        ).fetchone() or (0, 0)
        failed_login = con.execute(
            f"SELECT outcome, mfa_used FROM {events} "
            "WHERE action = 'ConsoleLogin' AND outcome = 'failure'"
        ).fetchall()
    finally:
        con.close()
    assert columns == EVENT_COLUMNS
    assert (total, distinct) == (39, 39)  # the sample has 40 lines; one is a duplicate delivery
    assert failed_login == [("failure", False)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/e2e/test_recipes.py -q --no-cov -k cloudtrail`
Expected: FAIL, `unknown recipe: 'cloudtrail-detect'`

- [ ] **Step 3: Write the sample generator**

`scripts/gen_cloudtrail_sample.py`:

```python
"""Generate the synthetic CloudTrail sample for the cloudtrail-detect recipe.

Deterministic: running it again writes the same bytes. Account id and IPs are documentation
values (123456789012, 192.0.2.0/24, 198.51.100.0/24); nothing here comes from a real account.

    uv run python scripts/gen_cloudtrail_sample.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ACCOUNT = "123456789012"
OUT = Path("packages/arsenal/src/arsenal/recipes/cloudtrail-detect/input/sample.jsonl")
START = datetime(2026, 11, 2, 9, 0, 0)
OFFICE_IP = "192.0.2.10"
OUTSIDE_IP = "198.51.100.7"
ADMIN_POLICY = "arn:aws:iam::aws:policy/AdministratorAccess"
READ_POLICY = "arn:aws:iam::aws:policy/ReadOnlyAccess"
ROUTINE_EVENTS = 24
ROUTINE_GAP_MINUTES = 5


def user(name: str) -> dict[str, Any]:
    return {"type": "IAMUser", "arn": f"arn:aws:iam::{ACCOUNT}:user/{name}", "userName": name}


ROOT: dict[str, Any] = {"type": "Root", "arn": f"arn:aws:iam::{ACCOUNT}:root"}


class Sample:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add(
        self,
        minute: int,
        service: str,
        name: str,
        identity: dict[str, Any],
        ip: str = OFFICE_IP,
        **fields: Any,
    ) -> None:
        event: dict[str, Any] = {
            "eventVersion": "1.09",
            "eventID": f"sample-{len(self.events) + 1:04d}",
            "eventTime": (START + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "eventSource": f"{service}.amazonaws.com",
            "eventName": name,
            "awsRegion": "us-east-1",
            "sourceIPAddress": ip,
            "userIdentity": identity,
            "recipientAccountId": ACCOUNT,
        }
        event.update(fields)
        self.events.append(event)

    def login(self, minute: int, identity: dict[str, Any], ip: str, mfa: str, result: str) -> None:
        self.add(
            minute, "signin", "ConsoleLogin", identity, ip,
            additionalEventData={"MFAUsed": mfa},
            responseElements={"ConsoleLogin": result},
        )  # fmt: skip


def build() -> list[dict[str, Any]]:
    s = Sample()
    routine = [("ec2", "DescribeInstances"), ("s3", "ListBuckets"), ("sts", "AssumeRole")]
    for index in range(ROUTINE_EVENTS):
        service, name = routine[index % len(routine)]
        actor = user("alice" if index % 2 == 0 else "bob")
        s.add(index * ROUTINE_GAP_MINUTES, service, name, actor)

    # Ordinary activity that looks close to a detection but must not fire one.
    s.login(3, user("alice"), OFFICE_IP, "Yes", "Success")
    s.login(8, user("bob"), OFFICE_IP, "Yes", "Success")
    s.login(12, user("bob"), OFFICE_IP, "No", "Failure")
    s.add(
        20, "s3", "PutBucketAcl", user("bob"),
        requestParameters={"bucketName": "team-reports"}, errorCode="AccessDenied",
    )  # fmt: skip
    s.add(30, "iam", "CreateAccessKey", user("carol"), requestParameters={"userName": "carol"})
    s.add(
        210, "iam", "AttachUserPolicy", user("carol"),
        requestParameters={"userName": "carol", "policyArn": ADMIN_POLICY},
    )  # fmt: skip
    s.add(40, "iam", "CreateAccessKey", user("dave"), requestParameters={"userName": "dave"})
    s.add(
        45, "iam", "AttachUserPolicy", user("dave"),
        requestParameters={"userName": "dave", "policyArn": READ_POLICY},
    )  # fmt: skip

    # One event per rule, and two for the bucket rule.
    s.login(60, ROOT, OUTSIDE_IP, "No", "Success")
    s.login(70, user("mallory"), OUTSIDE_IP, "No", "Success")
    s.add(75, "cloudtrail", "StopLogging", user("mallory"), OUTSIDE_IP,
          requestParameters={"name": "org-trail"})  # fmt: skip
    s.add(80, "iam", "CreateAccessKey", user("mallory"), OUTSIDE_IP,
          requestParameters={"userName": "mallory"})  # fmt: skip
    s.add(95, "iam", "AttachUserPolicy", user("mallory"), OUTSIDE_IP,
          requestParameters={"userName": "mallory", "policyArn": ADMIN_POLICY})  # fmt: skip
    s.add(100, "s3", "PutBucketAcl", user("mallory"), OUTSIDE_IP,
          requestParameters={"bucketName": "customer-exports"})  # fmt: skip
    s.add(101, "s3", "DeletePublicAccessBlock", user("mallory"), OUTSIDE_IP,
          requestParameters={"bucketName": "customer-exports"})  # fmt: skip

    # CloudTrail can deliver an event twice; the transform removes the copy.
    return [*s.events, s.events[-1]]


def main() -> None:
    lines = [json.dumps({"raw": json.dumps(event, separators=(",", ":"))}) for event in build()]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} lines -> {OUT}")


if __name__ == "__main__":
    main()
```

Run: `uv run python scripts/gen_cloudtrail_sample.py`
Expected: `40 lines -> packages/arsenal/src/arsenal/recipes/cloudtrail-detect/input/sample.jsonl`

- [ ] **Step 4: Write the recipe specs**

`packages/arsenal/src/arsenal/recipes/cloudtrail-detect/arsenal.yaml`:

```yaml
name: cloudtrail-detect
pipelines:
  - collect.yaml
transforms:
  - transform.yaml
```

`packages/arsenal/src/arsenal/recipes/cloudtrail-detect/collect.yaml`:

```yaml
name: cloudtrail-detect
source:
  type: file
  path: ./input/*.jsonl   # <- `vigil unpack`으로 만든 JSONL을 이 디렉터리에 넣으세요
  format: jsonl
sink:
  type: parquet
  path: ./data/raw
```

`packages/arsenal/src/arsenal/recipes/cloudtrail-detect/transform.yaml`:

```yaml
name: cloudtrail-events
input: ./data/raw
steps:
  - sql: |
      SELECT
        json_extract_string(raw, '$.eventID') AS event_id,
        CAST(json_extract_string(raw, '$.eventTime') AS TIMESTAMP) AS event_time,
        'cloudtrail' AS source,
        json_extract_string(raw, '$.eventSource') AS service,
        json_extract_string(raw, '$.eventName') AS action,
        json_extract_string(raw, '$.userIdentity.type') AS actor_type,
        json_extract_string(raw, '$.userIdentity.arn') AS actor_id,
        json_extract_string(raw, '$.sourceIPAddress') AS src_ip,
        json_extract_string(raw, '$.awsRegion') AS region,
        CASE
          WHEN json_extract_string(raw, '$.errorCode') IS NOT NULL THEN 'failure'
          WHEN json_extract_string(raw, '$.responseElements.ConsoleLogin') = 'Failure' THEN 'failure'
          ELSE 'success'
        END AS outcome,
        CASE json_extract_string(raw, '$.additionalEventData.MFAUsed')
          WHEN 'Yes' THEN true
          WHEN 'No' THEN false
        END AS mfa_used,
        coalesce(
          json_extract_string(raw, '$.requestParameters.bucketName'),
          json_extract_string(raw, '$.requestParameters.userName'),
          json_extract_string(raw, '$.requestParameters.name')
        ) AS resource,
        raw
      FROM {input}
  - dedup: [event_id]
output: ./data/events
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/e2e/test_recipes.py -q --no-cov -k cloudtrail`
Expected: `1 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format scripts/gen_cloudtrail_sample.py tests/e2e/test_recipes.py
uv run ruff check scripts tests/e2e && uv run pyright scripts/gen_cloudtrail_sample.py
git add scripts/gen_cloudtrail_sample.py packages/arsenal/src/arsenal/recipes/cloudtrail-detect tests/e2e/test_recipes.py
git commit -m "feat(arsenal): cloudtrail-detect recipe builds the events table from a synthetic sample"
```

---

### Task 2: Five detection rules

**Files:**
- Create: `packages/arsenal/src/arsenal/recipes/cloudtrail-detect/rules/*.yaml` (five files)
- Modify: `tests/e2e/test_recipes.py`

**Interfaces:**
- Consumes: `_cloudtrail_project(tmp_path)` from Task 1; `vigil.cli.app` with `test`, `run`, `coverage` from Plan 1.
- Produces: five rule ids, exactly as listed in Global Constraints.

- [ ] **Step 1: Write the failing test**

Add to the imports of `tests/e2e/test_recipes.py`: `from vigil.cli import app as vigil_app`. Append:

```python
def test_cloudtrail_detect_rules_pass_and_detect_once(tmp_path: Path) -> None:
    dest = _cloudtrail_project(tmp_path)
    rules = ["--rules", str(dest / "rules")]
    run_args = ["run", *rules, "--events", str(dest / "data" / "events"),
                "--out", str(dest / "data" / "alerts")]  # fmt: skip

    tested = runner.invoke(vigil_app, ["test", *rules])
    assert tested.exit_code == 0, tested.output
    assert json.loads(tested.stdout) == {"rules": 5, "failed": {}}

    first = runner.invoke(vigil_app, run_args)
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["by_rule"] == {
        "aws-access-key-then-admin-policy": 1,
        "aws-cloudtrail-logging-stopped": 1,
        "aws-console-login-no-mfa": 1,
        "aws-root-console-login": 1,
        "aws-s3-bucket-exposure-change": 2,
    }

    second = runner.invoke(vigil_app, run_args)
    assert json.loads(second.stdout)["new_alerts"] == 0

    covered = runner.invoke(
        vigil_app, ["coverage", *rules, "--events", str(dest / "data" / "events"), "--json-only"]
    )
    report = json.loads(covered.stdout)
    assert report["unlisted"] == []
    assert "T1078.004" not in report["uncovered"]
    assert "T1110" in report["uncovered"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/e2e/test_recipes.py -q --no-cov -k cloudtrail_detect_rules`
Expected: FAIL with exit code 2, `rules directory not found`

- [ ] **Step 3: Write the five rule files**

`rules/aws-root-console-login.yaml`:

```yaml
id: aws-root-console-login
title: 루트 계정 콘솔 로그인
severity: high
attack: [T1078.004]
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE action = 'ConsoleLogin' AND actor_type = 'Root' AND outcome = 'success'
tests:
  match:
    - { action: ConsoleLogin, actor_type: Root, outcome: success }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success }
    - { action: ConsoleLogin, actor_type: Root, outcome: failure }
```

`rules/aws-console-login-no-mfa.yaml`:

```yaml
id: aws-console-login-no-mfa
title: MFA 없는 IAM 사용자 콘솔 로그인
severity: medium
attack: [T1078.004]
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE action = 'ConsoleLogin' AND actor_type = 'IAMUser'
    AND outcome = 'success' AND mfa_used = false
tests:
  match:
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success, mfa_used: false }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success, mfa_used: true }
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: failure, mfa_used: false }
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success }
```

`rules/aws-cloudtrail-logging-stopped.yaml`:

```yaml
id: aws-cloudtrail-logging-stopped
title: CloudTrail 로깅 중지·삭제·변경
severity: critical
attack: [T1685.002]
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE service = 'cloudtrail.amazonaws.com'
    AND action IN ('StopLogging', 'DeleteTrail', 'UpdateTrail')
    AND outcome = 'success'
tests:
  match:
    - { service: cloudtrail.amazonaws.com, action: StopLogging, outcome: success }
    - { service: cloudtrail.amazonaws.com, action: DeleteTrail, outcome: success }
  no_match:
    - { service: cloudtrail.amazonaws.com, action: DescribeTrails, outcome: success }
    - { service: cloudtrail.amazonaws.com, action: StopLogging, outcome: failure }
```

`rules/aws-access-key-then-admin-policy.yaml`:

```yaml
id: aws-access-key-then-admin-policy
title: 액세스 키 생성 뒤 1시간 안에 관리자 정책 연결
severity: high
attack: [T1098.001, T1098.003]
source: cloudtrail
query: |
  SELECT p.* FROM events AS p
  JOIN events AS k ON k.actor_id = p.actor_id
  WHERE k.action = 'CreateAccessKey' AND k.outcome = 'success'
    AND p.action = 'AttachUserPolicy' AND p.outcome = 'success'
    AND json_extract_string(p.raw, '$.requestParameters.policyArn')
        LIKE '%:policy/AdministratorAccess'
    AND p.event_time >= k.event_time
    AND p.event_time <= k.event_time + INTERVAL 1 HOUR
tests:
  match:
    - - { actor_id: u1, action: CreateAccessKey, outcome: success, event_time: "2026-01-01 10:00:00" }
      - actor_id: u1
        action: AttachUserPolicy
        outcome: success
        event_time: "2026-01-01 10:30:00"
        raw: '{"requestParameters":{"policyArn":"arn:aws:iam::aws:policy/AdministratorAccess"}}'
  no_match:
    - - { actor_id: u1, action: CreateAccessKey, outcome: success, event_time: "2026-01-01 10:00:00" }
      - actor_id: u1
        action: AttachUserPolicy
        outcome: success
        event_time: "2026-01-01 12:00:00"
        raw: '{"requestParameters":{"policyArn":"arn:aws:iam::aws:policy/AdministratorAccess"}}'
    - - { actor_id: u1, action: CreateAccessKey, outcome: success, event_time: "2026-01-01 10:00:00" }
      - actor_id: u1
        action: AttachUserPolicy
        outcome: success
        event_time: "2026-01-01 10:30:00"
        raw: '{"requestParameters":{"policyArn":"arn:aws:iam::aws:policy/ReadOnlyAccess"}}'
    - actor_id: u1
      action: AttachUserPolicy
      outcome: success
      raw: '{"requestParameters":{"policyArn":"arn:aws:iam::aws:policy/AdministratorAccess"}}'
```

`rules/aws-s3-bucket-exposure-change.yaml`:

```yaml
id: aws-s3-bucket-exposure-change
title: S3 버킷 공개 설정 변경
severity: medium
attack: [T1530]
source: cloudtrail
# 공개로 바뀌었는지는 확인하지 않는다. 공개 여부를 좌우하는 설정을 바꾼 호출을 알린다.
query: |
  SELECT * FROM events
  WHERE service = 's3.amazonaws.com'
    AND action IN ('PutBucketAcl', 'PutBucketPolicy', 'DeletePublicAccessBlock')
    AND outcome = 'success'
tests:
  match:
    - { service: s3.amazonaws.com, action: PutBucketAcl, outcome: success }
    - { service: s3.amazonaws.com, action: DeletePublicAccessBlock, outcome: success }
  no_match:
    - { service: s3.amazonaws.com, action: PutBucketAcl, outcome: failure }
    - { service: s3.amazonaws.com, action: ListBuckets, outcome: success }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/e2e/test_recipes.py -q --no-cov -k cloudtrail`
Expected: `2 passed`

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format tests/e2e/test_recipes.py && uv run ruff check tests/e2e
git add packages/arsenal/src/arsenal/recipes/cloudtrail-detect/rules tests/e2e/test_recipes.py
git commit -m "feat(arsenal): five CloudTrail detection rules in the cloudtrail-detect recipe"
```

---

### Task 3: Docs

**Files:**
- Create: `packages/arsenal/src/arsenal/recipes/cloudtrail-detect/README.md`, `docs/reference/vigil-ocsf-mapping.md`
- Modify: `packages/vigil/README.md`, `docs/tools.md`, `README.md`, `README.ko.md`

**Interfaces:**
- Consumes: the CLI and recipe exactly as built. Every command in these docs must be one that the e2e test or Plan 1's CLI tests run. `pip install de-vigil` and `uvx --from de-vigil` describe the state after the v0.8.0 release publishes the package; until then use `uv run vigil` inside this repository.

- [ ] **Step 1: Recipe README**

`packages/arsenal/src/arsenal/recipes/cloudtrail-detect/README.md`:

````markdown
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
````

- [ ] **Step 2: OCSF mapping reference**

`docs/reference/vigil-ocsf-mapping.md`:

```markdown
# Vigil `events` 컬럼과 OCSF 필드

Vigil의 `events`는 OCSF를 구현하지 않는다. 탐지 룰에 필요한 컬럼만 평평하게 둔다. 아래 표는 나중에
OCSF(API Activity, class 6003)로 옮길 때 어느 필드에 해당하는지 적은 것이다.

| `events` 컬럼 | 타입 | CloudTrail 원본 | OCSF 필드 |
|---|---|---|---|
| `event_id` | varchar | `eventID` | `metadata.uid` |
| `event_time` | timestamp (UTC) | `eventTime` | `time` |
| `source` | varchar | 상수 `cloudtrail` | `metadata.product.name` |
| `service` | varchar | `eventSource` | `api.service.name` |
| `action` | varchar | `eventName` | `api.operation` |
| `actor_type` | varchar | `userIdentity.type` | `actor.user.type` |
| `actor_id` | varchar | `userIdentity.arn` | `actor.user.uid` |
| `src_ip` | varchar | `sourceIPAddress` | `src_endpoint.ip` |
| `region` | varchar | `awsRegion` | `cloud.region` |
| `outcome` | varchar | `errorCode` 유무, 콘솔 로그인은 `responseElements.ConsoleLogin` | `status` |
| `mfa_used` | boolean | `additionalEventData.MFAUsed` | `actor.session.is_mfa` |
| `resource` | varchar | `requestParameters`의 `bucketName`, `userName`, `name` 중 처음 있는 값 | `resources[].name` |
| `raw` | varchar (JSON) | 이벤트 전체 | `raw_data` |

OCSF 필드명은 https://schema.ocsf.io/ 의 API Activity 클래스 기준이다.
```

Before committing, open https://schema.ocsf.io/ (class API Activity) and confirm each OCSF field name in the table exists there; correct any that differ, in this file and in spec §3.

- [ ] **Step 3: Package README**

Replace `packages/vigil/README.md` with:

````markdown
# Vigil (`de-vigil`)

> 야간 순찰대. 정규화된 보안 로그에 탐지 룰을 실행하고, 룰이 비어 있는 곳을 알려 준다. (experimental)

탐지 룰을 YAML 파일로 관리한다. 룰 하나는 메타데이터, `events`를 읽는 SELECT 한 개, 그리고 걸려야 하는
케이스와 걸리면 안 되는 케이스로 이루어진다. 테스트가 없는 룰은 `vigil test`를 통과하지 못한다.

## 설치

```bash
pip install de-vigil
```

## 사용 예

```bash
arsenal init cloudtrail-detect && arsenal run      # 합성 CloudTrail 샘플 → data/events
vigil test                                          # 룰마다 픽스처로 검증
vigil run --events data/events --out data/alerts    # 새 alert만 기록 (다시 실행하면 0건)
vigil coverage --events data/events                 # 룰이 없는 ATT&CK 기법, 컬럼 NULL 비율
vigil unpack ./cloudtrail-logs --out input/ct.jsonl # CloudTrail 원본(*.json.gz)을 JSONL로
```

## 룰 파일

```yaml
id: aws-root-console-login
title: 루트 계정 콘솔 로그인
severity: high            # low | medium | high | critical
attack: [T1078.004]       # ATT&CK 기법 ID
source: cloudtrail
query: |
  SELECT * FROM events
  WHERE action = 'ConsoleLogin' AND actor_type = 'Root' AND outcome = 'success'
tests:
  match:
    - { action: ConsoleLogin, actor_type: Root, outcome: success }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser, outcome: success }
```

- `query`는 SELECT 한 개이고 결과에 `event_id`가 있어야 한다. `events` 말고는 읽지 못한다.
- 케이스는 이벤트 하나이거나 이벤트 목록이다. 케이스마다 그 이벤트만 든 `events`에서 따로 실행한다.
- `events`의 컬럼은 [OCSF 대응표](../../docs/reference/vigil-ocsf-mapping.md)에 있다.

## 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 정상 |
| 1 | 룰 테스트 실패, 실행 중 룰 오류, 또는 `--fail-on` 심각도 이상의 새 alert |
| 2 | 사용법이나 입력 오류 (룰 파일 오류, 이벤트 파일 없음, 필수 컬럼 없음) |

## 한계

- 배치만 한다. `vigil run`은 `events`를 메모리 DuckDB 테이블로 올려 읽으므로, 한 번에 읽는 이벤트가
  그 기계에 들어가야 한다.
- 로그 소스는 CloudTrail 관리 이벤트 하나다.
- 커버리지의 기준 기법 목록은 직접 고른 20개이고 ATT&CK 전체가 아니다.
````

- [ ] **Step 4: Tool tables**

`docs/tools.md`: after the Censor row (line 19) add:

```markdown
| [Vigil](../packages/vigil/README.md) | 보안 로그에 탐지 룰을 실행하고 룰이 없는 곳을 볼 때 (experimental) | `arsenal init cloudtrail-detect` → `vigil test` → `vigil run` | 룰을 파일로 관리하고, 테스트 없는 룰은 통과하지 못한다 |
```

`README.md`: after the **Censor** row (line 138) add:

```markdown
| **Vigil** | Detection: runs rule files (YAML with one SELECT and fixture cases) over a normalized security-log table, records each alert once, and reports ATT&CK techniques that have no rule. Rules without tests fail `vigil test`. Ships with a CloudTrail recipe | `de-vigil` | experimental |
```

`README.ko.md`: after the **Censor** row (line 132) add:

```markdown
| **Vigil** | 탐지: 정규화된 보안 로그 테이블에 룰 파일(SELECT 한 개와 픽스처 케이스가 든 YAML)을 실행한다. alert는 한 번만 기록하고, 룰이 없는 ATT&CK 기법을 알려 준다. 테스트 없는 룰은 `vigil test`에서 실패. CloudTrail 레시피 포함 | `de-vigil` | experimental |
```

- [ ] **Step 5: Verify the documented commands and the full suite**

```bash
CHECK_DIR="$(mktemp -d)/proj" && uv run arsenal init cloudtrail-detect --dest "$CHECK_DIR"
cd "$CHECK_DIR" && uv run --project "$OLDPWD" arsenal run && uv run --project "$OLDPWD" vigil test \
  && uv run --project "$OLDPWD" vigil run --events data/events --out data/alerts \
  && uv run --project "$OLDPWD" vigil coverage --events data/events --json-only > /dev/null; cd "$OLDPWD"
```

Expected: `vigil test` prints five `ok` lines; `vigil run` prints `"new_alerts": 6`; every command exits 0.

Run: `uv run pytest -q`
Expected: all pass, coverage at or above 80%.

- [ ] **Step 6: Commit**

```bash
git add packages/arsenal/src/arsenal/recipes/cloudtrail-detect/README.md docs/reference/vigil-ocsf-mapping.md \
  packages/vigil/README.md docs/tools.md README.md README.ko.md docs/plans/2026-11-28-vigil-design.md
git commit -m "docs(vigil): package README, recipe README, OCSF mapping and tool tables"
```
