"""Deterministic NFL opponent context for player/week decisions.

This module deliberately keeps NFL matchup context separate from fantasy-league
matchups.  It consumes normalized player rows and the normalized team schedule;
provider fields are optional and never inferred from fantasy points.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

MATCHUP_MODEL_VERSION = "matchup.v1"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
CAPS = {"QB": 2.0, "RB": 2.0, "WR": 2.0, "TE": 2.0, "K": 2.0, "DEF": 3.0}

_ALIASES = {
    "fantasy_points": ("fantasy_points", "sleeper_points", "points", "pts_ppr"),
    "rush_yd": ("rush_yd", "rushing_yards"),
    "rush_td": ("rush_td", "rushing_touchdowns"),
    "rec": ("rec", "receptions"),
    "rec_yd": ("rec_yd", "receiving_yards"),
    "rec_td": ("rec_td", "receiving_touchdowns"),
    "targets": ("targets", "rec_tgt", "targets_total"),
    "pass_att": ("pass_att", "passing_attempts"),
    "pass_int": ("pass_int", "interceptions", "pass_interceptions"),
    "pass_sack": ("pass_sack", "pass_sacks", "sacks_taken"),
    "sack": ("sack", "sacks", "def_sacks"),
    "fum_lost": ("fum_lost", "fumbles_lost"),
    "fg_att": ("fg_att", "field_goals_attempted"),
    "xpa": ("xpa", "extra_points_attempted", "pat_att"),
    "def_td": ("def_td", "defensive_touchdowns", "def_st_td", "special_teams_td"),
}


def build_matchup_profile(
    *,
    player_id: str,
    season: int,
    week: int,
    player: Mapping[str, Any],
    player_stats: Mapping[str, Any] | None = None,
    opponent: str | None = None,
    schedule_available: bool = True,
    opponent_rows: Sequence[Mapping[str, Any]] = (),
    league_rows: Sequence[Mapping[str, Any]] = (),
    provider_context: Mapping[str, Any] | None = None,
    availability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable JSON contract for one player's matchup context."""
    position = str(player.get("position") or "").upper()
    supported_position = position in POSITIONS
    stats = _stats(player, player_stats)
    provider = dict(provider_context or {})
    missing: list[str] = []
    reasons: list[str] = []
    source_availability = {
        "normalized_player_stats": bool(stats),
        "nfl_schedule": bool(schedule_available and opponent),
        "historical_opponent_stats": bool(opponent_rows),
        "enriched_provider": bool(provider),
        "weekly_availability": availability is not None,
    }

    if not schedule_available or not opponent:
        missing.append("not_evaluable_missing_nfl_schedule")
    if not opponent_rows:
        missing.append("not_evaluable_missing_opponent_position_stats")
    if availability is None:
        missing.append("not_evaluable_missing_weekly_availability")
    if not supported_position:
        missing.append("unsupported_position")

    opponent_games = _aggregate_game_rows(opponent_rows)
    league_games = _aggregate_game_rows(league_rows)
    features = _features(position, opponent_games) if supported_position else {}
    league_features = _features(position, league_games) if supported_position else {}
    modifier = 0.0
    evidence: dict[str, Any] = {
        "position": position,
        "opponent": opponent,
        "historical_games": len(opponent_games),
        "opponent_features": features,
    }
    if features and league_features and source_availability["nfl_schedule"]:
        opponent_rate = float(features["points_allowed_per_game"])
        league_rate = float(league_features["points_allowed_per_game"])
        if league_rate > 0:
            modifier = _cap((opponent_rate / league_rate - 1.0) * 2.0, position)
            reasons.append(
                "opponent_allows_above_average_points"
                if modifier > 0
                else "opponent_allows_below_average_points"
                if modifier < 0
                else "opponent_points_allowed_neutral"
            )
            evidence["league_average_points_allowed_per_game"] = round(league_rate, 3)

    required_provider_inputs = {
        "QB": ("pressure",),
        "RB": ("goal_line_allowance",),
        "WR": ("explosive_plays", "target_efficiency"),
        "TE": ("explosive_plays", "target_efficiency"),
        "K": ("weather", "implied_totals"),
        "DEF": ("implied_totals", "pressure"),
    }.get(position, ())
    for input_name in required_provider_inputs:
        if input_name not in provider or provider[input_name] is None:
            missing.append(f"not_evaluable_missing_{input_name}")
    if provider:
        evidence["provider_context"] = provider
    if availability is not None:
        evidence["weekly_availability"] = dict(availability)
    if not source_availability["historical_opponent_stats"]:
        reasons.append("opponent_position_stats_unavailable")
    if not supported_position:
        reasons.append("unsupported_position")
    if missing:
        reasons.extend(code for code in missing if code == "not_evaluable_missing_nfl_schedule")

    # A schedule join is a hard prerequisite. Provider data may add evidence,
    # but must not manufacture a matchup score without it.
    if not source_availability["nfl_schedule"] or not supported_position:
        modifier = 0.0
    return {
        "model_version": MATCHUP_MODEL_VERSION,
        "season": int(season),
        "week": int(week),
        "player_id": str(player_id),
        "position": position,
        "team": player.get("team"),
        "opponent": opponent,
        "home_away": player.get("home_away"),
        "availability": dict(availability) if availability is not None else None,
        "matchup_adjustment": round(_cap(modifier, position), 3),
        "matchup_cap": CAPS.get(position),
        "source_availability": source_availability,
        "missing_inputs": sorted(set(missing)),
        "reason_codes": sorted(set(reasons)),
        "evidence": evidence,
    }


