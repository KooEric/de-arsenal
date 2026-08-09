"""Small cross-process file lock with bounded retry/backoff."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from arsenal_core.errors import RetryableError


class LockConflict(RetryableError):
    """The lock remained held after the configured retry budget."""


class FileLock:
    """Exclusive lock file using O_EXCL and deterministic retry policy."""

    def __init__(
        self,
        path: Path,
        *,
        attempts: int = 5,
        backoff_seconds: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")
        self.path = path
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.attempts):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == self.attempts - 1:
                    raise LockConflict(
                        f"lock busy after {self.attempts} attempts: {self.path}"
                    ) from None
                self._sleep(self.backoff_seconds * (2**attempt))
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(f"pid={os.getpid()}\n")
                self._held = True
                return

    def release(self) -> None:
        if self._held:
            with suppress(FileNotFoundError):
                self.path.unlink()
            self._held = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


@contextmanager
def file_lock(
    path: Path,
    *,
    attempts: int = 5,
    backoff_seconds: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> Generator[FileLock, None, None]:
    """Context-manager convenience wrapper around :class:`FileLock`."""
    lock = FileLock(
        path,
        attempts=attempts,
        backoff_seconds=backoff_seconds,
        sleep=sleep,
    )
    with lock:
        yield lock
