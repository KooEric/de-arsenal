#!/usr/bin/env python3
"""Profile a delimited text file before writing a Pugio `source:` block.

Standard library only, so it runs before anything is installed.

    python profile_file.py data.csv                 # report + suggested YAML
    python profile_file.py data.tsv --normalize input/data.csv [--skip-rows 2]

The report answers the questions that decide the pipeline spec: which encoding,
which delimiter, where the header is, which columns must stay text, which
tokens mean "no value", and which column could be a key.

`--normalize` rewrites the file as UTF-8, comma-delimited CSV. Use it only for
what Pugio's file source cannot express: a delimiter other than a comma, or
lines above the header. Encoding alone does not need it (`source.encoding`).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ENCODINGS = ["utf-8-sig", "cp949", "utf-16", "latin-1"]
NULL_TOKENS = {
    "NA",
    "N/A",
    "n/a",
    "NULL",
    "null",
    "None",
    "-",
    "--",
    "?",
    "없음",
    "미상",
    "해당없음",
}
SAMPLE_BYTES = 4_000_000
INT_RE = re.compile(r"^[+-]?\d+$")
LEADING_ZERO_RE = re.compile(r"^0\d+$")
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
NUM_WITH_COMMA_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """Return (python codec, label for the report)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "utf-8 with BOM"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", "utf-16"
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
        except UnicodeDecodeError as exc:
            # A sample cut mid-character fails only at its very end.
            if exc.start >= len(raw) - 4:
                return enc, enc
            continue
        return enc, "utf-8" if enc == "utf-8-sig" else enc
    return "latin-1", "unknown (read as latin-1)"


