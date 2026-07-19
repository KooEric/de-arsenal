# Arsenal (`de-arsenal`)

> 우산 CLI — Pugio(수집)와 Gladius(변환)를 한 진입점에서 부른다. 새 로직 없음,
> 위임만 한다(Unix 철학: 개별 도구는 독립적으로 완결됨).

`arsenal init <recipe>` → `arsenal run` → `arsenal query`, 세 명령으로
수집→검증→변환→쿼리를 재현하는 원클릭 데이터 스택. 배포명은 `de-arsenal`,
설치되는 명령어는 `arsenal`이다.

## 설치

```bash
uv tool install de-arsenal
# 또는
pipx install de-arsenal
```

## 사용 예

```bash
arsenal init csv-cleanup      # 레시피로 프로젝트 스캐폴드 (arsenal init --list로 목록)
arsenal run                   # arsenal.yaml 순서대로 수집→변환 일괄 실행. 재실행 = 재개
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

`arsenal.yaml` 매니페스트:

```yaml
name: my-project
pipelines:      # pugio로 순서대로 실행
  - collect.yaml
transforms:     # gladius로 순서대로 실행
  - transform.yaml
```

## 문서

- [docs/01-scope.md](../../docs/01-scope.md) — M4 통합·릴리스 범위
- [docs/08-limits.md](../../docs/08-limits.md) — 정직한 한계선
- [examples/quickstart](../../examples/quickstart) — 10분 collect → transform → query 데모
- [저장소 루트 README](../../README.md)
