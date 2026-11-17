from datetime import UTC, datetime

import pytest

from arsenal_core.execution import (
    ExecutorCapabilities,
    GuaranteeRequest,
    SinkCapabilities,
    canonical_hash,
    canonical_json,
    spec_revision,
)


def test_canonical_json_is_key_order_independent() -> None:
    first = {"a": [{"z": 1, "b": 2}], "when": datetime(2026, 8, 22, tzinfo=UTC)}
    second = {"when": datetime(2026, 8, 22, tzinfo=UTC), "a": [{"b": 2, "z": 1}]}

    assert canonical_json(first) == canonical_json(second)
    assert canonical_hash(first) == canonical_hash(second)
    assert spec_revision(first) == spec_revision(second)


def test_canonical_json_rejects_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_json({"when": datetime(2026, 8, 22)})
    with pytest.raises(TypeError, match="cannot canonicalize"):
        canonical_json({"object": object()})


def test_capability_preflight_contracts() -> None:
    request = GuaranteeRequest(result_exactly_once=True, atomic_publish=True, delete_apply=True)
    executor = ExecutorCapabilities(result_exactly_once=True, atomic_publish=True)
    assert executor.supports(request) is False
    assert executor.missing(request) == ("delete_apply",)

    sink = SinkCapabilities(idempotent_batch_write=True, atomic_publish=True, delete_apply=True)
    assert sink.supports(request) is True
    assert SinkCapabilities(atomic_publish=True).supports(request) is False
