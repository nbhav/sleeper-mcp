"""Sleeper-only player usage context orchestration.

This module deliberately stops at the normalized repository boundary.  It does
not call Sleeper directly and it does not treat projections as observed usage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sleeper_tooling.player_usage_context_rules import (
    REASON_CODES,
    SCORE_COMPONENT_KEYS,
    TREND_WINDOWS,
    evaluate_player_usage_context,
    trend_window,
)

STAT_ALIASES: dict[str, tuple[str, ...]] = {
    "pass_attempts": ("pass_att", "pass_attempts"),
    "sacks": ("sacks", "sack"),
    "rush_attempts": ("rush_att", "rush_attempts", "carries"),
    "targets": ("rec_tgt", "targets", "target"),
    "receptions": ("rec", "receptions"),
    "pass_yards": ("pass_yd", "pass_yards"),
    "rush_yards": ("rush_yd", "rush_yards"),
    "rec_yards": ("rec_yd", "rec_yards"),
    "pass_tds": ("pass_td", "pass_tds"),
    "rush_tds": ("rush_td", "rush_tds"),
    "rec_tds": ("rec_td", "rec_tds"),
    "def_tds": ("def_td", "def_tds"),
    "takeaways": ("def_int", "interceptions", "fumble_recovery", "fum_rec"),
    "field_goal_attempts": ("fg_att", "field_goal_attempts"),
    "made_50_plus": ("fgm_50_plus", "made_50_plus"),
    "points_allowed": ("pts_allow", "points_allowed"),
}

POSITION_OPPORTUNITY = {
    "QB": "dropbacks",
    "RB": "touches",
    "WR": "targets",
    "TE": "targets",
    "K": "total_kick_attempts",
    "DEF": "sacks",
}

POSITION_MISSING_CONTEXT = {
    "QB": ("pressure", "implied_total"),
    "RB": ("touch_share", "red_zone_use", "nfl_schedule", "implied_total"),
    "WR": ("routes", "target_share", "air_yards", "nfl_schedule"),
    "TE": ("routes", "target_share", "red_zone_use", "nfl_schedule"),
    "K": ("nfl_schedule", "weather", "implied_total"),
    "DEF": ("nfl_schedule", "pressure", "implied_total"),
}


class PlayerUsageContextService:
    """Build one explainable context profile from normalized Sleeper data."""

    def __init__(self, repository: Any, *, provider_registry: Any | None = None) -> None:
        self.repository = getattr(repository, "repository", repository)
        self.provider_registry = provider_registry

    def build(
        self,
        *,
        player_id: str,
        season: int,
        week: int,
        position: str | None = None,
    ) -> dict[str, Any]:
        if season < 1 or week < 1:
            raise ValueError("season and week must be positive")
        player = self._player(player_id)
        resolved_position = (position or player.get("position") or "").upper()
        if resolved_position not in POSITION_OPPORTUNITY:
            raise ValueError("position must be one of QB, RB, WR, TE, K, DEF")

        actual_rows = self._stat_rows(player_id, season, week, "stats")
        projection_rows = self._stat_rows(player_id, season, week, "projections")
        actuals = self._materialize_rows(actual_rows, player, source="stats")
        projections = self._materialize_rows(projection_rows, player, source="projections")
        self._apply_snapshots(actuals, player_id, season)
        self._apply_snapshots(projections, player_id, season)
        actuals = [row for row in actuals if row["week"] <= week]
        projections = [row for row in projections if row["week"] <= week]
        recent = trend_window(actuals, "last_2_weeks")
        earlier = [row for row in actuals if row not in recent]

        inputs = {
            "recent_weeks": recent,
            "season_weeks": earlier,
            "current_metadata": self._metadata_context(player),
        }
        rule_result = evaluate_player_usage_context(resolved_position, inputs)
        reasons = list(rule_result["reasons"])
        missing_inputs = self._missing_context(resolved_position, actuals)
        reasons.extend(self._missing_reasons(missing_inputs))
        reason_codes = list(dict.fromkeys(rule_result["reason_codes"] + [row["code"] for row in reasons]))

        current = actuals[-1] if actuals else (projections[-1] if projections else None)
        projection = projections[-1] if projections else None
        if current and projection and current.get("points") is not None and projection.get("points") is not None:
            current["projection_delta"] = float(projection["points"]) - float(current["points"])
        if current and current.get("projection_delta") is not None:
            for row in recent:
                row["projection_delta"] = current["projection_delta"]

        scores = self._scores(resolved_position, actuals, recent, earlier, reasons, rule_result)
        role = self._role_label(scores, reasons, player)
        snapshots = self._snapshots(player_id, season, week)
        return {
            "schema_version": "player_usage_context.v1",
            "data_source": "sleeper_normalized_data",
            "player_id": str(player_id),
            "name": player.get("full_name") or player.get("player_name") or str(player_id),
            "team": player.get("team"),
            "position": resolved_position,
            "season": season,
            "week": week,
            "actual_first": True,
            "role_label": role,
            "scores": scores,
            **scores,
            "modifiers": rule_result.get("capped_modifiers", {}),
            "reason_codes": reason_codes,
            "reasons": reasons,
            "missing_inputs": missing_inputs,
            "depth_chart": self._metadata_context(player),
            "role_snapshots": snapshots["role"],
            "availability_snapshots": snapshots["availability"],
            "windows": self._windows(actuals, projections),
            "evidence": {
                "recent_actual_weeks": [row["week"] for row in recent],
                "season_actual_weeks": [row["week"] for row in actuals],
                "baseline_weeks": [row["week"] for row in earlier],
                "projection_weeks": [row["week"] for row in projections],
                "actual_rows": actuals,
                "projection_rows": projections,
            },
        }

    def _player(self, player_id: str) -> dict[str, Any]:
        getter = getattr(self.repository, "get_player", None)
        player = getter(str(player_id)) if callable(getter) else None
        return dict(player or {"player_id": str(player_id)})

    def _stat_rows(self, player_id: str, season: int, week: int, source: str) -> list[dict[str, Any]]:
        query = getattr(self.repository, "query_numeric_stat_rows", None)
        if not callable(query):
            return []
        return list(query(source=source, season=season, start_week=1, end_week=week, player_ids=[str(player_id)]))

    def _materialize_rows(self, rows: Iterable[Mapping[str, Any]], player: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = defaultdict(dict)
        for raw in rows:
            week = int(raw["week"])
            grouped[week].update({"week": week, "player_id": str(raw.get("player_id")), "team": raw.get("team") or player.get("team"), "position": raw.get("position") or player.get("position"), "source": source})
            grouped[week].setdefault("stats", {})[str(raw["stat_key"])] = float(raw["stat_value"])
        result = []
        for week, row in sorted(grouped.items()):
            stats = row.pop("stats", {})
            for field, aliases in STAT_ALIASES.items():
                values = [stats[key] for key in aliases if key in stats]
                if values:
                    row[field] = sum(values) if field == "takeaways" else values[0]
            row["dropbacks"] = (row.get("pass_attempts") or 0) + (row.get("sacks") or 0) if row.get("pass_attempts") is not None or row.get("sacks") is not None else None
            row["touches"] = (row.get("rush_attempts") or 0) + (row.get("targets") or 0) if row.get("rush_attempts") is not None or row.get("targets") is not None else None
            row["total_kick_attempts"] = (row.get("field_goal_attempts") or 0) + (stats.get("extra_point_attempts") or stats.get("xpa") or 0) if row.get("field_goal_attempts") is not None or "extra_point_attempts" in stats or "xpa" in stats else None
            row["total_tds"] = sum(row.get(key) or 0 for key in ("rush_tds", "rec_tds"))
            row["def_or_special_teams_tds"] = row.get("def_tds") or 0
            row["points"] = stats.get("points", stats.get("fantasy_points", stats.get("pts_ppr")))
            if row["points"] is None:
                row["points"] = 0.0
            row["active"] = True
            row["bye"] = False
            result.append(row)
        return result

    def _apply_snapshots(self, rows: list[dict[str, Any]], player_id: str, season: int) -> None:
        for row in rows:
            week = row["week"]
            availability = self._list("list_player_week_availability", season=season, week=week, player_id=player_id)
            status = str((availability[0] if availability else {}).get("status") or "").lower()
            injury = str((availability[0] if availability else {}).get("injury_status") or "").lower()
            if status in {"out", "inactive", "ir", "reserve"} or injury in {"out", "ir"}:
                row["active"] = False
            schedule = self._list_one("get_team_week_schedule", season=season, week=week, team=row.get("team") or "")
            if schedule and schedule.get("is_bye"):
                row["bye"] = True

    def _metadata_context(self, player: Mapping[str, Any]) -> dict[str, Any]:
        return {key: player.get(key) for key in ("depth_chart_order", "depth_chart_position", "status", "injury_status") if player.get(key) is not None} | {"depth_chart_role": player.get("depth_chart_position") or player.get("depth_chart_order")}

    def _snapshots(self, player_id: str, season: int, week: int) -> dict[str, list[dict[str, Any]]]:
        return {"role": self._list("list_player_role_snapshots", season=season, week=week, player_id=player_id), "availability": self._list("list_player_week_availability", season=season, week=week, player_id=player_id)}

    def _list(self, name: str, **kwargs: Any) -> list[dict[str, Any]]:
        method = getattr(self.repository, name, None)
        return list(method(**kwargs)) if callable(method) else []

    def _list_one(self, name: str, **kwargs: Any) -> dict[str, Any] | None:
        method = getattr(self.repository, name, None)
        return method(**kwargs) if callable(method) else None

    def _missing_context(self, position: str, rows: list[dict[str, Any]]) -> list[str]:
        missing = list(POSITION_MISSING_CONTEXT[position])
        if not rows:
            missing.append("actual_stats")
        return sorted(set(missing))

    def _missing_reasons(self, inputs: Iterable[str]) -> list[dict[str, Any]]:
        output = []
        for value in inputs:
            code = "not_evaluable_missing_" + value
            definition = REASON_CODES.get(code, {"component": "context_confidence", "polarity": "neutral", "computability_tier": "not_evaluable_missing_source", "evidence": [], "severity": "medium", "description": f"{value} is unavailable in Sleeper-only mode", "missing_inputs": [value]})
            output.append({"code": code, **{key: definition[key] for key in ("component", "polarity", "computability_tier", "evidence", "severity", "description", "missing_inputs")}})
        return output

    def _windows(self, actuals: list[dict[str, Any]], projections: list[dict[str, Any]]) -> dict[str, Any]:
        return {name: {"actual": trend_window(actuals, name), "projection": trend_window(projections, name), "sample_size": len(trend_window(actuals, name))} for name in TREND_WINDOWS}

    def _scores(self, position: str, actuals: list[dict[str, Any]], recent: list[dict[str, Any]], baseline: list[dict[str, Any]], reasons: list[dict[str, Any]], rule_result: Mapping[str, Any]) -> dict[str, float]:
        metric = POSITION_OPPORTUNITY[position]
        recent_value = _average(recent, metric)
        baseline_value = _average(baseline, metric)
        opportunity = min(100.0, max(0.0, (recent_value or 0) * 5.0))
        stability = max(0.0, min(100.0, 100.0 - (_relative_spread(recent, metric) * 100.0))) if recent else 0.0
        points = _average(recent, "points") or 0
        td = _average(recent, "rush_tds") or 0
        td += (_average(recent, "rec_tds") or 0) + (_average(recent, "pass_tds") or 0) + (_average(recent, "def_tds") or 0)
        td_dependency = min(100.0, max(0.0, td * 20.0 / max(points, 1.0)))
        trend_mod = float(rule_result.get("capped_modifiers", {}).get("trend", 0))
        context_mod = float(rule_result.get("capped_modifiers", {}).get("context", 0))
        return {"opportunity_score": round(opportunity, 2), "role_stability_score": round(stability, 2), "production_quality_score": round(max(0.0, min(100.0, points * 4.0 - td_dependency * 0.25)), 2), "td_dependency_score": round(td_dependency, 2), "depth_chart_confidence": 45.0 if any(reason["code"] == "current_metadata_only_depth_chart" for reason in reasons) else 60.0, "matchup_adjustment": float(rule_result.get("capped_modifiers", {}).get("matchup", 0)), "small_sample_risk": round(max(0.0, 100.0 - len(recent) * 35.0), 2), "one_off_risk": round(min(100.0, max(0.0, -context_mod * 12.5)), 2), "trend_change_score": round(max(0.0, min(100.0, 50.0 + trend_mod * 12.5)), 2), "season_context_score": round(max(0.0, min(100.0, 50.0 + ((_ratio(recent_value, baseline_value) or 0) * 25.0)), 2), "context_confidence": round(max(0.0, 100.0 - len(self._missing_context(position, actuals)) * 7.0 - (20.0 if len(actuals) < 2 else 0.0)), 2)}

    def _role_label(self, scores: Mapping[str, float], reasons: list[dict[str, Any]], player: Mapping[str, Any]) -> str:
        codes = {reason["code"] for reason in reasons}
        if "role_loss_recent" in codes:
            return "role_declining"
        if "role_change_recent" in codes:
            return "emerging_rotation"
        if player.get("depth_chart_order") == 1:
            return "established_role"
        return "uncertain_role"


def build_player_usage_context(repository: Any, **kwargs: Any) -> dict[str, Any]:
    return PlayerUsageContextService(repository).build(**kwargs)


def _average(rows: Iterable[Mapping[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def _relative_spread(rows: Iterable[Mapping[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values or sum(values) == 0:
        return 1.0
    mean = sum(values) / len(values)
    return sum(abs(value - mean) for value in values) / len(values) / abs(mean)


def _ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return (left - right) / abs(right)
