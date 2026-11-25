"""Run statements on a DB-API connection and turn driver errors into Arsenal errors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from arsenal_core.errors import ArsenalError
from pugio.sinks.redshift import quietly

T = TypeVar("T")
Connect = Callable[[], Any]


def run_statements(
    connect: Connect,
    work: Callable[[Any], T],
    *,
    fail: Callable[[Exception], ArsenalError],
    transactional: bool = True,
) -> T:
    """Open, run ``work(cursor)``, commit (or roll back), close.

    ``transactional=False`` is for HiveServer2, whose DB-API driver has no transactions.
    """

    try:
        con = connect()
    except ArsenalError:
        raise
    except Exception as error:
        raise fail(error) from error
    try:
        result = work(con.cursor())
        if transactional:
            con.commit()
        return result
    except Exception as error:
        if transactional:
            quietly(con.rollback)
        if isinstance(error, ArsenalError):
            raise
        raise fail(error) from error
    finally:
        quietly(con.close)


def first_int(cursor: Any) -> int:
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0
