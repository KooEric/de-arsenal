"""Golden corpus for LogicalPlan v1 engine equivalence (design invariant I7).

Every case runs on the DuckDB renderer (always, locally and in CI) and on the
Spark renderer (wherever pyspark + Java exist). Both must produce exactly
``expected``; the DuckDB result is the reference semantics.

Rows are compared order-insensitively. Only deterministic expressions are
allowed here: no ``current_*`` functions, and ``dedup`` only over rows that
are identical, because both engines keep an arbitrary row per key.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

INPUT_ROWS: list[dict[str, Any]] = [
    {"id": 1, "name": "  Ada ", "amount": 2.5, "qty": 4, "day": dt.date(2026, 1, 15)},
    {"id": 2, "name": None, "amount": 3.5, "qty": 1, "day": dt.date(2026, 2, 1)},
    {"id": 3, "name": "bob", "amount": -2.5, "qty": 7, "day": dt.date(2025, 12, 31)},
    {"id": 4, "name": "Cy", "amount": 12.75, "qty": 3, "day": dt.date(2026, 3, 9)},
    {"id": 4, "name": "Cy", "amount": 12.75, "qty": 3, "day": dt.date(2026, 3, 9)},
    {"id": 5, "name": "dee", "amount": 1.2346, "qty": 0, "day": dt.date(2026, 7, 4)},
]

CASES: list[dict[str, Any]] = [
    {
        "id": "filter_and_null_predicate",
        "steps": [{"filter": "amount > 2 AND name IS NOT NULL"}],
        "columns": ["id", "name", "amount", "qty", "day"],
        "expected": [
            (1, "  Ada ", 2.5, 4, dt.date(2026, 1, 15)),
            (4, "Cy", 12.75, 3, dt.date(2026, 3, 9)),
            (4, "Cy", 12.75, 3, dt.date(2026, 3, 9)),
        ],
    },
    {
        "id": "map_projects_named_expressions",
        "map": {"key": "id", "total": "amount * qty"},
        "columns": ["key", "total"],
        "expected": [(1, 10.0), (2, 3.5), (3, -17.5), (4, 38.25), (4, 38.25), (5, 0.0)],
    },
    {
        "id": "rename_moves_columns_last",
        "steps": [{"rename": {"amount": "gross", "id": "key"}}, {"filter": "key < 3"}],
        "columns": ["name", "qty", "day", "gross", "key"],
        "expected": [
            ("  Ada ", 4, dt.date(2026, 1, 15), 2.5, 1),
            (None, 1, dt.date(2026, 2, 1), 3.5, 2),
        ],
    },
    {
        "id": "double_to_integer_rounds_half_even",
        "steps": [{"cast": {"amount": "integer"}}, {"select": ["id", "amount"]}],
        "columns": ["id", "amount"],
        "expected": [(1, 2), (2, 4), (3, -2), (4, 13), (4, 13), (5, 1)],
    },
    {
        "id": "bare_decimal_is_38_9",
        "steps": [{"cast": {"amount": "decimal"}}, {"select": ["id", "amount"]}],
        "columns": ["id", "amount"],
        "expected": [
            (1, Decimal("2.500")),
            (2, Decimal("3.500")),
            (3, Decimal("-2.500")),
            (4, Decimal("12.750")),
            (4, Decimal("12.750")),
            (5, Decimal("1.2346")),
        ],
    },
    {
        "id": "arithmetic_and_modulo",
        "steps": [
            {"derive": {"rem": "qty % 3", "dist": "abs(amount - 3)", "half": "qty / 2"}},
            {"select": ["id", "rem", "dist", "half"]},
        ],
        "columns": ["id", "rem", "dist", "half"],
        "expected": [
            (1, 1, 0.5, 2.0),
            (2, 1, 0.5, 0.5),
            (3, 1, 5.5, 3.5),
            (4, 0, 9.75, 1.5),
            (4, 0, 9.75, 1.5),
            (5, 0, 1.7654, 0.0),
        ],
    },
    {
        "id": "string_functions_and_null_concatenation",
        "steps": [
            {
                "derive": {
                    "clean": "upper(trim(name))",
                    "n": "length(trim(name))",
                    "head": "substring(trim(name), 1, 2)",
                    "label": "coalesce(name, 'n/a')",
                    "tagged": "trim(name) || '-x'",
                }
            },
            {"select": ["id", "clean", "n", "head", "label", "tagged"]},
        ],
        "columns": ["id", "clean", "n", "head", "label", "tagged"],
        "expected": [
            (1, "ADA", 3, "Ad", "  Ada ", "Ada-x"),
            (2, None, None, None, "n/a", None),
            (3, "BOB", 3, "bo", "bob", "bob-x"),
            (4, "CY", 2, "Cy", "Cy", "Cy-x"),
            (4, "CY", 2, "Cy", "Cy", "Cy-x"),
            (5, "DEE", 3, "de", "dee", "dee-x"),
        ],
    },
    {
        "id": "case_and_extract",
        "steps": [
            {
                "derive": {
                    "size": "CASE WHEN qty > 2 THEN 'big' ELSE 'small' END",
                    "year": "extract(year FROM day)",
                }
            },
            {"select": ["id", "size", "year"]},
        ],
        "columns": ["id", "size", "year"],
        "expected": [
            (1, "big", 2026),
            (2, "small", 2026),
            (3, "big", 2025),
            (4, "big", 2026),
            (4, "big", 2026),
            (5, "small", 2026),
        ],
    },
    {
        "id": "dedup_identical_rows",
        "steps": [{"dedup": ["id"]}, {"select": ["id", "qty"]}],
        "columns": ["id", "qty"],
        "expected": [(1, 4), (2, 1), (3, 7), (4, 3), (5, 0)],
    },
]
