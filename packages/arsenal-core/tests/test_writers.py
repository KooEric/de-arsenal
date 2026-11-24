from pathlib import Path

import pytest

from arsenal_core.state import StateStore
from arsenal_core.writers import (
    DatasetBusyError,
    StreamWriter,
    exclusive_dataset,
    register_stream_writer,
    stream_writers,
)

RESOURCE = "stream:orders:orders"


def setup(tmp_path: Path) -> tuple[Path, StateStore, StreamWriter]:
    dataset = tmp_path / "data" / "orders"
    dataset.mkdir(parents=True)
    db = tmp_path / "state" / "orders.db"
    store = StateStore(db)
    writer = StreamWriter(str(db), RESOURCE, "orders", "orders")
    register_stream_writer(dataset, writer)
    return dataset, store, writer


def test_concurrent_registrations_of_one_writer_do_not_collide(tmp_path: Path) -> None:
    import threading

    writer = StreamWriter(str(tmp_path / "s.db"), RESOURCE, "orders", "orders")
    errors: list[BaseException] = []
    for attempt in range(30):
        dataset = tmp_path / f"d{attempt}"
        barrier = threading.Barrier(8)

        def register(target: Path = dataset, gate: threading.Barrier = barrier) -> None:
            gate.wait()
            try:
                register_stream_writer(target, writer)
            except BaseException as exc:  # noqa: BLE001 - collected for the assert below
                errors.append(exc)

        threads = [threading.Thread(target=register) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert stream_writers(dataset) == [writer]
        assert not list(dataset.parent.glob("**/*.tmp"))
    assert errors == []


def test_registration_is_idempotent_and_discoverable(tmp_path: Path) -> None:
    dataset, _, writer = setup(tmp_path)

    register_stream_writer(dataset, writer)

    assert stream_writers(dataset) == [writer]
    assert stream_writers(tmp_path / "unrelated") == []


def test_exclusive_access_takes_and_releases_the_stream_lease(tmp_path: Path) -> None:
    dataset, store, _ = setup(tmp_path)

    with exclusive_dataset(dataset, owner_id="onager") as writers:
        assert len(writers) == 1
        holder = store.lease(RESOURCE)
        assert holder is not None and holder.owner_id == "onager"
        # A stream starting now cannot get its lease.
        assert store.acquire_lease(RESOURCE, "hasta-runner", 60) is None

    assert store.lease(RESOURCE) is None


def test_live_runner_makes_the_dataset_busy(tmp_path: Path) -> None:
    dataset, store, _ = setup(tmp_path)
    store.acquire_lease(RESOURCE, "hasta-runner", 60)

    with (
        pytest.raises(DatasetBusyError, match="hasta-runner"),
        exclusive_dataset(dataset, owner_id="onager"),
    ):
        pass


def test_uncommitted_batches_block_and_leave_no_lease_behind(tmp_path: Path) -> None:
    dataset, store, _ = setup(tmp_path)
    store.plan_stream_batch(
        batch_id="b1",
        pipeline="orders",
        stream_name="orders",
        revision="r",
        start_position={},
        end_position={"cursor": "1"},
        event_hash="e",
    )

    with (
        pytest.raises(DatasetBusyError, match="uncommitted"),
        exclusive_dataset(dataset, owner_id="onager"),
    ):
        pass
    assert store.lease(RESOURCE) is None


def test_registration_for_a_deleted_state_db_is_ignored(tmp_path: Path) -> None:
    dataset = tmp_path / "data" / "orders"
    dataset.mkdir(parents=True)
    register_stream_writer(dataset, StreamWriter(str(tmp_path / "gone.db"), RESOURCE, "o", "o"))

    with exclusive_dataset(dataset, owner_id="onager") as writers:
        assert writers == []
