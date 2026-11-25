"""Write small DE Arsenal projects into tmp dirs. Maturity builders live here (Task 9)."""

from __future__ import annotations

import json as _json
from pathlib import Path

MANIFEST = """\
name: demo
pipelines:
  - collect.yaml
transforms:
  - transform.yaml
"""

COLLECT_BARE = """\
name: demo-collect
source:
  type: file
  path: ./input/*.csv
  format: csv
sink:
  type: parquet
  path: ./data/raw
"""

TRANSFORM = """\
name: clean-rows
input: ./data/raw
steps:
  - dedup: [id]
output: ./data/clean
"""


def write_project(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def bare_project(root: Path) -> Path:
    return write_project(
        root,
        {"arsenal.yaml": MANIFEST, "collect.yaml": COLLECT_BARE, "transform.yaml": TRANSFORM},
    )


GUARDED_COLLECT = (
    COLLECT_BARE
    + """\
validate:
  rules:
    - { field: id, not_null: true, unique: true }
  on_violation: split
"""
)
ROBUST_COLLECT = (
    GUARDED_COLLECT
    + """\
schema_drift: block
contract:
  columns:
    - { name: id, type: int64, nullable: false }
"""
)
CLAUDE_MD = "Run `arsenal check` before and `arsenal run` after edits.\n"
DAG = "DAG('daily', schedule='@daily')\nBashOperator(task_id='run', bash_command='arsenal run')\n"
SETTINGS = _json.dumps(
    {
        "enabledPlugins": {"de-arsenal@de-arsenal": True},
        "permissions": {"deny": ["Bash(rm -rf .arsenal*)"]},
    }
)
SECRET_HOOK = "# scan *.yaml and *.yml for secrets\n"


def minimal_project(root: Path) -> Path:
    return bare_project(root)


def functional_project(root: Path) -> Path:
    bare_project(root)
    return write_project(root, {"collect.yaml": GUARDED_COLLECT, "README.md": "# demo\n"})


def robust_project(root: Path) -> Path:
    functional_project(root)
    return write_project(
        root, {"collect.yaml": ROBUST_COLLECT, "CLAUDE.md": CLAUDE_MD, "dags/daily.py": DAG}
    )


def production_project(root: Path) -> Path:
    robust_project(root)
    return write_project(
        root, {".claude/settings.json": SETTINGS, ".claude/hooks/secret-scan.sh": SECRET_HOOK}
    )