def build_repository_matchup_profile(
    repository: Any,
    *,
    player_id: str,
    season: int,
    week: int,
    source: str = "stats",
    provider_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load normalized rows and join the player's team to NFL schedule data."""
    player_rows = repository.list_player_week_rows(
        season=season, week=week, source=source, player_id=player_id
    )
    if not player_rows:
        return _missing_source_profile(
            player_id=player_id, season=season, week=week, source=source
        )
    player = player_rows[0]
    team = str(player.get("team") or "").upper()
    schedule = repository.get_team_week_schedule(season=season, week=week, team=team)
    opponent = (schedule or {}).get("opponent")
    opponent_rows: list[dict[str, Any]] = []
    league_rows: list[dict[str, Any]] = []
    for historical_week in range(1, int(week) + 1):
        rows = repository.list_player_week_rows(
            season=season, week=historical_week, source=source
        )
        for row in rows:
            if str(row.get("player_id") or "") == str(player_id):
                continue
            row_position = str(row.get("position") or "").upper()
            if row_position != str(player.get("position") or "").upper():
                continue
            row_opponent = _row_opponent(row)
            if row_opponent == str(opponent or "").upper():
                opponent_rows.append(row)
            if row_opponent:
                league_rows.append(row)
    stats_rows = repository.list_player_week_stat_values(
        season=season, week=week, source=source, player_id=player_id
    )
    stats = {str(row["stat_key"]): row.get("stat_value") for row in stats_rows}
    availability_rows = []
    list_availability = getattr(repository, "list_player_week_availability", None)
    if callable(list_availability):
        availability_rows = list_availability(
            season=season, week=week, player_id=player_id
        )
    availability = availability_rows[0] if availability_rows else None
    return build_matchup_profile(
        player_id=player_id,
        season=season,
        week=week,
        player=player,
        player_stats=stats,
        opponent=opponent,
        schedule_available=schedule is not None,
        opponent_rows=opponent_rows,
        league_rows=league_rows,
        provider_context=provider_context,
        availability=availability,
    )


def _missing_source_profile(*, player_id: str, season: int, week: int, source: str) -> dict[str, Any]:
    return {
        "model_version": MATCHUP_MODEL_VERSION,
        "season": int(season),
        "week": int(week),
        "player_id": str(player_id),
        "position": None,
        "team": None,
        "opponent": None,
        "home_away": None,
        "availability": None,
        "matchup_adjustment": 0.0,
        "matchup_cap": None,
        "source_availability": {
            "normalized_player_stats": False,
            "nfl_schedule": False,
            "historical_opponent_stats": False,
            "enriched_provider": False,
            "weekly_availability": False,
        },
        "missing_inputs": [f"not_evaluable_missing_normalized_{source}_row"],
        "reason_codes": ["normalized_player_source_unavailable"],
        "evidence": {"source": source},
    }


def _row_opponent(row: Mapping[str, Any]) -> str:
    for candidate in (row, _mapping_value(row, "raw"), _mapping_value(row, "player"), _mapping_value(row, "stats")):
        for key in ("opponent", "opp", "row_opponent"):
            value = candidate.get(key) if isinstance(candidate, Mapping) else None
            if value:
                return str(value).strip().upper()
    return ""


def _mapping_value(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = row.get(key)
    return value if isinstance(value, Mapping) else {}


def _stats(row: Mapping[str, Any], explicit: Mapping[str, Any] | None) -> dict[str, float]:
    values: dict[str, Any] = {}
    values.update(row)
    raw = row.get("stats")
    if isinstance(raw, Mapping):
        values.update(raw)
    for nested_row in (row.get("raw"), row.get("player_json")):
        if not isinstance(nested_row, Mapping):
            continue
        values.update(nested_row)
        nested_stats = nested_row.get("stats")
        if isinstance(nested_stats, Mapping):
            values.update(nested_stats)
    values.update(explicit or {})
    return {key: _number(_first(values, aliases)) for key, aliases in _ALIASES.items() if _first(values, aliases) is not None}


def _features(position: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    points = [_stats(row, None).get("fantasy_points", _number(row.get("fantasy_points"))) for row in rows]
    points = [value for value in points if value is not None]
    if not points:
        return {}
    feature: dict[str, float] = {
        "points_allowed_per_game": round(sum(points) / len(points), 3),
        "sample_size": float(len(points)),
    }
    if position == "RB":
        feature["rushing_points_per_game"] = round(sum(_rushing_points(row) for row in rows) / len(rows), 3)
        feature["receiving_points_per_game"] = round(sum(_receiving_points(row) for row in rows) / len(rows), 3)
    elif position in {"WR", "TE"}:
        feature["targets_per_game"] = round(sum(_stats(row, None).get("targets", 0.0) for row in rows) / len(rows), 3)
    elif position == "QB":
        feature["pass_sacks_per_game"] = round(sum(_stats(row, None).get("pass_sack", 0.0) for row in rows) / len(rows), 3)
    return feature


def _aggregate_game_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse player rows to one positional total per game/week."""
    grouped: dict[str, dict[str, Any]] = {}
    stat_keys = ("fantasy_points", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "targets", "pass_sack", "sack")
    for row in rows:
        metadata = _row_metadata(row)
        key = str(metadata.get("game_id") or metadata.get("week") or len(grouped))
        aggregate = grouped.setdefault(key, metadata)
        stats = _stats(row, None)
        for stat_key in stat_keys:
            value = stats.get(stat_key)
            if value is not None:
                aggregate[stat_key] = float(aggregate.get(stat_key, 0.0)) + float(value)
    return list(grouped.values())


def _row_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {key: row.get(key) for key in ("game_id", "week")}
    for nested_key in ("stats", "raw", "player_json"):
        nested = row.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        for key in metadata:
            if metadata[key] is None and nested.get(key) is not None:
                metadata[key] = nested[key]
    return metadata


def _rushing_points(row: Mapping[str, Any]) -> float:
    stats = _stats(row, None)
    return stats.get("rush_yd", 0.0) * 0.1 + stats.get("rush_td", 0.0) * 6.0


def _receiving_points(row: Mapping[str, Any]) -> float:
    stats = _stats(row, None)
    return stats.get("rec_yd", 0.0) * 0.1 + stats.get("rec_td", 0.0) * 6.0 + stats.get("rec", 0.0)


def _first(values: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for alias in aliases:
        if alias in values and values[alias] is not None:
            return values[alias]
    return None


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _cap(value: float, position: str) -> float:
    return max(-CAPS.get(position, 2.0), min(CAPS.get(position, 2.0), float(value)))


__all__ = ["CAPS", "MATCHUP_MODEL_VERSION", "build_matchup_profile", "build_repository_matchup_profile"]
