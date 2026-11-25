# Censor (`de-censor`)

> 감찰관. 파이프라인을 조사해 등급을 매기고, 고칠 곳을 근거와 함께 남긴다.

```bash
censor audit .                 # grade the project in the current directory (quick tier)
censor report                  # grade history
censor compare <run_a> <run_b> # checks whose status changed
arsenal eval .                 # same as `censor audit .`
```

Results go to stdout as JSON, a summary to stderr, and history and a Markdown report to
`.arsenal/censor/`. Exit code 0 means no failed check, 1 means at least one, 2 means
Censor could not run. Design: `docs/plans/2026-11-25-censor-design.md`.
