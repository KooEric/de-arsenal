"""Write small DE Arsenal projects into tmp dirs. Maturity builders live here (Task 9)."""

from __future__ import annotations

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
