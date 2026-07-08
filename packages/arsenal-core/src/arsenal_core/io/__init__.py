"""Arrow/Parquet 허브 유틸 (예약 모듈).

도구 간 교환 포맷은 Arrow RecordBatch, 저장 포맷은 Parquet — 모든 도구가
같은 파일을 읽는다 (단일 스토리지 복사본, docs/07-cost-efficiency.md).

M1에서는 pyarrow 직접 사용으로 충분해 비워둔다. 공통 헬퍼(스키마 스냅샷
직렬화, 증분 판별 등)가 두 도구 이상에서 반복되는 시점에 채운다 (YAGNI).
"""
