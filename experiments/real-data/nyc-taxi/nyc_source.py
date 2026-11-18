"""Parquet row group = one Pugio unit. Experiment-only escape-hatch source."""

import glob
from collections.abc import Iterator
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class ParquetRowGroupSource:
    def __init__(self, options: dict[str, Any], *, pipeline: str) -> None:
        self._pattern = str(options["path"])
        self._pipeline = pipeline

    def units(self) -> Iterator[UnitSpec]:
        for path in sorted(glob.glob(self._pattern)):
            for rg in range(pq.ParquetFile(path).num_row_groups):
                yield UnitSpec.create(
                    pipeline=self._pipeline,
                    source=self._pattern,
                    unit_key=f"{path.rsplit('/', 1)[-1]}#rg{rg}",
                    payload={"path": path, "rg": rg},
                )

    def fetch(self, unit: UnitSpec) -> FetchResult:
        table: pa.Table = pq.ParquetFile(unit.payload["path"]).read_row_group(unit.payload["rg"])
        return FetchResult(batch=table.combine_chunks().to_batches()[0], exhausted=False)
