# AQ-ADR-4 — Aquila가 기준 원천, Augur는 읽기 전용 소비자

- 상태: 승인 (2026-11-17, 사용자 승인 결정 2번)
- 관련: `packages/augur/src/augur/aquila_source.py`

## 맥락

Augur는 text-to-SQL을 위해 자기 `Catalog`(테이블 경로·컬럼·샘플 값)를 만든다. Aquila도
데이터셋 목록을 갖는다. 둘이 따로 관리되면 진실 원천이 두 개가 된다. 반대로 Augur가
Aquila에 의존하면 Augur 단독 설치가 깨진다.

## 결정

- 데이터셋의 기준 원천은 Aquila다. Augur의 `Catalog`는 그 위의 검색용 뷰다.
- `augur index --aquila PATH`, 또는 소스 파일 없이 실행하면 `.arsenal/aquila.db`를 읽는다.
  읽기는 sqlite 읽기 전용으로 하며 `aquila` 패키지를 import하지 않는다.
- 소스 YAML을 주면 예전과 똑같이 `build_catalog()`를 쓴다. Augur는 `aquila.db`에 쓰지 않는다.
- 읽는 대상은 활성(`deprecated` 아님)·로컬 Parquet·SQL 식별자 이름의 데이터셋이다.

## 결과

- `de-augur`는 `de-aquila`에 의존하지 않는다.
- 같은 데이터에 대해 두 경로의 eval 결과가 case별로 같다
  (`test_eval_results_are_identical_with_and_without_aquila`, 34 case).
- deprecated 데이터셋은 Augur 검색에서 빠진다 — 사용자가 낡은 테이블로 질문을 받지 않는다.
