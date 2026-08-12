# 도구별 선택 가이드

DE Arsenal은 하나의 거대한 프레임워크가 아니라, Arrow/Parquet와 명시적인 상태
규약으로 연결되는 독립 도구 모음이다. 처음에는 `arsenal`로 시작하고, 요구가
뚜렷해질 때 필요한 도구만 직접 사용하면 된다.

| 도구 | 언제 쓰나 | 빠른 시작 | 핵심 장점 |
|---|---|---|---|
| [Arsenal](../packages/arsenal/README.md) | 수집부터 변환·쿼리까지 한 프로젝트로 실행할 때 | `arsenal init csv-cleanup` → `arsenal run` | 가장 짧은 진입 경로. 개별 도구를 묶되 로직을 중복하지 않는다. |
| [Pugio](../packages/pugio/README.md) | API·파일·DB에서 데이터를 수집·적재할 때 | `pugio run collect.yaml` | 중단 후 재개, 멱등 적재, 인증 갱신과 DLQ가 기본값이다. |
| [Gladius](../packages/gladius/README.md) | Parquet를 선언형으로 변환하거나 즉석 SQL을 실행할 때 | `gladius run transform.yaml` | YAML을 투명한 SQL로 컴파일하므로 성능과 디버깅 가능성을 함께 얻는다. |
| [Onager](../packages/onager/README.md) | 로컬 Parquet의 small-file 문제를 정리하거나 격리 백필할 때 | `onager compact ./data --dry-run` | 실제 교체 전 계획을 보며, 실패하면 기존 데이터셋을 복구한다. |
| [Scutum](../packages/scutum/README.md) | Arrow 스키마 계약이나 프로세스 간 파일 잠금이 필요할 때 | Python에서 `DataContract` 사용 | 벡터화 계약 검증과 bounded retry/backoff lock을 제공한다. |
| [Scorpio](../packages/scorpio/README.md) | 마지막 성공 시각을 기준으로 stale 상태를 판단할 때 | Python에서 `assess_freshness` 호출 | 외부 관측 플랫폼 없이 결정적인 freshness 판정을 만든다. |
| [Spatha](../packages/spatha/README.md) | 의존성·우선순위·실행 window를 반영한 작업 실행이 필요할 때 | Python에서 `Workflow.run_ready` 호출 | 안정된 DAG 순서와 signal-aware 실행, 선택적 cross-process lock을 제공한다. |
| [arsenal-core](../packages/arsenal-core/README.md) | 자체 도구를 만들거나 상태·재시도 규약을 재사용할 때 | Python에서 `StateStore` 사용 | 결정적 unit ID와 SQLite 상태 저장소로 재개·멱등 기반을 제공한다. |

## 1. Arsenal — 대부분의 사용자에게 시작점

`de-arsenal`을 설치하면 수집(Pugio), 변환(Gladius), 쿼리를 하나의 프로젝트
매니페스트로 실행할 수 있다.

