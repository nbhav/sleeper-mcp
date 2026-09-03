from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sleeper_tooling.reports import player_name


def calculate_fantasy_points(
    stats: dict[str, Any],
    scoring_settings: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    contributions: dict[str, float] = {}
    total = 0.0

    for stat_key, raw_multiplier in scoring_settings.items():
        stat_value = _number(stats.get(stat_key))
        multiplier = _number(raw_multiplier)
        if stat_value is None or multiplier is None:
            continue
        points = stat_value * multiplier
        if points == 0:
            continue
        contributions[stat_key] = round(points, 4)
        total += points

    return round(total, 2), contributions


def flatten_scored_player_rows(
    rows: Iterable[dict[str, Any]],
    scoring_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        player = row.get("player") or {}
        stats = row.get("stats") or {}
        calculated_points, contributions = calculate_fantasy_points(
            stats,
            scoring_settings,
        )
        sleeper_points = (
            stats.get("pts_ppr")
            or stats.get("pts_half_ppr")
            or stats.get("pts_std")
            or row.get("pts_ppr")
            or row.get("points")
            or 0
        )
        flattened.append(
            {
                "player_id": str(row.get("player_id", "")),
                "name": player.get("full_name")
                or player_name(player, str(row.get("player_id", ""))),
                "team": player.get("team") or "",
                "position": player.get("position") or "",
                "points": calculated_points,
                "sleeper_points": sleeper_points,
                "scoring_rules_matched": len(contributions),
                "scoring_breakdown": contributions,
            }
        )
    return flattened


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
