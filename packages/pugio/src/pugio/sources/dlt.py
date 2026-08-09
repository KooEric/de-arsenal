"""Optional dlt source adapter with deterministic Arrow batches."""

import importlib
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Any, cast

import pyarrow as pa

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import DltSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class DltSource:
    """Adapt a user-owned ``@dlt.source`` function to Pugio's Source contract.

    dlt remains responsible for source authentication/pagination. Pugio adds
    deterministic batch units and Arrow output around the source iterator.
    """

    def __init__(self, spec: DltSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._current: pa.RecordBatch | None = None
        self._current_index: int | None = None
        self._exhausted = False
        self._lookahead: deque[dict[str, Any]] = deque()
        self._records = self._build_records()

    def _build_records(self) -> Iterator[dict[str, Any]]:
        try:
            importlib.import_module("dlt")
        except ImportError as e:
            raise FatalError(
                "dlt source requires the 'dlt' extra: pip install pugio[dlt]"
            ) from e
        module_name, separator, function_name = self._spec.target.partition(":")
        if not separator:
            raise FatalError(
                f"invalid dlt source target {self._spec.target!r}: "
                "expected 'module:source_function'"
            )
        try:
            module = importlib.import_module(module_name)
            target: Any = getattr(module, function_name)
            if not callable(target):
                raise FatalError(f"dlt source target is not callable: {self._spec.target}")
            source = target(**self._spec.options)
            yield from self._iter_source(source)
        except FatalError:
            raise
        except Exception as e:
            raise FatalError(f"cannot load dlt source {self._spec.target!r}: {e}") from e

    def _iter_source(self, source: Any) -> Iterator[dict[str, Any]]:
        resources = getattr(source, "resources", None)
        if resources is not None:
            selected = self._spec.resources
            if selected:
                try:
                    values = [resources[name] for name in selected]
                except (KeyError, TypeError) as e:
                    raise FatalError(f"dlt resource not found: {e}") from e
            else:
                selected_resources = getattr(resources, "selected", None)
                values = (
                    list(selected_resources.values())
                    if selected_resources
                    else list(resources.values())
                )
            for resource in values:
                yield from self._iter_items(resource)
            return
        yield from self._iter_items(source)

    def _iter_items(self, value: Any) -> Iterator[dict[str, Any]]:
        if isinstance(value, (pa.Table, pa.RecordBatch)):
            rows = value.to_pylist()
            yield from (cast(dict[str, Any], row) for row in rows)
            return
        if isinstance(value, dict):
            yield cast(dict[str, Any], value)
            return
        if hasattr(value, "model_dump") and callable(value.model_dump):
            yield cast(dict[str, Any], value.model_dump())
            return
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for item in cast(Iterable[Any], value):
                yield from self._iter_items(item)
            return
        raise FatalError(f"dlt source yielded unsupported item: {type(value).__name__}")

    def _next_batch(self) -> pa.RecordBatch | None:
        rows: list[dict[str, Any]] = []
        while self._lookahead and len(rows) < self._spec.batch_size:
            rows.append(self._lookahead.popleft())
        while len(rows) < self._spec.batch_size:
            try:
                rows.append(next(self._records))
            except StopIteration:
                self._exhausted = True
                break
        if not rows:
            return None
        if not self._exhausted and len(rows) == self._spec.batch_size:
            try:
                self._lookahead.append(next(self._records))
                self._exhausted = False
            except StopIteration:
                self._exhausted = True
        return pa.RecordBatch.from_pylist(rows)

    def units(self) -> Iterator[UnitSpec]:
        index = 0
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            self._current = batch
            self._current_index = index
            try:
                yield UnitSpec.create(
                    pipeline=self._pipeline,
                    source=self._spec.target,
                    unit_key=f"batch={index}",
                    payload={"batch": index},
                )
            finally:
                self._current = None
                self._current_index = None
            index += 1

    def fetch(self, unit: UnitSpec) -> FetchResult:
        if self._current is None or self._current_index != unit.payload.get("batch"):
            raise FatalError(f"dlt batch is not available: {unit.unit_key}")
        return FetchResult(batch=self._current, exhausted=self._exhausted)

    def close(self) -> None:
        close = getattr(self._records, "close", None)
        if callable(close):
            close()


def load_dlt_source(spec: DltSourceSpec, *, pipeline: str) -> DltSource:
    return DltSource(spec, pipeline=pipeline)
