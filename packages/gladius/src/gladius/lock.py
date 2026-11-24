"""One transform at a time per output directory.

Two runs writing the same output used to share ``<output>.tmp`` and the second
one crashed with ``FileNotFoundError`` when the first moved it into place. The
lock is an OS advisory lock on ``<output>.lock`` (``flock`` on POSIX,
``msvcrt.locking`` on Windows), so the kernel drops it when the process dies:
a ``kill -9`` never leaves a stale lock behind, unlike a lock file's existence.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from arsenal_core.errors import FatalError


def lock_path(output: Path) -> Path:
    return output.with_name(output.name + ".lock")


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def output_lock(output: Path, transform: str) -> Generator[None, None, None]:
    """Hold the output's lock for the whole run, or fail at once if another run has it."""

    path = lock_path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise FatalError(
                f"transform {transform!r} is busy: another run is writing {output}. "
                "Wait for it to finish; the lock is released when that process exits"
            )
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
