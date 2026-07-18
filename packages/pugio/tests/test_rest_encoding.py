"""M2-C: REST 소스 응답 인코딩 처리.

resp.json()이 아니라 resp.content.decode(spec.encoding)으로 raw body를 디코드한다.
httpx는 charset이 명시되지 않으면 기본 UTF-8로 추정하므로, euc-kr 등 비UTF-8
응답은 spec.encoding을 명시적으로 지정하지 않으면 깨진다.
"""

import httpx
import pytest
import respx

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource

OFFSET_PAGINATION = PaginationSpec(mode="offset", param="offset", size_param="limit", size=2)


@respx.mock
def test_euc_kr_response_decoded() -> None:
    """euc-kr로 인코딩된 응답 바디를 spec.encoding에 맞춰 정확히 디코드한다."""
    body = '[{"이름": "김철수"}]'.encode("euc-kr")
    respx.get("https://api.test/euckr").respond(
        content=body, headers={"content-type": "application/json"}
    )
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/euckr",
        pagination=OFFSET_PAGINATION,
        encoding="euc-kr",
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.to_pylist()[0]["이름"] == "김철수"


@respx.mock
def test_bad_encoding_name_is_fatal() -> None:
    """알 수 없는 인코딩 이름(LookupError)은 FatalError로 변환한다."""
    respx.get("https://api.test/bad-encoding").respond(json=[{"id": 1}])
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/bad-encoding",
        pagination=OFFSET_PAGINATION,
        encoding="not-a-real-codec",
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    with pytest.raises(FatalError):
        src.fetch(next(iter(src.units())))


@respx.mock
def test_non_json_body_is_fatal() -> None:
    """200이지만 본문이 JSON이 아니면(json.JSONDecodeError) FatalError로 분류한다
    (재시도해도 나아지지 않는 응답이므로 with_retry 재시도 대상이 아니다)."""
    respx.get("https://api.test/not-json").respond(
        content=b"<html>not json</html>", headers={"content-type": "application/json"}
    )
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/not-json",
        pagination=OFFSET_PAGINATION,
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    with pytest.raises(FatalError):
        src.fetch(next(iter(src.units())))


@respx.mock
def test_utf8_default_still_works() -> None:
    """encoding을 지정하지 않으면 기본 utf-8 경로가 그대로 동작한다."""
    respx.get("https://api.test/utf8").respond(json=[{"id": 1}, {"id": 2}])
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/utf8",
        pagination=OFFSET_PAGINATION,
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.num_rows == 2
