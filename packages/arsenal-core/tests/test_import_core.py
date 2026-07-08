"""스켈레톤 배선 검증 — 공개 API가 전부 import 가능하고 이름이 살아 있어야 한다."""

import arsenal_core
from arsenal_core import errors, identity, retry
from arsenal_core.spec import PipelineSpec, load_pipeline
from arsenal_core.state import StateStore, UnitMetrics, UnitRecord, UnitSpec


def test_version() -> None:
    assert arsenal_core.__version__


def test_public_api_is_wired() -> None:
    assert issubclass(errors.AuthExpiredError, errors.RetryableError)
    assert callable(identity.unit_id)
    assert callable(retry.with_retry)
    assert callable(load_pipeline)
    wired = (PipelineSpec, StateStore, UnitMetrics, UnitRecord, UnitSpec)
    assert all(cls is not None for cls in wired)
