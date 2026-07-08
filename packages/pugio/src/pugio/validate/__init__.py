"""검증 게이트 (M2) — 적재 직전 스키마·null·범위·중복 검사.

계획 (docs/01-scope.md M2 Task 2.7~2.8):
- 선언형 규칙: not_null / unique / range / schema (YAML validate.rules)
- Arrow 수준 벡터화 검사 — 행 단위 루프 없음
- 위반 정책: block(중단) / quarantine(DLQ 격리 후 계속) / warn(경고만)
- DLQ: .arsenal/dlq/에 위반 unit parquet + 사유 저장, `pugio dlq list/retry`

P1에서 Scutum 독립 패키지로 분리한다. M1에서는 자리만 예약.
"""
