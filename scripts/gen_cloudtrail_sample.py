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
OUT = (
    Path(__file__).resolve().parents[1]
    / "packages/arsenal/src/arsenal/recipes/cloudtrail-detect/input/sample.jsonl"
)
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

    # One event for each of the first three rules, a key and a policy event for the key rule,
    # and two events for the bucket rule.
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
    s.add(101, "s3", "DeleteBucketPublicAccessBlock", user("mallory"), OUTSIDE_IP,
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
