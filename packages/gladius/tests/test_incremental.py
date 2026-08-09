from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from gladius.engine import run_transform
from gladius.spec import TransformSpec


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.Table.from_pylist(rows), path
    )


def read_output(path: Path) -> list[dict[str, object]]:
    tables: list[pa.Table] = []
    for item in sorted(path.glob("*.parquet")):
        tables.append(
            pq.read_table(item)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        )
    if not tables:
        return []
    rows = pa.concat_tables(tables).to_pylist()  # pyright: ignore[reportUnknownMemberType]
    return cast(list[dict[str, object]], rows)


def make_spec(tmp_path: Path, mode: str, key: list[str] | None = None) -> TransformSpec:
    incremental: dict[str, object] = {"mode": mode}
    if key is not None:
        incremental["key"] = key
    return TransformSpec.model_validate(
        {
            "name": f"{mode}-transform",
            "input": str(tmp_path / "input"),
            "output": str(tmp_path / "output"),
            "state_dir": str(tmp_path / "state"),
            "incremental": incremental,
            "steps": [{"select": ["id", "value"]}],
        }
    )


def test_by_unit_appends_only_new_input_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_rows(input_dir / "a.parquet", [{"id": 1, "value": "a"}])
    spec = make_spec(tmp_path, "by_unit")

    run_transform(spec)
    assert read_output(tmp_path / "output") == [{"id": 1, "value": "a"}]
    assert len(list((tmp_path / "output").glob("*.parquet"))) == 1

    run_transform(spec)
    assert len(list((tmp_path / "output").glob("*.parquet"))) == 1

    write_rows(input_dir / "b.parquet", [{"id": 2, "value": "b"}])
    run_transform(spec)
    assert sorted(read_output(tmp_path / "output"), key=lambda row: int(cast(int, row["id"]))) == [
        {"id": 1, "value": "a"},
        {"id": 2, "value": "b"},
    ]
    assert len(list((tmp_path / "output").glob("*.parquet"))) == 2


def test_changed_input_triggers_safe_full_recompute(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source = input_dir / "a.parquet"
    write_rows(source, [{"id": 1, "value": "old"}])
    spec = make_spec(tmp_path, "by_unit")
    run_transform(spec)

    write_rows(source, [{"id": 1, "value": "new"}])
    run_transform(spec)

    assert read_output(tmp_path / "output") == [{"id": 1, "value": "new"}]
    assert len(list((tmp_path / "output").glob("*.parquet"))) == 1


def test_by_key_upserts_new_rows_over_existing_output(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    write_rows(input_dir / "a.parquet", [{"id": 1, "value": "old"}])
    spec = make_spec(tmp_path, "by_key", ["id"])
    run_transform(spec)

    write_rows(
        input_dir / "b.parquet",
        [{"id": 1, "value": "new"}, {"id": 2, "value": "second"}],
    )
    run_transform(spec)

    assert sorted(read_output(tmp_path / "output"), key=lambda row: int(cast(int, row["id"]))) == [
        {"id": 1, "value": "new"},
        {"id": 2, "value": "second"},
    ]
