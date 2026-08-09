import sys
import textwrap
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import DltSourceSpec
from pugio.sources.dlt import load_dlt_source


def test_dlt_source_batches_iterable_into_deterministic_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "my_dlt_source.py").write_text(
        textwrap.dedent(
            """
            def source(prefix='x'):
                return [
                    {'id': 1, 'value': prefix + '-a'},
                    {'id': 2, 'value': prefix + '-b'},
                    {'id': 3, 'value': prefix + '-c'},
                ]
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    monkeypatch.setitem(sys.modules, "dlt", object())
    source = load_dlt_source(
        DltSourceSpec(
            type="dlt",
            target="my_dlt_source:source",
            options={"prefix": "p"},
            batch_size=2,
        ),
        pipeline="p",
    )

    units = source.units()
    first = next(units)
    first_result = source.fetch(first)
    assert first.unit_key == "batch=0"
    assert first_result.batch is not None
    assert first_result.batch.num_rows == 2
    assert first_result.exhausted is False

    second = next(units)
    second_result = source.fetch(second)
    assert second.unit_key == "batch=1"
    assert second_result.batch is not None
    assert second_result.batch.to_pylist() == [{"id": 3, "value": "p-c"}]
    assert second_result.exhausted is True


def test_dlt_source_missing_optional_dependency_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "dlt", None)
    source = load_dlt_source(
        DltSourceSpec(type="dlt", target="missing:source"), pipeline="p"
    )
    with pytest.raises(FatalError, match=r"dlt.*extra"):
        next(source.units())
