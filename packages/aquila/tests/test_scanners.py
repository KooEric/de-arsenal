from __future__ import annotations

from pathlib import Path

import yaml

from aquila import service


def spec(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_every_source_and_sink_kind_maps_to_a_secret_free_uri(tmp_path: Path) -> None:
    spec(
        tmp_path / "db.yaml",
        {
            "name": "accounts",
            "source": {
                "type": "database",
                "dialect": "mysql",
                "dsn_env": "MYSQL_DSN",
                "table": "acct",
            },
            "sink": {
                "type": "duckdb",
                "path": "./wh.duckdb",
                "table": "accounts",
                "merge_key": ["id"],
            },
        },
    )
    spec(
        tmp_path / "py.yaml",
        {
            "name": "custom",
            "source": {"type": "python", "target": "pkg.mod:Source"},
            "sink": {
                "type": "postgres",
                "dsn_env": "PG_DSN",
                "table": "custom",
                "merge_key": ["id"],
            },
        },
    )
    spec(
        tmp_path / "cdc.yaml",
        {
            "name": "cdc",
            "source": {
                "type": "database_outbox",
                "dialect": "postgres",
                "dsn_env": "OUTBOX",
                "table": "ob",
            },
            "stream": {"name": "cdc"},
            "sink": {"type": "duckdb", "path": "./wh.duckdb", "table": "cdc", "merge_key": ["id"]},
        },
    )
    spec(
        tmp_path / "odd.yaml",
        {"name": "odd", "source": {"type": "file", "path": "x"}, "sink": {"type": "kafka"}},
    )
    (tmp_path / "broken.yaml").write_text("name: [unclosed", encoding="utf-8")
    spec(tmp_path / ".hidden" / "skip.yaml", {"name": "h", "input": "a", "output": "b"})

    result = service.collect([tmp_path], root=tmp_path)

    assert sorted(result.datasets) == sorted(
        [
            "mysql://${MYSQL_DSN}/acct",
            f"duckdb://{tmp_path.as_posix()}/wh.duckdb/accounts",
            "python://pkg.mod:Source",
            "postgres://${PG_DSN}/custom",
            "postgres://${OUTBOX}/ob",
            f"duckdb://{tmp_path.as_posix()}/wh.duckdb/cdc",
        ]
    )
    assert {via for via in result.vias} == {
        "pugio:accounts",
        "pugio:custom",
        "hasta:cdc",
        "pugio:odd",
    }
    assert [reason for _, reason in result.skipped] == ["unsupported sink type 'kafka'"]


def test_an_unreadable_state_db_is_skipped_not_fatal(tmp_path: Path) -> None:
    spec(
        tmp_path / "p.yaml",
        {
            "name": "p",
            "source": {"type": "file", "path": "x"},
            "sink": {"type": "parquet", "path": "./out"},
        },
    )
    (tmp_path / ".arsenal").mkdir()
    (tmp_path / ".arsenal" / "p.db").write_bytes(b"not a database")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "bad.parquet").write_bytes(b"nope")

    result = service.collect([tmp_path], root=tmp_path)

    reasons = sorted(reason.split(":")[0] for _, reason in result.skipped)
    assert reasons == ["unreadable parquet footer", "unreadable state db"]
