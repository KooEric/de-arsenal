from pathlib import Path

import pytest
from _projects import MANIFEST, bare_project, write_project

from arsenal_core.errors import FatalError
from censor.context import load_context


def test_load_context_reads_raw_text_without_env_substitution(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "arsenal.yaml": MANIFEST,
            "collect.yaml": "name: x\nsource:\n  url: ${UNSET_VAR}\n",
            "transform.yaml": "name: t\n",
        },
    )
    ctx = load_context(tmp_path)
    assert ctx.manifest is not None
    assert ctx.pipelines[0].text.count("${UNSET_VAR}") == 1
    assert ctx.pipelines[0].raw == {"name": "x", "source": {"url": "${UNSET_VAR}"}}
    assert ctx.pipelines[0].name == "collect.yaml"
    assert ctx.transform_files[0].name == "transform.yaml"


def test_missing_manifest_is_a_fatal_error(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="arsenal.yaml"):
        load_context(tmp_path)


def test_invalid_manifest_is_recorded_not_raised(tmp_path: Path) -> None:
    write_project(tmp_path, {"arsenal.yaml": "pipelines: [a.yaml]\n"})
    ctx = load_context(tmp_path)
    assert ctx.manifest is None
    assert ctx.manifest_error is not None and "name" in ctx.manifest_error
    assert ctx.pipelines == ()


def test_unreadable_or_non_mapping_spec_has_no_raw(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    (tmp_path / "transform.yaml").unlink()
    ctx = load_context(tmp_path)
    assert ctx.pipelines[0].raw is None
    assert ctx.transform_files[0].raw is None
    assert ctx.transform_files[0].text == ""
    assert ctx.pipelines[0].read_error is None
    assert ctx.transform_files[0].read_error is not None


def test_undecodable_spec_records_read_error(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "collect.yaml").write_bytes(b"name: \xff\xfe\n")
    ctx = load_context(tmp_path)
    assert ctx.pipelines[0].read_error is not None
    assert ctx.pipelines[0].raw is None


def test_a_manifest_that_cannot_be_read_is_a_tool_error(tmp_path: Path) -> None:
    import pytest

    from arsenal_core.errors import FatalError

    (tmp_path / "arsenal.yaml").write_bytes(b"\xff\xfe\x00name")
    with pytest.raises(FatalError, match="cannot read"):
        load_context(tmp_path)
