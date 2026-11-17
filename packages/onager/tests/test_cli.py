from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from onager.cli import app
from typer.testing import CliRunner


def write_part(path: Path, value: int) -> None:
    pq.write_table(pa.table({"id": [value]}), path)  # pyright: ignore[reportUnknownMemberType]


def test_compact_subcommand_is_exposed(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    write_part(dataset / "a.parquet", 1)
    write_part(dataset / "b.parquet", 2)

    result = CliRunner().invoke(
        app,
        ["compact", str(dataset), "--target-bytes", "10000", "--min-files", "2", "--dry-run"],
    )

    assert result.exit_code == 0, result.stdout
    assert "selected=2" in result.stdout
    assert "compacted=False" in result.stdout
