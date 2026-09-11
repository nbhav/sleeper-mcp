from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

StatSource = Literal["stats", "projections"]

GRAPH_ROW_FIELDS = (
    "season",
    "week",
    "player_id",
    "name",
    "team",
    "position",
    "stat_key",
    "stat_value",
)


class TrendQueryRepository(Protocol):
    def query_numeric_stat_rows(
        self,
        *,
        source: StatSource,
        season: int,
        start_week: int,
        end_week: int,
        stat_keys: Sequence[str] | None = None,
        player_ids: Sequence[str] | None = None,
        positions: Sequence[str] | None = None,
    ) -> Iterable[dict[str, Any]]:
        """Return one row per numeric player stat from the normalized store."""


class DecisionDataStatusRepository(Protocol):
    def decision_data_status(self, *, season: int | None = None) -> dict[str, Any] | None:
        """Return compact normalized decision data freshness metadata."""


def player_stat_trends(
    repository: TrendQueryRepository,
    *,
    season: int,
    player_id: str,
    stat_key: str,
    start_week: int,
    end_week: int,
    source: StatSource = "stats",
) -> list[dict[str, Any]]:
    validate_week_window(start_week, end_week)
    rows = _query_numeric_rows(
        repository,
        source=source,
        season=season,
        start_week=start_week,
        end_week=end_week,
        stat_keys=[stat_key],
        player_ids=[player_id],
    )
    return sorted(rows, key=lambda row: (int(row["week"]), str(row["stat_key"])))


def multi_stat_usage_trends(
    repository: TrendQueryRepository,
    *,
    season: int,
    start_week: int,
    end_week: int,
    stat_keys: Sequence[str],
    player_ids: Sequence[str] | None = None,
    positions: Sequence[str] | None = None,
    source: StatSource = "stats",
) -> list[dict[str, Any]]:
    validate_week_window(start_week, end_week)
    rows = _query_numeric_rows(
        repository,
        source=source,
        season=season,
        start_week=start_week,
        end_week=end_week,
        stat_keys=stat_keys,
        player_ids=player_ids,
        positions=positions,
    )
    return sorted(
        rows,
        key=lambda row: (
            str(row["player_id"]),
            str(row["stat_key"]),
            int(row["week"]),
        ),
    )


