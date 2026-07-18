"""Generate `schemas/*.json` and `docs/reference/*-schema.md` from the Pydantic
spec models (`PipelineSpec`, `TransformSpec`).

Principle: docs generated FROM code — doc drift is a bug. Never hand-edit the
generated artifacts; edit the source models and re-run this script instead:

    uv run python scripts/gen_schema_docs.py            # write mode
    uv run python scripts/gen_schema_docs.py --check     # CI drift guard

Paths are resolved relative to the current working directory (so the script
can be pointed at a scratch checkout for tests) — run it from the repo root
for normal use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final, cast

SCRIPT_NAME: Final = "scripts/gen_schema_docs.py"

# (schema JSON path, markdown path, markdown title, source module hint)
_PIPELINE_JSON: Final = Path("schemas/pipeline.json")
_PIPELINE_MD: Final = Path("docs/reference/pipeline-schema.md")
_TRANSFORM_JSON: Final = Path("schemas/transform.json")
_TRANSFORM_MD: Final = Path("docs/reference/transform-schema.md")


def _import_pipeline_spec() -> type[Any]:
    from arsenal_core.spec.models import PipelineSpec

    return PipelineSpec


def _import_transform_spec() -> type[Any]:
    from gladius.spec import TransformSpec

    return TransformSpec


def _ref_name(ref: str) -> str:
    """`"#/$defs/SplitSpec"` -> `"SplitSpec"`."""
    return ref.rsplit("/", 1)[-1]


def render_type(node: dict[str, Any]) -> str:
    """Render a JSON-Schema field/type node as a short human-readable string.

    Handles the shapes pydantic v2 actually emits: `$ref` (link to the
    referenced model's section), `const` (single Literal), `enum` (Literal
    union), `anyOf`/`oneOf` (unions, including `X | None` optionals and
    discriminated unions), `array` (`items`), and plain scalar/object types.
    """
    if "$ref" in node:
        return _ref_name(node["$ref"])
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(v) for v in node["enum"])
    if "anyOf" in node or "oneOf" in node:
        variants = node.get("anyOf", node.get("oneOf", []))
        parts: list[str] = []
        nullable = False
        for variant in variants:
            if variant.get("type") == "null":
                nullable = True
                continue
            rendered = render_type(variant)
            if rendered not in parts:
                parts.append(rendered)
        result = " | ".join(parts) if parts else "any"
        if nullable:
            result += " | null"
        return result
    node_type = node.get("type")
    if node_type == "array":
        item_type = render_type(node.get("items", {}))
        if " | " in item_type:
            item_type = f"({item_type})"
        return f"array of {item_type}"
    if node_type == "object":
        additional: object = node.get("additionalProperties")
        if isinstance(additional, dict):
            return f"object<string, {render_type(cast(dict[str, Any], additional))}>"
        return "object"
    if isinstance(node_type, str):
        return node_type
    return "any"


_NO_DEFAULT: Final = object()


def _render_default(node: dict[str, Any]) -> str:
    default = node.get("default", _NO_DEFAULT)
    if default is _NO_DEFAULT:
        return ""
    return f"`{json.dumps(default)}`"


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _render_table(schema_def: dict[str, Any]) -> str:
    properties: dict[str, Any] = schema_def.get("properties", {})
    required: set[str] = set(schema_def.get("required", []))
    lines = [
        "| Field | Type | Required | Default | Description |",
        "|---|---|---|---|---|",
    ]
    for field_name, node in properties.items():
        type_str = _escape_cell(render_type(node))
        is_required = "Yes" if field_name in required else "No"
        default_str = _render_default(node)
        description = _escape_cell(node.get("description", ""))
        lines.append(
            f"| `{field_name}` | {type_str} | {is_required} | {default_str} | {description} |"
        )
    return "\n".join(lines)


def _render_model_section(name: str, schema_def: dict[str, Any], level: int = 2) -> str:
    heading = "#" * level
    parts = [f"{heading} {name}"]
    description = schema_def.get("description")
    if description:
        parts.append(description.strip())
    parts.append(_render_table(schema_def))
    return "\n\n".join(parts)


def render_markdown(schema: dict[str, Any], *, title: str, intro: str, source_path: str) -> str:
    """Render a full reference doc: intro + one section per model.

    The root model (everything in `schema` except `$defs`) is rendered first,
    then each `$defs` entry gets its own section, in the (alphabetical, stable)
    order pydantic emits them.
    """
    defs: dict[str, Any] = schema.get("$defs", {})
    root_name = schema.get("title", title)
    root_schema = {k: v for k, v in schema.items() if k != "$defs"}

    sections = [_render_model_section(root_name, root_schema)]
    for def_name, def_schema in defs.items():
        sections.append(_render_model_section(def_name, def_schema))

    header = (
        f"# {title}\n\n"
        f"> **AUTO-GENERATED — do not edit by hand.** This file is generated by "
        f"`{SCRIPT_NAME}` from the Pydantic models in `{source_path}`. To change it, edit "
        f"the source models and run `uv run python {SCRIPT_NAME}`.\n\n"
        f"{intro}\n"
    )
    return header + "\n\n" + "\n\n".join(sections) + "\n"


def _render_json_schema(model: type[Any]) -> str:
    return json.dumps(model.model_json_schema(), indent=2) + "\n"


def generate_artifacts() -> dict[Path, str]:
    """Build the full content of all 4 generated artifacts, keyed by the
    (CWD-relative) path they should be written to."""
    pipeline_spec = _import_pipeline_spec()
    transform_spec = _import_transform_spec()

    pipeline_schema = pipeline_spec.model_json_schema()
    transform_schema = transform_spec.model_json_schema()

    pipeline_source = "packages/arsenal-core/src/arsenal_core/spec/models.py"
    transform_source = "packages/gladius/src/gladius/spec.py"

    pipeline_md = render_markdown(
        pipeline_schema,
        title="Pipeline YAML Reference",
        intro=(
            "A pipeline YAML file declares one `PipelineSpec`: a name, a `source` "
            "(discriminated by `type`: `rest` | `file` | `database` | `python`), a `sink` "
            "(discriminated by `type`: `parquet` | `duckdb` | `postgres`), and an optional "
            "`validate` block. Every table below is generated from the corresponding "
            "Pydantic model's JSON Schema."
        ),
        source_path=pipeline_source,
    )
    transform_md = render_markdown(
        transform_schema,
        title="Transform YAML Reference",
        intro=(
            "A transform YAML file declares one `TransformSpec`: a name, an `input` and "
            "`output` Parquet path, an optional `map` of derived columns, and an ordered "
            "list of `steps` (`filter` | `rename` | `cast` | `select` | `dedup` | `derive`). "
            "Every table below is generated from the corresponding Pydantic model's JSON "
            "Schema."
        ),
        source_path=transform_source,
    )

    return {
        _PIPELINE_JSON: _render_json_schema(pipeline_spec),
        _PIPELINE_MD: pipeline_md,
        _TRANSFORM_JSON: _render_json_schema(transform_spec),
        _TRANSFORM_MD: transform_md,
    }


def _write_artifacts(artifacts: dict[Path, str]) -> None:
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _check_artifacts(artifacts: dict[Path, str]) -> list[Path]:
    stale: list[Path] = []
    for path, content in artifacts.items():
        if not path.exists() or path.read_text() != content:
            stale.append(path)
    return stale


def main(argv: list[str]) -> int:
    check_mode = "--check" in argv
    artifacts = generate_artifacts()

    if check_mode:
        stale = _check_artifacts(artifacts)
        if stale:
            print("gen_schema_docs.py --check: stale generated artifacts found:")
            for path in stale:
                print(f"  - {path}")
            print(f"Run `uv run python {SCRIPT_NAME}` and commit the result.")
            return 1
        print("gen_schema_docs.py --check: all generated artifacts are up to date.")
        return 0

    _write_artifacts(artifacts)
    for path in artifacts:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
