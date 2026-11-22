---
name: de-arsenal
description: Build and operate a single-machine DE Arsenal data project from an ordinary request. Use for collecting files or APIs, cleaning data, answering questions about results, or keeping a pipeline up to date. 한국어 요청 예시로 “이 CSV 정리해 줘”, “거래처별 매출 보여 줘”, “매일 아침 갱신해 줘”가 있다.
---

# DE Arsenal

Use the user's language. Work in their project directory. Resolve bundled references,
scripts and deployment examples relative to this SKILL.md. The user's project
and the installed skill can be in different directories.

For a recurring local file dataset, use the preview, publish and status workflow in
[start](references/start.md). It reuses the rules agreed on the first publication.

For a first trial without user data, create a new project with
`arsenal init csv-cleanup --dest <new-directory>`. Inside that directory run
`arsenal check`, then `arsenal run`. Count the raw and clean Parquet rows with
`arsenal query`, rerun, and check both counts again. Show what happened and
where the result lives. For real inputs, point the collect spec at the user's
files before running so the bundled sample cannot enter their results.

For a new or edited pipeline, follow [start](references/start.md).
For a specific task, read the relevant guide:

- [Collect files, APIs or database tables](references/ingest.md)
- [Clean and aggregate](references/transform.md)
- [Answer a question or export results](references/ask.md)
- [Check data quality](references/trust.md)
- [Schedule and monitor](references/operate.md)
- [Diagnose a failure or unexpected count](references/doctor.md)

Use a shell with DE Arsenal 0.6.7 or later and access to the inputs. In a hosted chat,
use uploaded files available to that session. If execution is unavailable,
deliver the project files and the exact local verification command, and identify
which checks still need to run. For recurring jobs use the user's persistent
machine, with the inputs, outputs and checkpoint directory retained between runs.