def sniff_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:200] if ln.strip()]
    best, best_score = ",", -1.0
    for cand in [",", "\t", ";", "|"]:
        counts = [ln.count(cand) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        common, freq = Counter(counts).most_common(1)[0]
        score = common * (freq / len(counts))
        if common > 0 and score > best_score:
            best, best_score = cand, score
    return best


def find_header_row(rows: list[list[str]]) -> int:
    """Index of the first row whose width matches the most common width."""
    widths = Counter(len(r) for r in rows if any(c.strip() for c in r))
    if not widths:
        return 0
    width = widths.most_common(1)[0][0]
    for i, row in enumerate(rows[:50]):
        if len(row) == width and all(c.strip() for c in row):
            return i
    return 0


def profile(path: Path, skip_rows: int | None) -> dict:
    size = path.stat().st_size
    with path.open("rb") as fh:
        raw = fh.read(SAMPLE_BYTES)
    codec, enc_label = detect_encoding(raw)
    text = raw.decode(codec, errors="replace")
    sampled = size > len(raw)
    if sampled:
        text = text[: text.rfind("\n") + 1]
    delim = sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header_at = find_header_row(rows) if skip_rows is None else skip_rows
    header = [c.strip() for c in rows[header_at]] if len(rows) > header_at else []
    body = [r for r in rows[header_at + 1 :] if any(c.strip() for c in r)]
    ragged = sum(1 for r in body if len(r) != len(header))

    columns = []
    for i, name in enumerate(header):
        vals = [r[i].strip() for r in body if i < len(r)]
        nonempty = [v for v in vals if v != ""]
        tokens = Counter(v for v in nonempty if v in NULL_TOKENS)
        real = [v for v in nonempty if v not in NULL_TOKENS]
        info: dict = {
            "name": name,
            "empty": len(vals) - len(nonempty),
            "distinct": len(set(real)),
            "example": real[0] if real else None,
        }
        notes = []
        if tokens:
            info["null_tokens"] = dict(tokens)
        if real and all(INT_RE.match(v) for v in real):
            if any(LEADING_ZERO_RE.match(v) for v in real):
                notes.append("leading zeros: keep as string")
                info["pin"] = "string"
            elif max(len(v.lstrip("+-")) for v in real) > 18:
                notes.append("too long for int64: keep as string")
                info["pin"] = "string"
        if real and sum(1 for v in real if NUM_WITH_COMMA_RE.match(v)) > len(real) * 0.5:
            notes.append("numbers with thousands separators: arrive as text, strip in a transform")
        if real and sum(1 for v in real if DATE_RE.match(v)) > len(real) * 0.9:
            notes.append("looks like a date")
        if real and len(set(real)) == len(vals) and len(vals) > 1:
            notes.append("unique in sample: key candidate")
            info["key_candidate"] = True
        if notes:
            info["notes"] = notes
        columns.append(info)

    dup_names = [n for n, c in Counter(header).items() if c > 1]
    full_dups = len(body) - len({tuple(r) for r in body})
    return {
        "file": str(path),
        "bytes": size,
        "sampled": sampled,
        "encoding": enc_label,
        "codec": codec,
        "delimiter": {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}[delim],
        "delimiter_char": delim,
        "header_row": header_at,
        "rows_in_sample": len(body),
        "ragged_rows": ragged,
        "fully_duplicated_rows": full_dups,
        "duplicate_column_names": dup_names,
        "blank_column_names": sum(1 for h in header if not h),
        "columns": columns,
    }


def suggest_yaml(rep: dict, path_for_yaml: str) -> str:
    out = ["source:", "  type: file", f"  path: {path_for_yaml}", "  format: csv"]
    codec = rep["codec"]
    if codec not in ("utf-8-sig", "utf-8"):
        out.append(f"  encoding: {codec}")
    tokens = sorted({t for c in rep["columns"] for t in c.get("null_tokens", {})})
    if tokens:
        quoted = ", ".join(json.dumps(t, ensure_ascii=False) for t in ["", *tokens])
        out.append(f"  null_values: [{quoted}]")
    pins = {c["name"]: c["pin"] for c in rep["columns"] if "pin" in c}
    if pins:
        body = ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {v}" for k, v in pins.items())
        out.append(f"  column_types: {{ {body} }}")
    return "\n".join(out)


def blockers(rep: dict) -> list[str]:
    found = []
    if rep["delimiter_char"] != ",":
        found.append(f"delimiter is {rep['delimiter']}: Pugio reads comma only")
    if rep["header_row"] > 0:
        found.append(f"{rep['header_row']} line(s) above the header")
    if rep["duplicate_column_names"]:
        found.append(f"duplicate column names: {rep['duplicate_column_names']}")
    if rep["blank_column_names"]:
        found.append(f"{rep['blank_column_names']} column(s) with no name")
    return found


def normalize(src: Path, dst: Path, rep: dict) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with (
        src.open("r", encoding=rep["codec"], newline="") as fin,
        dst.open("w", encoding="utf-8", newline="") as fout,
    ):
        reader = csv.reader(fin, delimiter=rep["delimiter_char"])
        writer = csv.writer(fout)
        width = None
        for i, row in enumerate(reader):
            if i < rep["header_row"] or not any(c.strip() for c in row):
                continue
            if width is None:
                width = len(row)
                seen: Counter[str] = Counter()
                fixed = []
                for j, name in enumerate(row):
                    name = name.strip() or f"column_{j + 1}"
                    seen[name] += 1
                    fixed.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
                row = fixed
            writer.writerow(row)
            written += 1
    return max(written - 1, 0)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("file", type=Path)
    ap.add_argument(
        "--skip-rows", type=int, default=None, help="lines above the header (default: detect)"
    )
    ap.add_argument("--normalize", type=Path, default=None, help="write a UTF-8 comma CSV here")
    ap.add_argument("--json", action="store_true", help="print the report as JSON only")
    args = ap.parse_args()

    if not args.file.is_file():
        print(f"error: {args.file} is not a file", file=sys.stderr)
        return 2
    rep = profile(args.file, args.skip_rows)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    scope = "first 4 MB" if rep["sampled"] else "whole file"
    print(f"{rep['file']}  ({rep['bytes']:,} bytes, profiled: {scope})")
    print(f"  encoding   {rep['encoding']}")
    print(f"  delimiter  {rep['delimiter']}")
    print(f"  header     line {rep['header_row'] + 1}")
    print(f"  rows       {rep['rows_in_sample']:,}" + (" in sample" if rep["sampled"] else ""))
    if rep["ragged_rows"]:
        print(f"  ragged     {rep['ragged_rows']} row(s) with a different number of cells")
    if rep["fully_duplicated_rows"]:
        print(f"  duplicates {rep['fully_duplicated_rows']} row(s) repeat another row exactly")
    print("  columns")
    for c in rep["columns"]:
        extra = "; ".join(c.get("notes", []))
        toks = c.get("null_tokens")
        if toks:
            extra = (extra + "; " if extra else "") + f"null-like tokens {toks}"
        if c["empty"]:
            extra = (extra + "; " if extra else "") + f"{c['empty']} empty"
        print(f"    {c['name'] or '(blank)':<24} e.g. {c['example']!r:<22} {extra}")

    blocked = blockers(rep)
    if args.normalize:
        n = normalize(args.file, args.normalize, rep)
        print(f"\nnormalized: {n:,} data rows -> {args.normalize} (utf-8, comma)")
        rep2 = profile(args.normalize, 0)
        print("\nsuggested source block:\n")
        print(suggest_yaml(rep2, str(args.normalize)))
        return 0
    if blocked:
        print("\nPugio cannot read this file as it is:")
        for b in blocked:
            print(f"  - {b}")
        target = f"input/{args.file.stem}.csv"
        print(f"\nrewrite it first:\n  python {sys.argv[0]} {args.file} --normalize {target}")
        return 1
    print("\nsuggested source block:\n")
    print(suggest_yaml(rep, str(args.file)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
