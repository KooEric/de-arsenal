"""Validate workspace package metadata before a release."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages"


def package_metadata() -> list[tuple[str, str, Path]]:
    metadata: list[tuple[str, str, Path]] = []
    for path in sorted(PACKAGE_ROOT.glob("*/pyproject.toml")):
        with path.open("rb") as file:
            project = tomllib.load(file)["project"]
        metadata.append((project["name"], project["version"], path))
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Optional release tag, such as v0.2.0")
    args = parser.parse_args()

    metadata = package_metadata()
    versions = {version for _, version, _ in metadata}
    errors: list[str] = []
    if len(versions) != 1:
        errors.append("all workspace packages must share one version")
    if not any(name == "de-gladius" for name, _, _ in metadata):
        errors.append("the transform package must use the de-gladius distribution name")

    if args.tag:
        tag_version = args.tag.removeprefix("refs/tags/").removeprefix("v")
        if versions != {tag_version}:
            errors.append(f"package version {versions} does not match release tag v{tag_version}")

    if errors:
        for error in errors:
            print(f"release metadata error: {error}", file=sys.stderr)
        return 1

    version = next(iter(versions))
    print(f"release metadata: {len(metadata)} packages at v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
