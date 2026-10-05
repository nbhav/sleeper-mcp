"""nflverse data access and parity helpers.

This module keeps the nflreadpy dependency behind a small boundary. Sleeper
continues to own league and team context until the parity checks here support
moving a particular stat family.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
import time
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


NFLVERSE_SOURCE = "nflverse_stats"
NFLVERSE_DATASET = "player_stats"


@dataclass(frozen=True)
class ParityMismatch:
    player_id: str
    season: int
    week: int
    stat_key: str
    sleeper_value: float
    nflverse_value: float

    @property
    def delta(self) -> float:
        return round(self.nflverse_value - self.sleeper_value, 4)

    @property
    def direction(self) -> str:
        return "higher" if self.delta > 0 else "lower"


@dataclass(frozen=True)
class ParityReport:
    comparisons: int
    mismatches: tuple[ParityMismatch, ...]
    sleeper_rows: int = 0
    nflverse_rows: int = 0
    missing_nflverse: int = 0
    unresolved_identity: int = 0
    provider_metadata: Mapping[str, Any] | None = None

    @property
    def match_rate(self) -> float:
        return (self.comparisons - len(self.mismatches)) / self.comparisons if self.comparisons else 1.0

    @property
    def coverage(self) -> float:
        return self.comparisons / self.sleeper_rows if self.sleeper_rows else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparisons": self.comparisons,
            "mismatches": [
                {
                    **mismatch.__dict__,
                    "delta": mismatch.delta,
                    "direction": mismatch.direction,
                }
                for mismatch in self.mismatches
            ],
            "match_rate": self.match_rate,
            "coverage": self.coverage,
            "sleeper_rows": self.sleeper_rows,
            "nflverse_rows": self.nflverse_rows,
            "missing_nflverse": self.missing_nflverse,
            "unresolved_identity": self.unresolved_identity,
            "mismatch_summary": {
                "by_stat": _count_by(self.mismatches, lambda item: item.stat_key),
                "by_direction": _count_by(self.mismatches, lambda item: item.direction),
                "total_delta": round(sum(item.delta for item in self.mismatches), 4),
            },
            "provider_metadata": dict(self.provider_metadata or {}),
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
    gsis_to_sleeper = _identity_map(player_ids)
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


def sync_nflverse_stats(
    repository: Any,
    seasons: Sequence[int],
    *,
    weeks: Iterable[int] | None = None,
    loader: NflverseLoader | None = None,
    fetched_at: float | None = None,
) -> dict[str, Any]:
    """Persist nflverse weekly stats without touching Sleeper rows."""
    if loader is None:
        import nflreadpy as loader
    fetched = time.time() if fetched_at is None else float(fetched_at)
    player_ids = _to_records(loader.load_ff_playerids())
    identity_map = _identity_map(player_ids)
    raw_rows = _to_records(loader.load_player_stats(list(seasons)))
    allowed_weeks = {int(week) for week in weeks} if weeks is not None else None
    rows_by_week: dict[tuple[int, int], list[dict[str, Any]]] = {}
    unresolved = 0
    skipped = 0
    for raw in raw_rows:
        if raw.get("season_type") != "REG":
            skipped += 1
            continue
        season = int(raw["season"])
        week = int(raw["week"])
        if allowed_weeks is not None and week not in allowed_weeks:
            skipped += 1
            continue
        sleeper_id = identity_map.get(str(raw.get("player_id")))
        if sleeper_id is None:
            unresolved += 1
            continue
        stats = {
            stat_key: raw[column]
            for stat_key, column in STAT_KEY_MAP.items()
            if raw.get(column) is not None
        }
        rows_by_week.setdefault((season, week), []).append(
            {
                "player_id": sleeper_id,
                "player": {
                    "full_name": raw.get("player_display_name") or raw.get("player_name"),
                    "team": raw.get("recent_team") or raw.get("team"),
                    "position": raw.get("position"),
                },
                "team": raw.get("recent_team") or raw.get("team"),
                "position": raw.get("position"),
                "stats": stats,
                "raw_nflverse": raw,
            }
        )
    package_version = _package_version("nflreadpy")
    counts: dict[str, int] = {}
    for (season, week), rows in sorted(rows_by_week.items()):
        result = repository.upsert_player_week_rows(
            season=season,
            week=week,
            source=NFLVERSE_SOURCE,
            rows=rows,
            scoring_settings=None,
        )
        counts[f"{season}:{week}"] = sum(int(value) for value in result.values()) if isinstance(result, Mapping) else int(result or 0)
        metadata = {
            "provider": "nflverse",
            "dataset": NFLVERSE_DATASET,
            "loader": "nflreadpy",
            "package_version": package_version,
            "season_type": "REG",
            "identity_mapping": "gsis_to_sleeper",
            "source": NFLVERSE_SOURCE,
            "coverage": {"season": season, "weeks": [week]},
            "row_count": len(rows),
            "unresolved_identity_count": unresolved,
            "skipped_row_count": skipped,
            "fetched_at": fetched,
        }
        repository.upsert_provider_sync_metadata(
            provider="nflverse",
            dataset=NFLVERSE_DATASET,
            season=season,
            week=week,
            fetched_at=fetched,
            metadata=metadata,
        )
    return {
        "provider": "nflverse",
        "dataset": NFLVERSE_DATASET,
        "source": NFLVERSE_SOURCE,
        "fetched_at": fetched,
        "package_version": package_version,
        "row_counts": counts,
        "mapped_rows": sum(len(rows) for rows in rows_by_week.values()),
        "unresolved_identity_count": unresolved,
        "skipped_row_count": skipped,
    }


def compare_stat_values(
    sleeper_rows: Iterable[Mapping[str, Any]],
    nflverse_rows: Iterable[Mapping[str, Any]],
    *,
    stat_key_map: Mapping[str, str] = STAT_KEY_MAP,
    unresolved_identity: int = 0,
    provider_metadata: Mapping[str, Any] | None = None,
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
    nflverse_keys = set(nflverse_values)
    return ParityReport(
        comparisons,
        tuple(mismatches),
        sleeper_rows=len(sleeper_values),
        nflverse_rows=len(nflverse_keys),
        missing_nflverse=len(set(sleeper_values) - nflverse_keys),
        unresolved_identity=int(unresolved_identity),
        provider_metadata=provider_metadata,
    )


def _identity_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        gsis_id = row.get("gsis_id")
        sleeper_id = row.get("sleeper_id")
        if gsis_id and sleeper_id and str(gsis_id) not in mapping:
            mapping[str(gsis_id)] = str(sleeper_id)
    return mapping


def _count_by(items: Iterable[Any], key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = str(key(item))
        counts[label] = counts.get(label, 0) + 1
    return counts


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def _to_records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    return [dict(row) for row in value]
