# 실전 API 5종 — 표현 가능성 검증 결과 (M2-H)

M2의 페이지네이션(offset/page/cursor/link) · 인증(static/oauth2) · rate_limit ·
encoding 설계가 탁상 설계인지 실전인지 판정하는 문서. 각 API를 실제 YAML로
작성하고(`examples/real-world/`), respx로 실 응답 형태를 재현하는 계약 테스트로
검증했다(`packages/pugio/tests/test_real_world_apis.py`). 실 계정/네트워크는
쓰지 않았다 — 아래 결과는 모두 이 저장소의 respx 목이 실제 API 문서/응답 스키마를
따른다는 전제 위에 있다.

**요약**: 5종 중 4종(GitHub/공공데이터포털/Notion/Slack)은 REST 소스로 완전히
표현 가능했다. Stripe 1종만 표현 불가 지점이 있어 Python 탈출구가 필요하다.
검증 과정에서 스펙 설계의 실제 버그 2건을 발견해 고쳤다(아래 "검증 중 발견한
버그" 참고) — 이게 이 배치의 요점이다: 종이 위 설계는 그럴듯해도 실 API 응답의
사소한 관례 차이(빈 문자열 vs null, url에 박힌 쿼리 파라미터)가 실제로 깨뜨린다.

## 표

| API | 표현 가능? | 검증 포인트 | 비고 |
|---|---|---|---|
| [GitHub Issues](../../examples/real-world/github.yaml) | 완전 표현 가능 | `mode: link` (RFC 5988 Link 헤더, 복수 rel), `auth: static_token` | — |
| [Stripe Charges](../../examples/real-world/stripe.yaml) | **부분** — envelope만 표현 가능, 커서는 불가 | `record_path: data` | 커서(`starting_after`)는 Python 탈출구 필요 (아래 상세) |
| [공공데이터포털](../../examples/real-world/data-go-kr.yaml) | 완전 표현 가능 | `encoding: euc-kr`, `mode: page`, API 키 쿼리 파라미터 | 키를 담을 전용 필드는 없음 — url에 직접 삽입 (아래 상세) |
| [Notion Search](../../examples/real-world/notion.yaml) | 완전 표현 가능 (M2-H에서 `method`/`body` 필드 추가) | `mode: cursor`, `rate_limit: 3rps`, `POST` + JSON body | — |
| [Slack conversations.history](../../examples/real-world/slack.yaml) | 완전 표현 가능 | `mode: cursor`(중첩 dot-path), 429 `Retry-After` | 빈 문자열 커서 버그를 이 API가 드러냄 (아래 상세) |

## API별 상세

### GitHub — `mode: link` + static_token

GitHub의 `Link` 헤더는 `rel="next"` 외에 `rel="last"` 등 복수 항목을 콤마로
나열한다. 기존 정규식(`_LINK_NEXT`, M1 스코프 고정: GitHub 스타일 `rel="next"`만
처리)이 복수 rel 중 정확히 `next`만 골라내는지 계약 테스트로 재확인했다 — 통과.
static_token auth는 매 페이지 요청에 `Authorization: Bearer <token>`을 실었다.

**결론**: 표현 가능, 회귀 없음.

### Stripe — envelope는 표현 가능, 커서는 표현 불가 (Python 탈출구)

Stripe Charges API는 `{"object": "list", "data": [...], "has_more": bool}` 형태의
envelope를 반환한다. `record_path: data`로 배열을 뽑는 부분은 REST 소스로 완전히
표현된다 (`test_stripe_envelope_record_path_is_expressible`).

문제는 다음 페이지 커서다. Stripe의 `starting_after` 파라미터에 넣을 값은
**응답의 최상위 필드가 아니라 이번 페이지 `data` 배열의 "마지막 원소의 id"**다.
`PaginationSpec.cursor_path`와 그 구현인 `_dig()`(`packages/pugio/src/pugio/sources/rest.py`)
는 dict의 dot-path 순회만 지원하고, 순회 도중 리스트를 만나면 즉시 `None`을
반환한다(`_dig`의 `if not isinstance(cur, dict): return None`) — 그래서
`"data.id"` 같은 경로로 배열 원소의 필드까지 내려갈 수 없다
(`test_stripe_last_item_id_cursor_is_not_expressible_via_dot_path`가 이를
직접 재현: `_dig(envelope, "data")`는 배열을 돌려주지만 `_dig(envelope, "data.id")`는
`None`이다). 게다가 Stripe는 `has_more`(불리언, 최상위 필드)로 종료를 신호하는데,
`PaginationSpec`의 종료 시맨틱은 "cursor_path가 가리키는 값이 None이면 종료"
하나뿐이라 불리언 종료 신호와도 어긋난다.

**판정**: 스펙을 억지로 늘리지 않는다(리스트 인덱싱/"마지막 원소" 문법을
`cursor_path`에 넣는 건 이 하나의 API를 위해 일반 스펙을 복잡하게 만드는
과잉설계다). 대신 `type: python` 탈출구로 페이지네이션 자체를 구현한다 —
`examples/real-world/stripe.yaml`이 이 패턴을 보여주고,
`test_stripe_escape_hatch_source_completes_pagination`이 최소 구현으로 실제
2페이지 완주까지 검증한다. **결론: REST 소스로 표현 불가, Python 탈출구가
의도된 경로.**

### 공공데이터포털(data.go.kr) — euc-kr + page + 쿼리 파라미터 인증

`encoding: euc-kr`은 기존 M2-C로 이미 지원되며, euc-kr로 인코딩된 바이트가
한글이 깨지지 않고 디코드되는지 계약 테스트로 재확인했다
(`test_data_go_kr_euckr_decoding_page_mode_and_query_param_key`). `mode: page`도
그대로 표현된다.

API 키(`serviceKey`)는 이 API의 관례상 쿼리 파라미터로 전달한다.
`RestSourceSpec`에는 "임의의 정적 쿼리 파라미터"를 위한 전용 필드가 없다 —
`AuthSpec`은 헤더 기반(static_token/oauth2)만 지원한다. 우회로는 `url` 자체에
`?serviceKey=${DATA_GO_KR_KEY}`를 박아 넣는 것뿐이다
(`examples/real-world/data-go-kr.yaml`). 이 우회로 자체는 동작하지만 두 가지
비용이 있다: (1) 로더의 `${VAR}` 치환이 URL 문자열 단계에서 일어나므로 시크릿이
YAML 텍스트에는 없지만 `PipelineSpec.source.url`(및 그걸 로그/에러 메시지에
싣는 모든 경로)에는 평문으로 남는다. (2) **이 검증 중 실제 버그를 하나
발견했다**: `RestSource.fetch()`가 페이지네이션 파라미터를 항상
`httpx.Client.get(url, params=X)`로 넘기는데, httpx는 `params`가 주어지면 url에
이미 있던 쿼리 문자열을 병합이 아니라 **통째로 대체**한다 — 즉 `url`에 심어둔
`serviceKey`가 실제 요청에서는 조용히 사라졌다. 고쳤다(`_merge_static_query()`,
`packages/pugio/src/pugio/sources/rest.py`) — url의 기존 쿼리를 파싱해
페이지네이션 파라미터와 병합한 뒤 `params=`로 넘긴다. 회귀 테스트:
`packages/pugio/tests/test_rest_static_query.py`.

**결론**: 표현 가능(우회 경유). 전용 "정적 쿼리 파라미터" 필드가 없다는 설계
제약은 남아 있고, 이번엔 그 우회로 자체가 조용히 깨져 있던 버그를 고쳤다.

### Notion Search — cursor + rate_limit + POST (method/body 필드 추가)

Notion의 검색은 `POST /v1/search`이고, 페이지네이션 파라미터(`page_size`,
`start_cursor`)를 쿼리가 아니라 **JSON 바디**로 보낸다. 이 API가 정확히
`RestSourceSpec`에 `method`/`body` 필드가 필요한지 판정하는 대상이었다 —
**필요하다고 판정**했고 M2-H에서 추가했다:

```python
method: Literal["GET", "POST"] = "GET"
body: dict[str, Any] | None = None
```

`RestSource.fetch()`는 `method == "POST"`일 때 `body`(정적)와
`_request_params(unit)`(페이지네이션 동적 파라미터)를 병합해 JSON으로 보낸다 —
GET이 기본값이라 기존 스펙/테스트는 전혀 영향받지 않는다
(`packages/pugio/tests/test_rest_method.py`). `rate_limit: 3rps`는 기존 M2-C
토큰 버킷 그대로 배선된다 — 결정론적 타이밍 검증은 이미
`test_rest_ratelimit.py`가 커버하므로, 여기서는 스펙 배선(요청이 실제로 POST로
나가고 body가 올바르게 병합되는지)만 재확인했다.

**결론**: 표현 가능 (M2-H에서 `method`/`body` 필드 추가로 표현 가능해짐).

### Slack conversations.history — 중첩 커서 + 429, 그리고 빈 문자열 커서 버그

`cursor_path: response_metadata.next_cursor`처럼 중첩 dot-path는 기존 `_dig()`로
바로 표현된다. 429 + `Retry-After` 헤더는 기존 M2-C rate limiter의
`penalize()` 경로가 API 이름과 무관하게 처리하므로 계약 형태(정수 초 헤더)만
재확인했다.

**이 검증 중 두 번째 실제 버그를 발견했다**: Slack의 진짜 관례는 "더 이상 페이지
없음"을 `null`이 아니라 **빈 문자열(`next_cursor: ""`)**로 신호한다. 기존 코드는
`next_cursor = str(raw_cursor) if raw_cursor is not None else None`였는데,
빈 문자열은 `None`이 아니므로 그대로 `str("")` = `""`로 "다음 커서가 있다"고
오판했다 — `_cursor_exhausted`가 영원히 `False`로 남아 **무한루프**가 된다
(실전 API 검증 테스트를 처음 작성해 돌렸을 때 실제로 60초 이상 CPU를 100% 태우며
멈추지 않는 것으로 발견됨 — `run_pipeline`으로 실행했다면 API 쿼터를 소진하며
영원히 끝나지 않았을 것이다). 고쳤다: `next_cursor = str(raw_cursor) if
raw_cursor not in (None, "") else None` — 빈 문자열만 `None`과 동일하게
취급하고, falsy이지만 유효한 숫자 0 커서(`str(0) == "0"`, non-empty)는 여전히
계속 진행으로 취급된다. 회귀 테스트:
`packages/pugio/tests/test_rest_cursor_empty_string.py` (빈 문자열 케이스와
숫자 0 케이스 둘 다 고정).

**결론**: 표현 가능. 발견한 버그는 이 문서가 아니라 `RestSource` 자체를
고쳐 모든 cursor-mode API에 적용된다(Slack 전용 우회가 아니다).

## 검증 중 발견한 버그 (요약, 둘 다 수정 완료)

이번 배치의 실질적 산출물 — "5개 YAML을 썼다"가 아니라 "썼더니 진짜 버그가
나왔다":

1. **정적 쿼리 파라미터 드롭**(`packages/pugio/src/pugio/sources/rest.py`
   `_merge_static_query`): `url`에 심어둔 쿼리(예: API 키)가 페이지네이션
   파라미터에 의해 조용히 사라짐. data-go-kr/slack 검증 중 발견.
2. **빈 문자열 커서 무한루프**(같은 파일, `fetch()`의 `next_cursor` 계산):
   `next_cursor: ""`를 "다음 페이지 있음"으로 오판해 종료 조건이 never-true가
   됨. Slack 검증 중 발견 — respx 계약 테스트가 실제로 무한루프에 빠지는 것으로
   드러났다.

두 버그 모두 회귀 테스트로 고정되어 있다
(`test_rest_static_query.py`, `test_rest_cursor_empty_string.py`).

## 아직 없는 것 (설계 제약, 버그 아님)

- **정적 쿼리 파라미터 전용 필드 없음**: API 키를 쿼리로 받는 API는 `url`에
  직접 삽입해야 한다(위 공공데이터포털/Slack 참고). 헤더 기반 인증
  (`AuthSpec`)만 1급 시민이다.
- **불리언 기반 페이지네이션 종료 없음**: `has_more: false` 같은 최상위 불리언
  종료 신호는 표현할 수 없다 — `cursor_path`가 가리키는 값이 `None`(또는 이제
  빈 문자열)이어야 종료로 인식한다(Stripe gap의 근본 원인).
- **리스트 인덱싱 없음**: `cursor_path`/`record_path`는 dict dot-path만
  순회한다 — "배열의 마지막 원소" 같은 시맨틱은 Python 탈출구가 필요하다
  (Stripe).
