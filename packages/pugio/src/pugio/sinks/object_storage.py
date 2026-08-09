"""Parquet sink for S3/GCS URLs through DuckDB's httpfs extension."""

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.state import UnitSpec

OBJECT_STORE_SCHEMES = frozenset({"s3", "gcs", "gs"})


def _quote_str_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def is_object_store_path(path: str) -> bool:
    scheme, separator, _ = path.partition("://")
    return bool(separator) and scheme.lower() in OBJECT_STORE_SCHEMES


class ObjectStorageParquetSink:
    """Write deterministic unit files directly to object storage.

    DuckDB's multipart uploader handles the remote transfer. The deterministic
    object key provides idempotency; remote object replacement is not claimed to
    be a cross-provider atomic transaction.
    """

    def __init__(self, path: str) -> None:
        if not is_object_store_path(path):
            raise ValueError(f"unsupported object storage path: {path!r}")
        self._path = path.rstrip("/")

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        target = f"{self._path}/{unit.unit_id}.parquet"
        con: duckdb.DuckDBPyConnection | None = None
        try:
            con = duckdb.connect()
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
            con.register("_arsenal_batch", pa.Table.from_batches([batch]))
            con.execute(
                f"COPY _arsenal_batch TO {_quote_str_literal(target)} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        except (duckdb.IOException, duckdb.TransactionException) as e:
            raise RetryableError(
                f"object storage write failed for unit {unit.unit_id}: {e}"
            ) from e
        except duckdb.Error as e:
            raise FatalError(f"object storage write failed: {e}") from e
        finally:
            if con is not None:
                con.close()
