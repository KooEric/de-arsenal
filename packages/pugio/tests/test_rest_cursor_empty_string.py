"""M2-H: Slack의 실제 계약은 `next_cursor: ""`(빈 문자열)로 "더 없음"을 신호한다 —
null이 아니다. 기존 rest.py는 `raw_cursor is not None`만 검사해 next_cursor를
결정했으므로, 빈 문자열은 `str("")`로 그대로 살아남아 `_cursor_exhausted`가 영원히
False로 남는다 (실전 API 검증 중 실제로 이 테스트가 무한루프에 빠져 발견됨 —
run_pipeline으로 구동했다면 API 쿼터를 태우며 영원히 멈추지 않았을 것이다).

이 테스트는 fetch()를 직접 두 번만 호출해 무한루프 위험 없이 회귀를 고정한다
(run_pipeline의 무한 for 루프에 태우지 않는다 — 수정 전 코드라면 이 테스트
자체는 유한 시간에 끝나되 마지막 assert에서 실패한다).
"""

import httpx
import respx

from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource

SLACK_LIKE_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/slack-like",
    pagination=PaginationSpec(
        mode="cursor",
        size_param="limit",
        size=2,
        cursor_param="cursor",
        cursor_path="response_metadata.next_cursor",
        record_path="messages",
    ),
)


@respx.mock
def test_empty_string_cursor_means_exhausted_not_a_valid_next_page() -> None:
    respx.get("https://api.test/slack-like").respond(
        json={"messages": [{"ts": "1"}], "response_metadata": {"next_cursor": ""}}
    )
    src = RestSource(SLACK_LIKE_SPEC, pipeline="p", client=httpx.Client())
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.next_cursor is None
    assert result.exhausted is True


@respx.mock
def test_numeric_zero_cursor_still_continues_not_treated_as_exhausted() -> None:
    """회귀 방지: 빈 문자열만 특별 취급한다 — 숫자 0 커서(falsy이지만 유효한 다음
    페이지 지시자)는 여전히 계속 진행으로 취급되어야 한다."""
    respx.get("https://api.test/slack-like").respond(
        json={"messages": [{"ts": "1"}], "response_metadata": {"next_cursor": 0}}
    )
    src = RestSource(SLACK_LIKE_SPEC, pipeline="p", client=httpx.Client())
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.next_cursor == "0"
    assert result.exhausted is False
