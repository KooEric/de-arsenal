import json

from arsenal_core.schema import compare_schema_snapshots


def snapshot(*fields: dict[str, object]) -> str:
    return json.dumps(list(fields))


def test_schema_diff_detects_added_removed_and_changed_fields() -> None:
    diff = compare_schema_snapshots(
        snapshot(
            {"name": "id", "type": "int64", "nullable": False},
            {"name": "old", "type": "string", "nullable": True},
        ),
        snapshot(
            {"name": "id", "type": "string", "nullable": False},
            {"name": "new", "type": "bool", "nullable": True},
        ),
    )

    assert [change.field for change in diff.added] == ["new"]
    assert [change.field for change in diff.removed] == ["old"]
    assert [change.field for change in diff.changed] == ["id"]
    assert diff.summary() == "added=new; removed=old; changed=id"


def test_identical_schema_has_no_changes() -> None:
    value = snapshot({"name": "id", "type": "int64", "nullable": False})
    diff = compare_schema_snapshots(value, value)
    assert diff.has_changes is False
