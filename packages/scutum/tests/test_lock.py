from pathlib import Path

import pytest
from scutum import FileLock, LockConflict


def test_file_lock_retries_then_acquires(tmp_path: Path) -> None:
    path = tmp_path / "dataset.lock"
    path.write_text("holder", encoding="utf-8")
    slept: list[float] = []

    lock = FileLock(path, attempts=3, backoff_seconds=0.25, sleep=slept.append)

    def release_holder(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            path.unlink()

    lock = FileLock(path, attempts=3, backoff_seconds=0.25, sleep=release_holder)
    lock.acquire()
    try:
        assert path.exists()
        assert slept == [0.25, 0.5]
    finally:
        lock.release()


def test_file_lock_raises_retryable_conflict(tmp_path: Path) -> None:
    path = tmp_path / "dataset.lock"
    path.write_text("holder", encoding="utf-8")
    with pytest.raises(LockConflict, match="lock busy"):
        FileLock(path, attempts=2, backoff_seconds=0, sleep=lambda _: None).acquire()
