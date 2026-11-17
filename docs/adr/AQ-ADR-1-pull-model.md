# AQ-ADR-1 — Aquila는 pull 모델이다

- 상태: 승인 (2026-11-17, Phase 5 착수 결정)
- 관련: [Pilum·Aquila 계획 §1.3](../plans/2026-11-17-pilum-aquila-plan.md), `packages/aquila/src/aquila/scanners.py`

## 맥락

카탈로그에 정보를 넣는 방법은 둘이다. 각 도구가 실행할 때 카탈로그에 이벤트를 보내는
**push**, 카탈로그가 레포와 도구의 산출물을 읽어 오는 **pull**. push는 실시간이지만 모든
도구가 카탈로그에 의존하게 되고, 카탈로그가 깨지면 파이프라인이 깨진다. DE Arsenal의
첫 원칙은 "도구 하나만 집어 바로 쓴다"(락인 없음)다.

## 결정

`aquila scan`이 스펙 YAML(Pugio·Gladius·Ballista·Hasta·`arsenal.yaml`), 도구의 상태 DB,
로컬 Parquet footer를 **읽어서** 등록한다. 어떤 도구도 Aquila를 import하거나 호출하지 않는다.
스펙은 해당 도구를 import하지 않고 평범한 YAML로 파싱한다.

## 결과

- Aquila를 지우거나 `aquila.db`를 삭제해도 모든 파이프라인이 그대로 돈다.
  `packages/aquila/tests/test_install.py`가 양방향 의존 없음을 고정한다.
- `de-aquila`는 `arsenal-core`·`de-scorpio`·`pyarrow`·`pyyaml`·`typer`에만 의존한다.
- 카탈로그는 마지막 스캔 시점까지만 최신이다. 실시간 이벤트 버스는 v1 범위 밖이다.
- lineage는 **선언 기반**이다 — 스펙에 적힌 입력·출력만 간선이 되고, SQL이 스펙에 없는
  경로를 읽는 것은 보이지 않는다(→ [08-limits](../08-limits.md#aquila-카탈로그의-보장과-한계-05-preview)).
- 계획 초안은 Ballista `OutputProof`도 읽는다고 적었지만, v1은 proof를 읽지 않는다. proof는
  원격 staging에 있어 자격증명이 필요하고, 같은 간선을 스펙 선언이 이미 준다.
