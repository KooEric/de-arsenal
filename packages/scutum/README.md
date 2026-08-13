# Scutum

Arrow data contract와 파일 lock retry/backoff를 제공한다. Pugio의
`PipelineSpec.contract`가 계약 검증과 DLQ 정책에 연결된다.

PyPI 배포명은 `de-scutum`이며 Python import 경로는 `scutum`을 유지한다.

```bash
pip install de-scutum
```

```python
from scutum import DataContract
```
