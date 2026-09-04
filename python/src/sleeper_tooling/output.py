from __future__ import annotations

import csv
import json
import sys
from collections.abc import Sequence
from typing import Any, Literal

from rich.console import Console
from rich.table import Table

OutputFormat = Literal["json", "csv", "table"]


def emit(data: Any, *, output_format: OutputFormat) -> None:
    if output_format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    if output_format == "csv":
        write_csv(data)
        return
    write_table(data)


def write_csv(data: Any) -> None:
    rows = _rows(data)
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def write_table(data: Any) -> None:
    rows = _rows(data)
    console = Console()
    if not rows:
        console.print("No rows")
        return

    columns = [key for key in rows[0] if _is_scalar(rows[0][key])]
    table = Table()
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if isinstance(data, Sequence) and not isinstance(data, str):
        return [row for row in data if isinstance(row, dict)]
    return [{"value": data}]


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)