```bash
uv tool install de-arsenal
arsenal init csv-cleanup
cd csv-cleanup
arsenal run
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

개별 단계를 이해하지 않아도 레시피로 시작할 수 있고, 필요해지면 같은 YAML을
`pugio`와 `gladius`로 직접 실행할 수 있다.

## 2. Pugio — 신뢰성 있는 수집과 적재

REST API, CSV/JSONL/Excel, SQLite/Postgres/MySQL을 읽어 Parquet, DuckDB, Postgres로
적재한다.

```bash
pugio run collect.yaml
pugio status collect.yaml
pugio dlq list collect.yaml
```

특히 API 페이지 수집처럼 중간 실패가 흔한 작업에 적합하다. 완료한 unit을 상태
DB에 기록하므로 같은 명령을 다시 실행해도 중복 적재보다 재개가 우선된다.

## 3. Gladius (`de-gladius`) — 선언형 변환과 즉석 쿼리

배포명은 `de-gladius`지만 import와 명령은 `gladius`다.

```bash
pip install de-gladius
gladius compile transform.yaml
gladius run transform.yaml
gladius query "SELECT count(*) FROM './data/clean/*.parquet'"
```

`filter`, `rename`, `cast`, `dedup`, `derive` 같은 단계가 DuckDB SQL로 컴파일된다.
생성 SQL을 먼저 출력할 수 있어, 선언형 인터페이스를 쓰면서도 실행 계획을 숨기지
않는 것이 강점이다.

## 4. Onager — 안전한 Parquet 정리와 백필

작은 Parquet 파일이 쌓여 쿼리 성능이 떨어질 때 먼저 계획을 확인하고 압축한다.

```bash
onager compact ./data/events --dry-run
onager compact ./data/events --target-bytes 134217728
```

Onager는 후보 파일만 선택하고, 새 디렉터리를 만든 뒤 성공했을 때만 기존
데이터셋과 교체한다. 백필도 별도 workspace에서 준비한 뒤 명시적으로 promote한다.

## 5. Scutum — 데이터 계약과 동시성 보호

Arrow 배치가 예상한 필드·타입·nullability를 만족하는지 확인한다.

```python
import pyarrow as pa
from scutum import DataContract

contract = DataContract(
    name="orders",
    columns=[
        {"name": "order_id", "type": "int64", "nullable": False},
        {"name": "amount", "type": "double", "nullable": False},
    ],
)
report = contract.check(pa.record_batch([[1], [12.5]], names=["order_id", "amount"]))
assert report.ok
```

계약 위반을 명시적인 보고서로 돌려주며, `FileLock`은 여러 실행기가 같은 파일을
동시에 바꾸지 않도록 제한된 재시도와 backoff를 제공한다.

## 6. Scorpio — freshness 판정

상태 DB나 외부 저장소에서 읽은 마지막 성공 시각으로 즉시 fresh/stale/unknown을
판정한다.

```python
from scorpio import assess_freshness

result = assess_freshness("2026-08-11T01:00:00+00:00", max_age_seconds=3600)
if result.alert:
    print(result.message)
```

판정에 필요한 입력이 작고 결정적이어서, 기존 알림 시스템에 쉽게 연결할 수 있다.

## 7. Spatha — 신호와 의존성을 아는 workflow

외부 scheduler가 신호와 완료 frontier를 관리하는 구조에서, 현재 실행 가능한 작업만
안정적인 우선순위 순서로 골라 실행한다.

```python
from spatha import TaskSpec, Workflow

workflow = Workflow(
    tasks=[
        TaskSpec(name="collect", signals=["source-ready"]),
        TaskSpec(name="transform", depends_on=["collect"], priority=10),
    ]
)
completed = workflow.run_ready(lambda task: print(f"run {task.name}"), signals={"source-ready"})
```

작업 그래프의 누락 의존성과 순환을 미리 검증하며, 선택적으로 파일 잠금을 써서
여러 프로세스가 같은 작업을 중복 실행하는 일을 막는다.

## 8. arsenal-core — 확장 도구를 위한 신뢰성 기반

일반 사용자는 직접 설치할 필요가 없지만, DE Arsenal에 새 source/sink/도구를
붙일 때 공통 상태 모델과 재시도 규약을 재사용할 수 있다.

```python
from pathlib import Path

from arsenal_core.state import StateStore, UnitSpec

unit = UnitSpec.create(pipeline="orders", source="api", unit_key="page=1", payload={})
store = StateStore(Path("./.arsenal/state.db"))
store.register(unit)
# fetch → write 뒤에만 완료 처리
store.mark_done(unit.unit_id, row_count=100, byte_count=8192)
store.close()
```

같은 입력에서 같은 unit ID를 만들고, 성공한 sink write 이후에만 완료 처리하는
순서를 통해 재실행 안정성을 구현한다.
