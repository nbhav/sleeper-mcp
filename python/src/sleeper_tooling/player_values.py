from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sleeper_tooling.reports import player_name
from sleeper_tooling.scoring import calculate_fantasy_points

K_DEF_POSITIONS = {"K", "DEF"}

DEFAULT_REPLACEMENT_BASELINES: dict[str, dict[str, float]] = {
    "QB": {"week": 14.0, "three_week": 14.0, "season": 14.0},
    "RB": {"week": 8.0, "three_week": 8.0, "season": 8.0},
    "WR": {"week": 8.0, "three_week": 8.0, "season": 8.0},
    "TE": {"week": 6.0, "three_week": 6.0, "season": 6.0},
    "K": {"week": 7.0, "three_week": 7.0, "season": 7.0},
    "DEF": {"week": 7.0, "three_week": 7.0, "season": 7.0},
}


def build_player_value(
    *,
    player_id: str,
    player: dict[str, Any] | None = None,
    projection_row: dict[str, Any] | None = None,
    stat_row: dict[str, Any] | None = None,
    recent_rows: Sequence[dict[str, Any]] | None = None,
    scoring_settings: dict[str, Any] | None = None,
    replacement_baselines: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic per-week value profile for one player."""

    player_id = str(player_id)
    player = _merge_player_context(player, projection_row, stat_row)
    projection_score = _score_row(projection_row, scoring_settings, source_name="sleeper_projections")
    stat_score = _score_row(stat_row, scoring_settings, source_name="sleeper_stats")
    recent_scores = [
        score
        for score in (
            _score_row(row, scoring_settings, source_name="recent_rows")
            for row in (recent_rows or [])
        )
        if score["points"] is not None
    ]

    selected_week_score = projection_score if projection_score["points"] is not None else stat_score
    raw_week_value = float(selected_week_score["points"] or 0)
    recent_average = _average([float(score["points"]) for score in recent_scores])
    raw_three_week_value = _near_term_value(raw_week_value, recent_average, bool(selected_week_score["points"] is not None))
    raw_season_value = _season_value(
        raw_week_value=raw_week_value,
        recent_average=recent_average,
        has_week_score=selected_week_score["points"] is not None,
        player=player,
    )

    availability = availability_adjustments(player)
    week_value = round(raw_week_value * availability["week"], 2)
    three_week_value = round(raw_three_week_value * availability["three_week"], 2)
    season_value = round(raw_season_value * availability["season"], 2)

    position = str(
        player.get("position")
        or (projection_row or {}).get("position")
        or (stat_row or {}).get("position")
        or ""
    ).upper()
    fantasy_positions = _fantasy_positions(player, projection_row, stat_row, position)
    replacement_value = replacement_profile(position, replacement_baselines)
    value_above_replacement = {
        "week": round(week_value - replacement_value["week"], 2),
        "three_week": round(three_week_value - replacement_value["three_week"], 2),
        "season": round(season_value - replacement_value["season"], 2),
    }

    decision_value = round((week_value * 0.2) + (three_week_value * 0.35) + (season_value * 0.45), 2)
    value_tier, role_tag, decision_value = tier_player(
        position=position,
        week_value=week_value,
        three_week_value=three_week_value,
        season_value=season_value,
        decision_value=decision_value,
        replacement_value=replacement_value,
        player=player,
        recent_score_count=len(recent_scores),
        availability=availability,
    )
    value_above_replacement["decision"] = round(decision_value - replacement_value["decision"], 2)

    context_sources = _context_sources(
        player=player,
        projection_score=projection_score,
        stat_score=stat_score,
        recent_scores=recent_scores,
        scoring_settings=scoring_settings,
        replacement_baselines=replacement_baselines,
    )
    metadata = {
        "week_score": _score_metadata(selected_week_score),
        "projection_score": _score_metadata(projection_score),
        "stat_score": _score_metadata(stat_score),
        "recent_score_count": len(recent_scores),
        "recent_average": round(recent_average, 2) if recent_average is not None else None,
        "availability": availability,
        "replacement_baseline": replacement_value,
        "missing_context": _missing_context(selected_week_score, recent_scores),
    }

    return {
        "player_id": player_id,
        "name": player_name(player, player_id),
        "team": player.get("team") or (projection_row or {}).get("team") or (stat_row or {}).get("team") or "",
        "position": position,
        "fantasy_positions": fantasy_positions,
        "week_value": week_value,
        "three_week_value": three_week_value,
        "season_value": season_value,
        "replacement_value": replacement_value,
        "value_above_replacement": value_above_replacement,
        "decision_value": decision_value,
        "value_tier": value_tier,
        "role_tag": role_tag,
        "scoring_source": selected_week_score["scoring_source"],
        "context_sources": context_sources,
        "source_metadata": metadata,
    }


def build_player_values(
    *,
    players: Mapping[str, dict[str, Any]],
    projection_rows: Iterable[dict[str, Any]] = (),
    stat_rows: Iterable[dict[str, Any]] = (),
    recent_rows_by_player: Mapping[str, Sequence[dict[str, Any]]] | None = None,
    scoring_settings: dict[str, Any] | None = None,
    replacement_baselines: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build and rank deterministic value profiles for many players."""

    projections_by_player = _rows_by_player(projection_rows)
    stats_by_player = _rows_by_player(stat_rows)
    player_ids = sorted(set(players) | set(projections_by_player) | set(stats_by_player))
    values = [
        build_player_value(
            player_id=player_id,
            player=players.get(player_id),
            projection_row=projections_by_player.get(player_id),
            stat_row=stats_by_player.get(player_id),
            recent_rows=(recent_rows_by_player or {}).get(player_id, ()),
            scoring_settings=scoring_settings,
            replacement_baselines=replacement_baselines,
        )
        for player_id in player_ids
    ]
    return sorted(
        values,
        key=lambda row: (
            float(row.get("decision_value") or 0),
            float(row.get("three_week_value") or 0),
            float(row.get("season_value") or 0),
        ),
        reverse=True,
    )


def replacement_profile(
    position: str,
    replacement_baselines: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    position = position.upper()
    default = DEFAULT_REPLACEMENT_BASELINES.get(position, {"week": 0.0, "three_week": 0.0, "season": 0.0})
    configured = (replacement_baselines or {}).get(position) or (replacement_baselines or {}).get(position.lower())
    if isinstance(configured, int | float | str):
        number = _number(configured)
        if number is not None:
            profile = {"week": number, "three_week": number, "season": number}
        else:
            profile = dict(default)
    elif isinstance(configured, Mapping):
        profile = {
            "week": _configured_number(configured, "week", default["week"]),
            "three_week": _configured_number(configured, "three_week", default["three_week"]),
            "season": _configured_number(configured, "season", default["season"]),
        }
    else:
        profile = dict(default)
    profile["decision"] = round((profile["week"] * 0.2) + (profile["three_week"] * 0.35) + (profile["season"] * 0.45), 2)
    return profile


def availability_adjustments(player: dict[str, Any]) -> dict[str, Any]:
    status = str(player.get("status") or "").strip().lower()
    injury_status = str(player.get("injury_status") or "").strip().lower()
    combined = " ".join(part for part in [status, injury_status] if part)

    factor = {"week": 1.0, "three_week": 1.0, "season": 1.0, "tag": "available"}
    if any(term in combined for term in ["questionable", "game time", "gametime"]):
        factor = {"week": 0.85, "three_week": 0.9, "season": 0.95, "tag": "questionable"}
    if "doubtful" in combined:
        factor = {"week": 0.35, "three_week": 0.55, "season": 0.75, "tag": "doubtful"}
    if any(term in combined for term in ["out", "inactive", "suspended"]):
        factor = {"week": 0.0, "three_week": 0.35, "season": 0.65, "tag": "out"}
    if any(term in combined for term in ["injured reserve", "ir", "pup"]):
        factor = {"week": 0.0, "three_week": 0.2, "season": 0.55, "tag": "reserve_stash"}

    return {
        **factor,
        "status": player.get("status") or "",
        "injury_status": player.get("injury_status") or "",
    }


def tier_player(
    *,
    position: str,
    week_value: float,
    three_week_value: float,
    season_value: float,
    decision_value: float,
    replacement_value: Mapping[str, float],
    player: dict[str, Any],
    recent_score_count: int,
    availability: Mapping[str, Any],
) -> tuple[str, str, float]:
    if position in K_DEF_POSITIONS:
        return _tier_k_def(
            week_value=week_value,
            three_week_value=three_week_value,
            season_value=season_value,
            decision_value=decision_value,
            replacement_value=replacement_value,
            player=player,
            recent_score_count=recent_score_count,
        )

    if availability.get("tag") in {"reserve_stash", "out", "doubtful", "questionable"}:
        role_tag = "injury_risk" if availability.get("tag") != "reserve_stash" else "stash"
    elif decision_value >= replacement_value["decision"] + 5:
        role_tag = "starter"
    elif decision_value >= replacement_value["decision"] + 1:
        role_tag = "depth"
    else:
        role_tag = "replacement_depth"

    if decision_value >= replacement_value["decision"] + 7:
        value_tier = "elite"
    elif decision_value >= replacement_value["decision"] + 3:
        value_tier = "strong_starter"
    elif decision_value >= replacement_value["decision"]:
        value_tier = "depth"
    else:
        value_tier = "replacement_level"
    return value_tier, role_tag, decision_value


def _tier_k_def(
    *,
    week_value: float,
    three_week_value: float,
    season_value: float,
    decision_value: float,
    replacement_value: Mapping[str, float],
    player: dict[str, Any],
    recent_score_count: int,
) -> tuple[str, str, float]:
    rostered_pct = _rostered_percent(player) or 0.0
    recurring_signal = recent_score_count > 0 or rostered_pct >= 55
    baseline = replacement_value["decision"]

    if recurring_signal and rostered_pct >= 80 and season_value >= replacement_value["season"] + 2:
        return "elite_hold", "elite_hold", max(decision_value, baseline + 3)
    if recurring_signal and (
        three_week_value >= replacement_value["three_week"] + 1.5
        or season_value >= replacement_value["season"] + 1.5
    ):
        return "strong_weekly_play", "strong_weekly_play", max(decision_value, baseline + 1.5)
    if week_value >= replacement_value["week"]:
        capped = min(decision_value, baseline + 1.0)
        return "streamer", "streamer", round(capped, 2)
    return "replacement_level", "replacement_level", min(decision_value, baseline)


def _score_row(
    row: dict[str, Any] | None,
    scoring_settings: dict[str, Any] | None,
    *,
    source_name: str,
) -> dict[str, Any]:
    if not row:
        return {"points": None, "scoring_source": "missing", "source_name": source_name}

    stats = row.get("stats") if isinstance(row.get("stats"), dict) else row
    if scoring_settings and isinstance(stats, dict):
        calculated_points, contributions = calculate_fantasy_points(stats, scoring_settings)
        if contributions or _has_scoreable_stat(stats, scoring_settings):
            return {
                "points": calculated_points,
                "scoring_source": "league_scoring",
                "source_name": source_name,
                "scoring_rules_matched": len(contributions),
                "scoring_breakdown": contributions,
            }

    sleeper_points = _first_number(
        row,
        "points",
        "pts_ppr",
        "pts_half_ppr",
        "pts_std",
        "projected_points",
        "actual_points",
    )
    if sleeper_points is None and isinstance(row.get("stats"), dict):
        sleeper_points = _first_number(row["stats"], "pts_ppr", "pts_half_ppr", "pts_std", "points")
    if sleeper_points is None:
        return {"points": None, "scoring_source": "missing", "source_name": source_name}
    return {
        "points": sleeper_points,
        "scoring_source": "sleeper_points",
        "source_name": source_name,
        "scoring_rules_matched": 0,
        "scoring_breakdown": {},
    }


def _season_value(
    *,
    raw_week_value: float,
    recent_average: float | None,
    has_week_score: bool,
    player: dict[str, Any],
) -> float:
    if has_week_score and recent_average is not None:
        base = (raw_week_value * 0.55) + (recent_average * 0.45)
    elif has_week_score:
        base = raw_week_value
    elif recent_average is not None:
        base = recent_average
    else:
        base = 0.0

    position = str(player.get("position") or "").upper()
    rostered_pct = _rostered_percent(player)
    depth_order = _number(player.get("depth_chart_order"))
    if base <= 0 and rostered_pct is None and depth_order is None:
        return 0.0

    if rostered_pct is not None:
        rostered_boost = min(3.0, rostered_pct / 35)
        if position in K_DEF_POSITIONS:
            rostered_boost = min(2.0, rostered_pct / 55)
        base += rostered_boost

    if depth_order is not None:
        if depth_order <= 1:
            base += 0.75
        elif depth_order >= 3:
            base -= min(1.5, (depth_order - 2) * 0.5)

    if position == "RB":
        base += 0.6
    elif position in {"WR", "TE"}:
        base += 0.4
    elif position in K_DEF_POSITIONS:
        base -= 0.5
    return max(base, 0.0)


def _near_term_value(raw_week_value: float, recent_average: float | None, has_week_score: bool) -> float:
    if has_week_score and recent_average is not None:
        return (raw_week_value * 0.65) + (recent_average * 0.35)
    if has_week_score:
        return raw_week_value
    if recent_average is not None:
        return recent_average
    return 0.0


def _merge_player_context(
    player: dict[str, Any] | None,
    projection_row: dict[str, Any] | None,
    stat_row: dict[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for row in (stat_row, projection_row):
        nested = (row or {}).get("player")
        if isinstance(nested, dict):
            merged.update(nested)
    if player:
        merged.update(player)
    return merged


def _context_sources(
    *,
    player: dict[str, Any],
    projection_score: Mapping[str, Any],
    stat_score: Mapping[str, Any],
    recent_scores: Sequence[Mapping[str, Any]],
    scoring_settings: dict[str, Any] | None,
    replacement_baselines: Mapping[str, Any] | None,
) -> list[str]:
    sources = []
    if player:
        sources.append("sleeper_players")
    if projection_score.get("points") is not None:
        sources.append("sleeper_projections")
    if stat_score.get("points") is not None:
        sources.append("sleeper_stats")
    if recent_scores:
        sources.append("recent_rows")
    if scoring_settings:
        sources.append("league_scoring_settings")
    if replacement_baselines:
        sources.append("replacement_baselines")
    return sources


def _missing_context(selected_week_score: Mapping[str, Any], recent_scores: Sequence[Mapping[str, Any]]) -> list[str]:
    missing = []
    if selected_week_score.get("points") is None:
        missing.append("week_projection_or_stat")
    if not recent_scores:
        missing.append("recent_rows")
    return missing


def _score_metadata(score: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": score.get("source_name"),
        "scoring_source": score.get("scoring_source"),
        "points": score.get("points"),
        "scoring_rules_matched": score.get("scoring_rules_matched", 0),
        "scoring_breakdown": score.get("scoring_breakdown", {}),
    }


def _fantasy_positions(
    player: Mapping[str, Any],
    projection_row: Mapping[str, Any] | None,
    stat_row: Mapping[str, Any] | None,
    position: str,
) -> list[str]:
    for source in (player, projection_row or {}, stat_row or {}):
        values = source.get("fantasy_positions")
        if isinstance(values, list):
            return [str(value) for value in values]
    return [position] if position else []


def _rows_by_player(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("player_id")): row
        for row in rows
        if row.get("player_id") not in (None, "")
    }


def _average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rostered_percent(player: Mapping[str, Any]) -> float | None:
    for key in ("rostered_percent", "rostered_pct", "percent_rostered"):
        value = _number(player.get(key))
        if value is not None:
            return value
    return None


def _first_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _configured_number(configured: Mapping[str, Any], key: str, default: float) -> float:
    value = _number(configured.get(key))
    return default if value is None else value


def _has_scoreable_stat(stats: Mapping[str, Any], scoring_settings: Mapping[str, Any]) -> bool:
    return any(key in stats and _number(stats.get(key)) is not None for key in scoring_settings)


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
