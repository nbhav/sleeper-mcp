"""nflverse data access and parity helpers.

This module keeps the nflreadpy dependency behind a small boundary. Sleeper
continues to own league and team context until the parity checks here support
moving a particular stat family.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


NflreadpyLoader = Any

STAT_KEY_MAP: dict[str, str] = {
    "pass_att": "attempts",
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "passing_interceptions",
    "rush_att": "carries",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rec_tgt": "targets",
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
}


class NflverseLoader(Protocol):
    def load_ff_playerids(self) -> Any:
        ...

    def load_player_stats(self, seasons: Sequence[int]) -> Any:
        ...


@dataclass(frozen=True)
class ParityMismatch:
    player_id: str
    season: int
    week: int
    stat_key: str
    sleeper_value: float
    nflverse_value: float


@dataclass(frozen=True)
class ParityReport:
    comparisons: int
    mismatches: tuple[ParityMismatch, ...]

    @property
    def match_rate(self) -> float:
        return (self.comparisons - len(self.mismatches)) / self.comparisons if self.comparisons else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparisons": self.comparisons,
            "mismatches": [mismatch.__dict__ for mismatch in self.mismatches],
            "match_rate": self.match_rate,
        }


def load_weekly_player_stats(
    seasons: Sequence[int],
    *,
    weeks: Iterable[int] | None = None,
    loader: NflverseLoader | None = None,
) -> list[dict[str, Any]]:
    """Load regular-season player stats keyed with Sleeper IDs.

    The returned rows retain nflverse columns and add ``sleeper_id``. Rows
    without a Sleeper mapping are omitted because they cannot be compared with
    or joined to the normalized Sleeper database.
    """
    if loader is None:
        import nflreadpy as loader

    player_ids = _to_records(loader.load_ff_playerids())
    gsis_to_sleeper = {
        str(row["gsis_id"]): str(row["sleeper_id"])
        for row in player_ids
        if row.get("gsis_id") and row.get("sleeper_id")
    }
    rows = _to_records(loader.load_player_stats(list(seasons)))
    allowed_weeks = {int(week) for week in weeks} if weeks is not None else None
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if row.get("season_type") != "REG":
            continue
        if allowed_weeks is not None and int(row["week"]) not in allowed_weeks:
            continue
        sleeper_id = gsis_to_sleeper.get(str(row.get("player_id")))
        if sleeper_id is None:
            continue
        normalized.append({**row, "sleeper_id": sleeper_id})
    return normalized


def compare_stat_values(
    sleeper_rows: Iterable[Mapping[str, Any]],
    nflverse_rows: Iterable[Mapping[str, Any]],
    *,
    stat_key_map: Mapping[str, str] = STAT_KEY_MAP,
) -> ParityReport:
    """Compare normalized Sleeper stat rows with nflverse weekly rows."""
    sleeper_values = {
        (str(row["player_id"]), int(row["season"]), int(row["week"]), str(row["stat_key"])): float(row["stat_value"])
        for row in sleeper_rows
    }
    nflverse_values: dict[tuple[str, int, int, str], float] = {}
    for row in nflverse_rows:
        for stat_key, column in stat_key_map.items():
            value = row.get(column)
            if value is not None:
                nflverse_values[(str(row["sleeper_id"]), int(row["season"]), int(row["week"]), stat_key)] = float(value)

    mismatches: list[ParityMismatch] = []
    comparisons = 0
    for key, sleeper_value in sleeper_values.items():
        nflverse_value = nflverse_values.get(key)
        if nflverse_value is None:
            continue
        comparisons += 1
        if sleeper_value != nflverse_value:
            player_id, season, week, stat_key = key
            mismatches.append(
                ParityMismatch(player_id, season, week, stat_key, sleeper_value, nflverse_value)
            )
    return ParityReport(comparisons, tuple(mismatches))


def _to_records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    return [dict(row) for row in value]
