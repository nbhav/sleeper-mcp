from __future__ import annotations

import json

from sleeper_tooling.output import emit


def test_emit_json_outputs_sorted_pretty_json(capsys) -> None:
    emit({"b": 2, "a": 1}, output_format="json")

    output = capsys.readouterr().out

    assert json.loads(output) == {"a": 1, "b": 2}
    assert output.splitlines()[1] == '  "a": 1,'


def test_emit_csv_outputs_union_of_fields(capsys) -> None:
    emit([{"name": "One", "points": 1}, {"name": "Two", "team": "DEN"}], output_format="csv")

    output = capsys.readouterr().out

    assert output.splitlines() == [
        "name,points,team",
        "One,1,",
        "Two,,DEN",
    ]


def test_emit_table_handles_empty_rows(capsys) -> None:
    emit([], output_format="table")

    assert "No rows" in capsys.readouterr().out
