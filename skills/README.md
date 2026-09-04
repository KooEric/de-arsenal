# DE Arsenal skills for Claude Code

Five [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) that
let an AI agent operate DE Arsenal end to end: build a pipeline, transform the data,
gate its quality, recover from failures, and hand it to a cloud skill for scheduling.
They contain no LLM code — the tool stays free and model-neutral; the skill is
documentation the agent reads.

| Skill | Use when |
|---|---|
| [`arsenal-pipeline`](arsenal-pipeline/SKILL.md) | collect data from an API, file, or DB into Parquet/DuckDB/Postgres |
| [`arsenal-transform`](arsenal-transform/SKILL.md) | clean, cast, dedup, derive, query Parquet with declarative steps or SQL |
| [`arsenal-quality`](arsenal-quality/SKILL.md) | validation rules, data contracts, schema drift, DLQ, freshness |
| [`arsenal-operate`](arsenal-operate/SKILL.md) | a run failed or was interrupted; backfill; compaction |
| [`arsenal-deploy`](arsenal-deploy/SKILL.md) | schedule it in GitHub Actions / AWS / GCP / Cloudflare together with that provider's skill |

## Install

Claude Code, as a plugin (all five skills at once):

```
/plugin marketplace add KooEric/de-arsenal
/plugin install de-arsenal@de-arsenal
```

Claude Code, by hand: copy or symlink any skill folder into `~/.claude/skills/` (global)
or a project's `.claude/skills/` (project-local).

```bash
git clone https://github.com/KooEric/de-arsenal
ln -s "$PWD/de-arsenal/skills/arsenal-pipeline" ~/.claude/skills/arsenal-pipeline
```

Claude.ai: zip a skill folder and upload it under Settings → Capabilities → Skills.

Other agents (Cursor, Codex, …): the SKILL.md files are plain Markdown; point the agent at
the folder or paste the relevant file into its context.

The tool itself is separate: `uv tool install de-arsenal`.

## Conventions

Each `SKILL.md` has the same sections: **Scope · Inspect first · Safety · Verify** plus
an **Honest limits** block where the tool does not yet do what people will ask for. The
schema tables under `references/` are generated from the Pydantic models by
`scripts/gen_schema_docs.py`; do not hand-edit them (CI checks drift).

Skills are read by models of very different strength. Keep them concrete: exact commands,
exact field names, what to run before writing YAML, what to check after.
