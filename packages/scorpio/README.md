# Scorpio

운영 관측 패키지. Arsenal 상태 DB의 마지막 완료 시각을 기준으로 freshness를
판정하고 stale alert를 만든다.

```python
from scorpio import assess_freshness

status = assess_freshness(last_completed_at, max_age_seconds=3600)
```