def position_stat_leaders(
    repository: TrendQueryRepository,
    *,
    season: int,
    week: int,
    position: str,
    stat_key: str,
    source: StatSource = "stats",
    limit: int = 10,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    rows = _query_numeric_rows(
        repository,
        source=source,
        season=season,
        start_week=week,
        end_week=week,
        stat_keys=[stat_key],
        positions=[position],
    )
    leaders = sorted(
        rows,
        key=lambda row: (
            -float(row["stat_value"]),
            str(row["name"]),
            str(row["player_id"]),
        ),
    )[:limit]
    return [
        {"position_rank": rank, **row}
        for rank, row in enumerate(leaders, start=1)
    ]


def projection_actual_deltas(
    repository: TrendQueryRepository,
    *,
    season: int,
    week: int,
    stat_keys: Sequence[str],
    player_ids: Sequence[str] | None = None,
    positions: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    actual_rows = _query_numeric_rows(
        repository,
        source="stats",
        season=season,
        start_week=week,
        end_week=week,
        stat_keys=stat_keys,
        player_ids=player_ids,
        positions=positions,
    )
    projection_rows = _query_numeric_rows(
        repository,
        source="projections",
        season=season,
        start_week=week,
        end_week=week,
        stat_keys=stat_keys,
        player_ids=player_ids,
        positions=positions,
    )
    projected_by_key = {_stat_identity(row): row for row in projection_rows}
    rows: list[dict[str, Any]] = []
    for actual in actual_rows:
        projected = projected_by_key.get(_stat_identity(actual))
        projected_value = float(projected["stat_value"]) if projected else 0.0
        actual_value = float(actual["stat_value"])
        delta = round(actual_value - projected_value, 4)
        rows.append(
            {
                **actual,
                "stat_value": _compact_number(delta),
                "actual_value": _compact_number(actual_value),
                "projected_value": _compact_number(projected_value),
                "delta_value": _compact_number(delta),
            }
        )
    rows = sorted(
        rows,
        key=lambda row: (
            abs(float(row["delta_value"])),
            float(row["actual_value"]),
        ),
        reverse=True,
    )
    return rows[:limit] if limit is not None else rows


def week_over_week_movers(
    repository: TrendQueryRepository,
    *,
    season: int,
    previous_week: int,
    current_week: int,
    stat_key: str,
    positions: Sequence[str] | None = None,
    source: StatSource = "stats",
    limit: int = 10,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if current_week <= previous_week:
        raise ValueError("current_week must be greater than previous_week")
    rows = _query_numeric_rows(
        repository,
        source=source,
        season=season,
        start_week=previous_week,
        end_week=current_week,
        stat_keys=[stat_key],
        positions=positions,
    )
    previous = {
        str(row["player_id"]): row
        for row in rows
        if int(row["week"]) == previous_week
    }
    current = {
        str(row["player_id"]): row
        for row in rows
        if int(row["week"]) == current_week
    }
    movers = []
    for player_id in sorted(set(previous) & set(current)):
        previous_value = float(previous[player_id]["stat_value"])
        current_value = float(current[player_id]["stat_value"])
        delta = round(current_value - previous_value, 4)
        movers.append(
            {
                **current[player_id],
                "stat_value": _compact_number(delta),
                "previous_week": previous_week,
                "current_week": current_week,
                "previous_value": _compact_number(previous_value),
                "current_value": _compact_number(current_value),
                "delta_value": _compact_number(delta),
            }
        )

    return {
        "season": season,
        "previous_week": previous_week,
        "current_week": current_week,
        "stat_key": stat_key,
        "source": source,
        "top_risers": sorted(
            movers,
            key=lambda row: (
                float(row["delta_value"]),
                float(row["current_value"]),
            ),
            reverse=True,
        )[:limit],
        "top_fallers": sorted(
            movers,
            key=lambda row: (
                float(row["delta_value"]),
                float(row["current_value"]),
            ),
        )[:limit],
    }


def decision_data_status(
    repository: DecisionDataStatusRepository,
    *,
    season: int | None = None,
    max_age_seconds: int = 86400,
    now: float | None = None,
) -> dict[str, Any]:
    if max_age_seconds < 1:
        raise ValueError("max_age_seconds must be at least 1")
    checked_at = time.time() if now is None else now
    raw_status = repository.decision_data_status(season=season)
    if not raw_status:
        return {
            "status": "missing",
            "fresh": False,
            "season": season,
            "row_count": 0,
            "max_age_seconds": max_age_seconds,
            "evidence": ["normalized decision data status was not found"],
        }

    row_count = int(
        raw_status.get("numeric_stat_rows")
        or raw_status.get("row_count")
        or raw_status.get("total_rows")
        or 0
    )
    last_synced_at = (
        raw_status.get("last_synced_at")
        or raw_status.get("last_sync_at")
        or raw_status.get("fetched_at")
    )
    age_seconds = _age_seconds(last_synced_at, checked_at)
    if row_count <= 0:
        status = "missing"
    elif age_seconds is None or age_seconds > max_age_seconds:
        status = "stale"
    else:
        status = "fresh"

    output = {
        "status": status,
        "fresh": status == "fresh",
        "season": raw_status.get("season", season),
        "row_count": row_count,
        "last_synced_at": last_synced_at,
        "age_seconds": int(age_seconds) if age_seconds is not None else None,
        "max_age_seconds": max_age_seconds,
        "latest_stats_week": raw_status.get("latest_stats_week"),
        "latest_projections_week": raw_status.get("latest_projections_week"),
        "sources": raw_status.get("sources") or [],
    }
    if raw_status.get("metadata"):
        output["metadata"] = raw_status["metadata"]
    return output


def validate_stat_source(source: str) -> None:
    if source not in {"stats", "projections"}:
        raise ValueError("source must be 'stats' or 'projections'")


def validate_week_window(start_week: int, end_week: int) -> None:
    if start_week < 1:
        raise ValueError("start_week must be at least 1")
    if end_week < start_week:
        raise ValueError("end_week must be greater than or equal to start_week")


def _query_numeric_rows(
    repository: TrendQueryRepository,
    *,
    source: StatSource,
    season: int,
    start_week: int,
    end_week: int,
    stat_keys: Sequence[str] | None = None,
    player_ids: Sequence[str] | None = None,
    positions: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    validate_stat_source(source)
    stat_key_set = {str(key) for key in stat_keys or []}
    player_id_set = {str(player_id) for player_id in player_ids or []}
    position_set = {str(position).upper() for position in positions or []}
    raw_rows = repository.query_numeric_stat_rows(
        source=source,
        season=season,
        start_week=start_week,
        end_week=end_week,
        stat_keys=list(stat_key_set) or None,
        player_ids=list(player_id_set) or None,
        positions=list(position_set) or None,
    )
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        row = _normalize_graph_row(raw_row)
        if row is None:
            continue
        if int(row["season"]) != int(season):
            continue
        if int(row["week"]) < start_week or int(row["week"]) > end_week:
            continue
        if stat_key_set and str(row["stat_key"]) not in stat_key_set:
            continue
        if player_id_set and str(row["player_id"]) not in player_id_set:
            continue
        if position_set and str(row["position"]).upper() not in position_set:
            continue
        rows.append(row)
    return rows


def _normalize_graph_row(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("stat_value", row.get("value"))
    number = _coerce_number(value)
    if number is None:
        return None
    player_id = str(row.get("player_id") or "")
    stat_key = str(row.get("stat_key") or "")
    if not player_id or not stat_key:
        return None
    return {
        "season": int(row["season"]),
        "week": int(row["week"]),
        "player_id": player_id,
        "name": row.get("name") or row.get("full_name") or player_id,
        "team": row.get("team") or "",
        "position": row.get("position") or "",
        "stat_key": stat_key,
        "stat_value": number,
    }


def _stat_identity(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        int(row["season"]),
        int(row["week"]),
        str(row["player_id"]),
        str(row["stat_key"]),
    )


def _coerce_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return _compact_number(float(value))
    except (TypeError, ValueError):
        return None


def _compact_number(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return round(value, 4)


def _age_seconds(value: Any, now: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return max(0.0, now - float(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            numeric = float(stripped)
            return max(0.0, now - numeric)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, now - parsed.timestamp())
    return None
