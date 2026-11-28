import json
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from vigil.rule import Rule, load_rules

SCHEMA = Path(__file__).parents[3] / "schemas" / "vigil-rule.json"

GOOD = """\
id: aws-root-console-login
title: Root console login
severity: high
attack: [T1078.004]
source: cloudtrail
query: |
  SELECT * FROM events WHERE action = 'ConsoleLogin' AND actor_type = 'Root';
tests:
  match:
    - { action: ConsoleLogin, actor_type: Root }
  no_match:
    - { action: ConsoleLogin, actor_type: IAMUser }
"""


def _write(rules_dir: Path, name: str, text: str) -> Path:
    rules_dir.mkdir(exist_ok=True)
    path = rules_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_rules_reads_a_valid_rule(tmp_path: Path) -> None:
    _write(tmp_path, "root.yaml", GOOD)
    (rule,) = load_rules(tmp_path)
    assert rule.id == "aws-root-console-login"
    assert rule.attack == ["T1078.004"]
    assert not rule.query.endswith(";")
    assert len(rule.tests.match) == 1


def test_missing_directory_and_empty_directory_are_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="not found"):
        load_rules(tmp_path / "nope")
    with pytest.raises(FatalError, match="no rule files"):
        load_rules(tmp_path)


def test_schema_error_names_file_and_field(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.yaml", GOOD.replace("severity: high", "severity: urgent"))
    with pytest.raises(FatalError) as info:
        load_rules(tmp_path)
    assert str(path) in str(info.value)
    assert "severity" in str(info.value)


def test_duplicate_id_names_both_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", GOOD)
    _write(tmp_path, "b.yaml", GOOD)
    with pytest.raises(FatalError, match=r"duplicate rule id.*a\.yaml.*b\.yaml"):
        load_rules(tmp_path)


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE events",
        "SELECT * FROM events; DROP TABLE events",
        "SELEC * FROM events",
    ],
)
def test_query_must_be_one_select(tmp_path: Path, query: str) -> None:
    text = GOOD.split("query:")[0] + f'query: "{query}"\n'
    _write(tmp_path, "bad.yaml", text)
    with pytest.raises(FatalError, match="query"):
        load_rules(tmp_path)


def test_query_referencing_unknown_column_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("actor_type = 'Root'", "actor_kind = 'Root'"))
    with pytest.raises(FatalError, match="fails against events"):
        load_rules(tmp_path)


def test_query_must_return_event_id(tmp_path: Path) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("SELECT * FROM", "SELECT action FROM"))
    with pytest.raises(FatalError, match="must return event_id"):
        load_rules(tmp_path)


@pytest.mark.parametrize("technique", ["cloud-accounts", '"T1078\\n"'])
def test_bad_technique_id_is_fatal(tmp_path: Path, technique: str) -> None:
    _write(tmp_path, "bad.yaml", GOOD.replace("[T1078.004]", f"[{technique}]"))
    with pytest.raises(FatalError, match="attack"):
        load_rules(tmp_path)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM events WHERE action = 'X' -- note",
        "SELECT * FROM events; -- note",
        "SELECT * FROM events WHERE action = 'a;b' ;",
    ],
)
def test_trailing_comment_and_semicolon_load(tmp_path: Path, query: str) -> None:
    text = (
        GOOD.split("query:")[0] + "query: |\n  " + query + "\n" + "tests:" + GOOD.split("tests:")[1]
    )
    _write(tmp_path, "ok.yaml", text)
    (rule,) = load_rules(tmp_path)
    assert not rule.query.rstrip().endswith(";")


def test_query_reading_a_file_is_fatal(tmp_path: Path) -> None:
    text = GOOD.replace(
        "SELECT * FROM events WHERE action = 'ConsoleLogin' AND actor_type = 'Root';",
        "SELECT 'x' AS event_id FROM read_csv('/etc/hosts')",
    )
    _write(tmp_path, "bad.yaml", text)
    with pytest.raises(FatalError, match="fails against events"):
        load_rules(tmp_path)


def test_schema_file_matches_model() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == Rule.model_json_schema()


def test_yaml_and_yml_files_are_both_loaded(tmp_path: Path) -> None:
    _write(tmp_path, "b.yaml", GOOD)
    _write(tmp_path, "a.yml", GOOD.replace("aws-root-console-login", "other-rule"))
    assert [rule.id for rule in load_rules(tmp_path)] == ["other-rule", "aws-root-console-login"]


def test_directory_with_only_other_files_names_both_extensions(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "x")
    with pytest.raises(FatalError, match=r"\*\.yaml, \*\.yml"):
        load_rules(tmp_path)
