"""Sleeper-only player usage context orchestration.

This module deliberately stops at the normalized repository boundary.  It does
not call Sleeper directly and it does not treat projections as observed usage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sleeper_tooling.player_usage_context_rules import (
    INJURY_REPLACEMENT_POSITIONS,
    PLAYER_USAGE_CONTEXT_SCHEMA_VERSION,
    POSITION_USAGE_THRESHOLDS,
    POST_CHANGE_WINDOW,
    REASON_CODES,
    SCORE_COMPONENT_KEYS,
    TREND_WINDOWS,
    compute_derived_metrics,
    compute_td_dependency,
    detect_change_point,
    evaluate_player_usage_context,
    post_change_rows,
    td_dependency_score,
    trend_window,
)

# Exact canonical Sleeper stat keys for the row fields the rules read.  Each
# field reads exactly one key; there are no fallback aliases and nothing is
# summed implicitly.  Sums of distinct canonical stats are built explicitly in
# ``_materialize_rows`` (for example DEF ``takeaways = int + fum_rec``).
STAT_KEYS: dict[str, str] = {
    "pass_attempts": "pass_att",
    "rush_attempts": "rush_att",
    "targets": "rec_tgt",
    "receptions": "rec",
    "pass_yards": "pass_yd",
    "rush_yards": "rush_yd",
    "rec_yards": "rec_yd",
    "pass_tds": "pass_td",
    "rush_tds": "rush_td",
    "rec_tds": "rec_td",
    "field_goal_attempts": "fga",
    "made_50_plus": "fgm_50p",
}
# Sack keys are position-aware: QB rows report sacks taken as ``pass_sack``
# (used for dropbacks), team DEF rows report sacks made as ``sack``.
POSITION_STAT_KEYS: dict[str, dict[str, str]] = {
    "QB": {"sacks_taken": "pass_sack"},
    "DEF": {"sacks": "sack", "def_tds": "def_td", "points_allowed": "pts_allow"},
}
# Explicit sums of distinct canonical stats (never alternative spellings).
POSITION_STAT_SUMS: dict[str, dict[str, tuple[str, ...]]] = {
    "DEF": {"takeaways": ("int", "fum_rec")},
}

POSITION_OPPORTUNITY = {
    "QB": "dropbacks",
    "RB": "weighted_touches",
    "WR": "targets",
    "TE": "targets",
    "K": "total_kick_attempts",
    "DEF": "sacks",
}
# Exact stat keys behind each position's opportunity metric; used for
# projection key-presence flags.
POSITION_OPPORTUNITY_INPUT_KEYS: dict[str, tuple[str, ...]] = {
    "QB": ("pass_att", "pass_sack"),
    "RB": ("rush_att", "rec_tgt"),
    "WR": ("rec_tgt",),
    "TE": ("rec_tgt",),
    "K": ("fga", "xpa"),
    "DEF": ("sack",),
}

POSITION_MISSING_CONTEXT = {
    "QB": ("pressure", "implied_total"),
    "RB": ("touch_share", "red_zone_share", "nfl_schedule", "implied_total"),
    "WR": ("routes", "target_share", "air_yard_share", "nfl_schedule"),
    "TE": ("routes", "target_share", "red_zone_share", "nfl_schedule"),
    "K": ("nfl_schedule", "weather", "implied_total"),
    "DEF": ("nfl_schedule", "pressure", "implied_total"),
}

UNAVAILABLE_STATUSES = frozenset({"out", "inactive", "ir", "reserve"})
UNAVAILABLE_INJURY_STATUSES = frozenset({"out", "ir"})


def is_unavailable(availability: Mapping[str, Any] | None) -> bool:
    """Week-specific absence predicate shared by own-row and teammate checks."""

    if not availability:
        return False
    status = str(availability.get("status") or "").lower()
    injury = str(availability.get("injury_status") or "").lower()
    return status in UNAVAILABLE_STATUSES or injury in UNAVAILABLE_INJURY_STATUSES


class PlayerUsageContextService:
    """Build one explainable context profile from normalized Sleeper data."""

    def __init__(self, repository: Any, *, provider_registry: Any | None = None) -> None:
        # Production passes SQLiteNormalizedRepositoryAdapter, which owns
        # query_numeric_stat_rows while the wrapped SleeperNormalizedRepository
        # owns the list_* / get_* primitives.  Keep both and resolve each method
        # on whichever object defines it.
        self.outer_repository = repository
        self.repository = getattr(repository, "repository", repository)
        self.provider_registry = provider_registry

    def _method(self, name: str) -> Any:
        for target in (self.outer_repository, self.repository):
            method = getattr(target, name, None)
            if callable(method):
                return method
        return None

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
        actuals = self._materialize_rows(actual_rows, player, source="stats", position=resolved_position)
        projections = self._materialize_rows(projection_rows, player, source="projections", position=resolved_position)
        self._apply_snapshots(actuals, player_id, season)
        self._apply_snapshots(projections, player_id, season)
        actuals = [row for row in actuals if row["week"] <= week]
        projections = [row for row in projections if row["week"] <= week]
        recent = trend_window(actuals, "last_2_weeks")
        earlier = [row for row in actuals if row not in recent]

        metric = POSITION_OPPORTUNITY[resolved_position]
        change_point = detect_change_point(resolved_position, actuals, metric=metric)
        trend_rows, baseline_rows, trend_name = self._trend_rows(resolved_position, actuals, change_point)

        projection_comparison = self._projection_comparison(resolved_position, actuals, projections)
        current_comparison = projection_comparison[-1] if projection_comparison else None
        if current_comparison and current_comparison["projection_delta"] is not None:
            for row in recent:
                row["projection_delta"] = current_comparison["projection_delta"]
            actuals[-1]["projection_delta"] = current_comparison["projection_delta"]

        inputs: dict[str, Any] = {
            "recent_weeks": recent,
            "season_weeks": earlier,
            "current_metadata": self._metadata_context(player),
        }
        if resolved_position in INJURY_REPLACEMENT_POSITIONS:
            inputs["teammate_availability"] = self._teammate_availability(player_id, resolved_position, season, trend_rows, baseline_rows)
        rule_result = evaluate_player_usage_context(resolved_position, inputs)
        reasons = list(rule_result["reasons"])
        td_dependency = compute_td_dependency(resolved_position, trend_rows, window=trend_name)
        if td_dependency["reason"] == "not_evaluable_missing_scoring_contributions":
            reasons.append(_reason_row("not_evaluable_missing_scoring_contributions"))
        missing_inputs = self._missing_context(resolved_position, actuals)
        reasons.extend(self._missing_reasons(missing_inputs))
        reason_codes = list(dict.fromkeys(rule_result["reason_codes"] + [row["code"] for row in reasons]))

        scores = self._scores(resolved_position, actuals, trend_rows, baseline_rows, reasons, rule_result, td_dependency)
        role = self._role_label(scores, reasons, player)
        snapshots = self._snapshots(player_id, season, week)
        trend_window_block = dict(rule_result["trend_window"])
        trend_window_block.update({"name": trend_name, "weeks": [row["week"] for row in trend_rows], "baseline_weeks": [row["week"] for row in baseline_rows], "change_point": change_point})
        return {
            "schema_version": PLAYER_USAGE_CONTEXT_SCHEMA_VERSION,
            "data_source": "sleeper_normalized_data",
            "player_id": str(player_id),
            "name": player.get("full_name") or player.get("player_name") or str(player_id),
            "team": player.get("team"),
            "position": resolved_position,
            "season": season,
            "week": week,
            "actual_first": bool(actuals),
            "primary_source": "stats" if actuals else "projections",
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
            "trend_window": trend_window_block,
            "windows": self._windows(actuals, projections, change_point),
            "td_dependency": td_dependency,
            "td_dependency_method": td_dependency["method"],
            "injury_replacement": rule_result.get("injury_replacement"),
            "projection_comparison": projection_comparison,
            "derived_metrics": compute_derived_metrics(resolved_position, actuals, change_point=change_point),
            "implementation_notes": {
                "python_only": ["derived_metrics", "injury_replacement teammate availability (Worker has no historical availability store)"],
            },
            "evidence": {
                "recent_actual_weeks": [row["week"] for row in trend_rows],
                "season_actual_weeks": [row["week"] for row in actuals],
                "baseline_weeks": [row["week"] for row in baseline_rows],
                "projection_weeks": [row["week"] for row in projections],
                "actual_rows": actuals,
                "projection_rows": projections,
                "actual_source": "stats",
                "projection_source": "projections",
            },
        }

    def _trend_rows(self, position: str, actuals: list[dict[str, Any]], change_point: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        if change_point.get("status") == "change_detected":
            post, pre = post_change_rows(actuals, change_point)
            return list(post), list(pre), "post_change_window"
        metric = POSITION_OPPORTUNITY[position]
        name = POSITION_USAGE_THRESHOLDS[position][metric]["window"]
        window = list(trend_window(actuals, name))
        eligible = list(trend_window(actuals, "season_to_date"))
        baseline = eligible[: len(eligible) - len(window)] if len(eligible) > len(window) else []
        return window, baseline, name

    def _player(self, player_id: str) -> dict[str, Any]:
        getter = self._method("get_player")
        player = getter(str(player_id)) if getter else None
        return dict(player or {"player_id": str(player_id)})

    def _stat_rows(self, player_id: str, season: int, week: int, source: str, *, end_week: int | None = None, start_week: int = 1, with_raw: bool = True) -> list[dict[str, Any]]:
        query = self._method("query_numeric_stat_rows")
        if query is None:
            return []
        rows = list(query(source=source, season=season, start_week=start_week, end_week=end_week or week, player_ids=[str(player_id)]))
        if not with_raw:
            return rows
        raw_getter = self._method("list_player_week_rows")
        scoring_getter = self._method("list_player_week_scoring_values") if source == "stats" else None
        raw_by_week: dict[int, Any] = {}
        scoring_by_week: dict[int, dict[str, float] | None] = {}
        for current_week in sorted({int(row["week"]) for row in rows}):
            if raw_getter is not None:
                raw_rows = list(raw_getter(source=source, season=season, week=current_week, player_id=str(player_id)))
                if raw_rows:
                    raw_by_week[current_week] = raw_rows[0]
            if source == "stats":
                if scoring_getter is None:
                    scoring_by_week[current_week] = None
                else:
                    values = scoring_getter(source=source, season=season, week=current_week, player_id=str(player_id))
                    scoring_by_week[current_week] = {str(value["stat_key"]): float(value["points"]) for value in values}
        return [{**row, "raw_row": raw_by_week.get(int(row["week"])), "scoring_contributions": scoring_by_week.get(int(row["week"]))} for row in rows]

    def _materialize_rows(self, rows: Iterable[Mapping[str, Any]], player: Mapping[str, Any], *, source: str, position: str) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = defaultdict(dict)
        for raw in rows:
            week = int(raw["week"])
            grouped[week].update({"week": week, "player_id": str(raw.get("player_id")), "team": raw.get("team") or player.get("team"), "position": raw.get("position") or player.get("position"), "source": source})
            if raw.get("raw_row") is not None:
                grouped[week]["raw_row"] = raw["raw_row"]
            if "scoring_contributions" in raw:
                grouped[week]["scoring_contributions"] = raw["scoring_contributions"]
            grouped[week].setdefault("stats", {})[str(raw["stat_key"])] = float(raw["stat_value"])
        result = []
        for week, row in sorted(grouped.items()):
            stats = row.pop("stats", {})
            raw_row = row.get("raw_row") or {}
            raw_json = raw_row.get("raw_json", raw_row.get("raw")) if isinstance(raw_row, Mapping) else None
            if isinstance(raw_json, Mapping):
                row["raw"] = dict(raw_json)
            row["raw_stats"] = dict(stats)
            for field, key in (STAT_KEYS | POSITION_STAT_KEYS.get(position, {})).items():
                if key in stats:
                    row[field] = stats[key]
            for field, keys in POSITION_STAT_SUMS.get(position, {}).items():
                if any(key in stats for key in keys):
                    row[field] = sum(stats.get(key) or 0 for key in keys)
            row["dropbacks"] = _sum_present(row, ("pass_attempts", "sacks_taken"))
            # weighted_touches (rush_att + rec_tgt) is the RB opportunity metric;
            # touches (rush_att + rec) is evidence only.
            row["weighted_touches"] = _sum_present(row, ("rush_attempts", "targets"))
            row["touches"] = _sum_present(row, ("rush_attempts", "receptions"))
            row["total_kick_attempts"] = _sum_present(stats, ("fga", "xpa"))
            row["total_tds"] = sum(row.get(key) or 0 for key in ("pass_tds", "rush_tds", "rec_tds"))
            row["def_or_special_teams_tds"] = row.get("def_tds") or 0
            # League fantasy_points is non-null only when league scoring ran at
            # sync time; it is the TD-dependency denominator.
            league_points = raw_row.get("fantasy_points") if isinstance(raw_row, Mapping) else None
            row["league_fantasy_points"] = float(league_points) if league_points is not None else None
            row["points"], row["points_source"] = _row_points(raw_row, stats)
            row["active"] = bool(raw_row["active"]) if isinstance(raw_row, Mapping) and "active" in raw_row else True
            row["bye"] = bool(raw_row["bye"]) if isinstance(raw_row, Mapping) and "bye" in raw_row else False
            if source != "stats":
                row.pop("scoring_contributions", None)
                row.pop("league_fantasy_points", None)
            elif position == "K":
                # long_kick_point_share = long-FG league points / league points
                # for this one row; null when contributions are missing or the
                # league scores field goals by yardage.
                share = compute_td_dependency("K", [row], window="row")["position_formula"]
                row["long_kick_point_share"] = share["value"]
                row["long_kick_point_share_reason"] = share["reason"]
            row.pop("raw_row", None)
            result.append(row)
        return result

    def _apply_snapshots(self, rows: list[dict[str, Any]], player_id: str, season: int) -> None:
        for row in rows:
            week = row["week"]
            availability = self._list("list_player_week_availability", season=season, week=week, player_id=player_id)
            if row.get("active", True) and is_unavailable(availability[0] if availability else None):
                row["active"] = False
            schedule = self._list_one("get_team_week_schedule", season=season, week=week, team=row.get("team") or "")
            if not row.get("bye") and schedule and schedule.get("is_bye"):
                row["bye"] = True

    def _teammate_availability(self, player_id: str, position: str, season: int, trend_rows: list[dict[str, Any]], baseline_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Collect same-team, same-position teammate absences for the trend weeks.

        Evidence comes only from player_week_availability; when the repository
        has no rows for the team in a week, that week is reported as missing.
        """

        metric = POSITION_OPPORTUNITY[position]
        baseline_weeks = sorted({int(row["week"]) for row in baseline_rows})
        players: dict[str, dict[str, Any]] = {}
        baselines: dict[str, float | None] = {}
        weeks = []
        for row in trend_rows:
            week = int(row["week"])
            team = str(row.get("team") or "").upper()
            entries = self._list("list_player_week_availability", season=season, week=week, team=team) if team else []
            absent = []
            for entry in entries:
                teammate_id = str(entry.get("player_id") or "")
                if not teammate_id or teammate_id == str(player_id) or not is_unavailable(entry):
                    continue
                if teammate_id not in players:
                    players[teammate_id] = self._player(teammate_id)
                if str(players[teammate_id].get("position") or "").upper() != position:
                    continue
                if teammate_id not in baselines:
                    baselines[teammate_id] = self._teammate_baseline(teammate_id, position, season, baseline_weeks, metric)
                absent.append({
                    "player_id": teammate_id,
                    "name": players[teammate_id].get("full_name"),
                    "position": position,
                    "team": team,
                    "status": entry.get("status"),
                    "injury_status": entry.get("injury_status"),
                    "source": entry.get("source"),
                    "snapshot_at": entry.get("snapshot_at"),
                    "baseline_weeks": baseline_weeks,
                    "baseline_opportunity_metric": metric,
                    "baseline_opportunity_per_game": baselines[teammate_id],
                })
            weeks.append({"week": week, "team": team, "availability_present": bool(entries), "availability_rows": len(entries), "absent_teammates": absent})
        return {"evidence_source": "player_week_availability", "weeks": weeks}

    def _teammate_baseline(self, teammate_id: str, position: str, season: int, baseline_weeks: list[int], metric: str) -> float | None:
        if not baseline_weeks:
            return None
        rows = self._stat_rows(teammate_id, season, max(baseline_weeks), "stats", start_week=min(baseline_weeks), with_raw=False)
        materialized = self._materialize_rows(rows, {"player_id": teammate_id}, source="stats", position=position)
        values = [float(row[metric]) for row in materialized if row["week"] in baseline_weeks and row.get(metric) is not None]
        return round(sum(values) / len(values), 4) if values else None

    def _projection_comparison(self, position: str, actuals: list[dict[str, Any]], projections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Same-week actual vs projection pairing with explicit presence flags.

        A missing projection row or key is reported as absent (value null), never
        as a projected zero.  ``projection_delta`` is projected - actual points and
        exists only when the projection row and its points key are both present.
        """

        by_week = {int(row["week"]): row for row in projections}
        keys = POSITION_OPPORTUNITY_INPUT_KEYS[position]
        output = []
        for actual in actuals:
            projection = by_week.get(int(actual["week"]))
            row_present = projection is not None
            projection_stats = projection.get("raw_stats", {}) if projection else {}
            key_present = {"points": bool(projection and projection.get("points_source"))} | {key: key in projection_stats for key in keys}
            projected_points = projection["points"] if key_present["points"] else None
            delta = None
            if projected_points is not None and actual.get("points") is not None:
                delta = round(float(projected_points) - float(actual["points"]), 4)
            output.append({
                "week": int(actual["week"]),
                "projection_row_present": row_present,
                "projection_key_present": key_present,
                "actual_points": actual.get("points"),
                "projected_points": projected_points,
                "projected_values": {key: (projection_stats[key] if key in projection_stats else None) for key in keys},
                "projection_points_source": projection.get("points_source") if projection else None,
                "projection_delta": delta,
            })
        return output

    def _metadata_context(self, player: Mapping[str, Any]) -> dict[str, Any]:
        return {key: player.get(key) for key in ("depth_chart_order", "depth_chart_position", "status", "injury_status") if player.get(key) is not None} | {"depth_chart_role": player.get("depth_chart_position") or player.get("depth_chart_order")}

    def _snapshots(self, player_id: str, season: int, week: int) -> dict[str, list[dict[str, Any]]]:
        return {"role": self._list("list_player_role_snapshots", season=season, week=week, player_id=player_id), "availability": self._list("list_player_week_availability", season=season, week=week, player_id=player_id)}

    def _list(self, name: str, **kwargs: Any) -> list[dict[str, Any]]:
        method = self._method(name)
        return list(method(**kwargs)) if method else []

    def _list_one(self, name: str, **kwargs: Any) -> dict[str, Any] | None:
        method = self._method(name)
        return method(**kwargs) if method else None

    def _missing_context(self, position: str, rows: list[dict[str, Any]]) -> list[str]:
        missing = list(POSITION_MISSING_CONTEXT[position])
        if not rows:
            missing.append("actual_stats")
        return sorted(set(missing))

    def _missing_reasons(self, inputs: Iterable[str]) -> list[dict[str, Any]]:
        output = []
        for value in inputs:
            code = {
                "pressure": "not_evaluable_missing_matchup_pressure",
                "implied_total": "not_evaluable_missing_matchup_implied_total",
                "weather": "not_evaluable_missing_matchup_weather",
            }.get(value, "not_evaluable_missing_" + value)
            definition = REASON_CODES.get(code, {"component": "context_confidence", "polarity": "neutral", "computability_tier": "not_evaluable_missing_source", "evidence": [], "severity": "medium", "description": f"{value} is unavailable in Sleeper-only mode", "missing_inputs": [value]})
            output.append({"code": code, **{key: definition[key] for key in ("component", "polarity", "computability_tier", "evidence", "severity", "description", "missing_inputs")}})
        return output

    def _windows(self, actuals: list[dict[str, Any]], projections: list[dict[str, Any]], change_point: Mapping[str, Any]) -> dict[str, Any]:
        windows: dict[str, Any] = {name: {"actual": trend_window(actuals, name), "projection": trend_window(projections, name), "sample_size": len(trend_window(actuals, name))} for name in TREND_WINDOWS}
        detected = change_point.get("status") == "change_detected"
        post = post_change_rows(actuals, change_point)[0] if detected else []
        post_weeks = {int(row["week"]) for row in post}
        windows["post_change_window"] = {
            "detected": detected,
            "status": change_point.get("status"),
            "change_week": change_point.get("change_week"),
            "direction": change_point.get("direction"),
            "pre_change_weeks": change_point.get("pre_change_weeks", []),
            "actual": post,
            "projection": [row for row in trend_window(projections, "season_to_date") if int(row["week"]) in post_weeks],
            "sample_size": len(post),
            "minimum_samples": POST_CHANGE_WINDOW["minimum_samples"],
        }
        return windows

    def _scores(self, position: str, actuals: list[dict[str, Any]], recent: list[dict[str, Any]], baseline: list[dict[str, Any]], reasons: list[dict[str, Any]], rule_result: Mapping[str, Any], td_dependency: Mapping[str, Any]) -> dict[str, float | None]:
        metric = POSITION_OPPORTUNITY[position]
        recent_value = _average(recent, metric)
        baseline_value = _average(baseline, metric)
        opportunity = min(100.0, max(0.0, (recent_value or 0) * 5.0))
        stability = max(0.0, min(100.0, 100.0 - (_relative_spread(recent, metric) * 100.0))) if recent else 0.0
        points = _average(recent, "points") or 0
        # League-scoring TD share (0-100); null when contributions are missing.
        td_dependency_value = td_dependency_score(td_dependency)
        trend_mod = float(rule_result.get("capped_modifiers", {}).get("trend", 0))
        context_mod = float(rule_result.get("capped_modifiers", {}).get("context", 0))
        season_context = max(0.0, min(100.0, 50.0 + ((_ratio(recent_value, baseline_value) or 0) * 25.0)))
        return {
            "opportunity_score": round(opportunity, 2),
            "role_stability_score": round(stability, 2),
            "production_quality_score": round(max(0.0, min(100.0, points * 4.0 - (td_dependency_value or 0) * 0.25)), 2),
            "td_dependency_score": td_dependency_value,
            "depth_chart_confidence": 45.0 if any(reason["code"] == "current_metadata_only_depth_chart" for reason in reasons) else 60.0,
            "matchup_adjustment": float(rule_result.get("capped_modifiers", {}).get("matchup", 0)),
            "small_sample_risk": round(max(0.0, 100.0 - len(recent) * 35.0), 2),
            "one_off_risk": round(min(100.0, max(0.0, -context_mod * 12.5)), 2),
            "trend_change_score": round(max(0.0, min(100.0, 50.0 + trend_mod * 12.5)), 2),
            "season_context_score": round(season_context, 2),
            "context_confidence": round(max(0.0, 100.0 - len(self._missing_context(position, actuals)) * 7.0 - (20.0 if len(actuals) < 2 else 0.0)), 2),
        }

    def _role_label(self, scores: Mapping[str, Any], reasons: list[dict[str, Any]], player: Mapping[str, Any]) -> str:
        codes = {reason["code"] for reason in reasons}
        if "role_loss_recent" in codes:
            return "role_declining"
        if "injury_replacement" in codes:
            return "injury_fill_in"
        if "role_change_recent" in codes:
            return "emerging_rotation"
        if player.get("depth_chart_order") == 1:
            return "established_role"
        return "uncertain_role"


def build_player_usage_context(repository: Any, **kwargs: Any) -> dict[str, Any]:
    return PlayerUsageContextService(repository).build(**kwargs)


def _reason_row(code: str) -> dict[str, Any]:
    definition = REASON_CODES[code]
    return {key: list(value) if isinstance(value, list) else value for key, value in definition.items()}


def _row_points(raw_row: Any, stats: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """Return (points, source key); (None, None) when no points value exists."""

    if isinstance(raw_row, Mapping):
        for key in ("fantasy_points", "sleeper_points"):
            if raw_row.get(key) is not None:
                return float(raw_row[key]), key
    for key in ("points", "fantasy_points", "pts_ppr"):
        if stats.get(key) is not None:
            return float(stats[key]), f"stat:{key}"
    return None, None


def _sum_present(values: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    keys = tuple(keys)
    if all(values.get(key) is None for key in keys):
        return None
    return sum(values.get(key) or 0 for key in keys)


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
