import textwrap
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import PythonSourceSpec
from pugio.sources.python_source import load_python_source


def test_loads_user_source_and_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "my_source.py").write_text(
        textwrap.dedent(
            """
            import pyarrow as pa
            from arsenal_core.state import UnitSpec
            from pugio.sources.base import FetchResult

            class MySource:
                def __init__(self, options, *, pipeline):
                    self._p = pipeline
                def units(self):
                    yield UnitSpec.create(
                        pipeline=self._p, source="my", unit_key="only", payload={}
                    )
                def fetch(self, unit):
                    return FetchResult(pa.RecordBatch.from_pylist([{"id": 1}]), exhausted=True)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    src = load_python_source(
        PythonSourceSpec(type="python", target="my_source:MySource"), pipeline="p"
    )
    units = list(src.units())
    assert units[0].unit_key == "only"
    result = src.fetch(units[0])
    assert result.exhausted is True
    assert result.batch is not None and result.batch.num_rows == 1


def test_missing_protocol_method_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "half_source.py").write_text(
        textwrap.dedent(
            """
            class HalfSource:
                def __init__(self, options, *, pipeline):
                    pass
                def units(self):
                    return iter([])
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(FatalError, match="does not implement Source protocol: fetch"):
        load_python_source(
            PythonSourceSpec(type="python", target="half_source:HalfSource"), pipeline="p"
        )


def test_import_error_is_fatal_with_hint() -> None:
    with pytest.raises(FatalError, match="nope_this_module_does_not_exist:Foo"):
        load_python_source(
            PythonSourceSpec(type="python", target="nope_this_module_does_not_exist:Foo"),
            pipeline="p",
        )


def test_missing_class_attribute_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "empty_module.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(FatalError, match="cannot load python source"):
        load_python_source(
            PythonSourceSpec(type="python", target="empty_module:NoSuchClass"), pipeline="p"
        )


def test_malformed_target_without_colon_is_fatal() -> None:
    with pytest.raises(FatalError, match="expected 'module:ClassName'"):
        load_python_source(PythonSourceSpec(type="python", target="just_a_module"), pipeline="p")
