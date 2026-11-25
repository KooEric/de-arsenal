"""Markdown report: grade -> dimensions -> what to fix."""

from __future__ import annotations

from typing import Final, Literal

from censor.model import AuditReport, Status

Lang = Literal["en", "ko"]
_LABELS: Final[dict[Lang, dict[str, str]]] = {
    "en": {
        "grade": "Grade",
        "score": "score",
        "capped": "capped at C by a failed blocker",
        "dimension": "Dimension",
        "dim_score": "Score (0-10)",
        "fix_heading": "To fix",
        "nothing": "Nothing to fix.",
        "none": "no grade: no check applied",
    },
    "ko": {
        "grade": "등급",
        "score": "점수",
        "capped": "차단 체크 실패로 C 상한 적용",
        "dimension": "차원",
        "dim_score": "점수 (0-10)",
        "fix_heading": "고칠 항목",
        "nothing": "고칠 항목이 없습니다.",
        "none": "등급 없음: 적용된 체크가 없음",
    },
}
_ORDER: Final = {Status.FAIL: 0, Status.ERROR: 1, Status.WARN: 2}


def render_markdown(report: AuditReport, lang: Lang = "en") -> str:
    t = _LABELS[lang]
    lines = [
        f"# Censor report — {report.run_id}",
        "",
        f"`{report.target}` · tier {report.tier}",
        "",
    ]
    if report.grade is None or report.score is None:
        lines.append(t["none"])
    else:
        capped = f" ({t['capped']})" if report.capped_by_blocker else ""
        lines.append(f"**{t['grade']} {report.grade}** · {t['score']} {report.score:.1f}{capped}")
    lines += ["", f"| {t['dimension']} | {t['dim_score']} |", "|---|---|"]
    for d in report.dimensions:
        lines.append(f"| {d.dimension.value} | {'n/a' if d.score is None else f'{d.score:.1f}'} |")
    todo = sorted(
        (c for c in report.checks if c.status in _ORDER),
        key=lambda c: (_ORDER[c.status], -c.weight, c.id),
    )
    lines += ["", f"## {t['fix_heading']}", ""]
    if not todo:
        lines.append(t["nothing"])
    for c in todo:
        blocker = " · blocker" if c.blocker else ""
        lines.append(f"- **{c.status.value}**{blocker} `{c.id}` — {c.evidence}")
        if c.fix:
            lines.append(f"  - {c.fix}")
    return "\n".join(lines) + "\n"
