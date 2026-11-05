from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

PLAYER_USAGE_CONTEXT_SCHEMA_VERSION = "player_usage_context.v3"
POSITION_FAMILIES = ("QB", "RB", "WR", "TE", "K", "DEF")
SCORE_COMPONENT_KEYS = (
    "opportunity", "role_stability", "production_quality", "td_dependency",
    "depth_chart_confidence", "matchup_adjustment", "small_sample_risk",
    "one_off_risk", "trend_change_score", "season_context_score", "context_confidence",
)

COMPUTABILITY_TIERS: dict[str, dict[str, Any]] = {
    "computed": {"rank": 3, "description": "normalized Sleeper fields are sufficient for a deterministic signal", "required_sources": ["normalized_stats_or_projections"]},
    "partial": {"rank": 2, "description": "a useful Sleeper proxy is computable, but an important desired input is missing", "required_sources": ["normalized_stats_or_projections"]},
    "current_metadata_only": {"rank": 1, "description": "the signal can use current metadata but not historical week-specific context", "required_sources": ["current_player_metadata"]},
    "not_evaluable_missing_source": {"rank": 0, "description": "the source required to compute the signal is not available", "required_sources": []},
}

TREND_WINDOWS: dict[str, dict[str, Any]] = {
    "last_1_week": {"weeks": 1, "minimum_samples": 1, "eligible_weeks": "active_non_bye"},
    "last_2_weeks": {"weeks": 2, "minimum_samples": 2, "eligible_weeks": "active_non_bye"},
    "last_3_weeks": {"weeks": 3, "minimum_samples": 3, "eligible_weeks": "active_non_bye"},
    "last_4_weeks": {"weeks": 4, "minimum_samples": 3, "eligible_weeks": "active_non_bye"},
    "season_to_date": {"weeks": None, "minimum_samples": 1, "eligible_weeks": "active_non_bye"},
}
# post_change_window is dynamic: it exists only when detect_change_point finds a
# change, so it is defined separately from the fixed-size TREND_WINDOWS above.
POST_CHANGE_WINDOW: dict[str, Any] = {
    "weeks": "dynamic",
    "minimum_samples": 2,
    "minimum_pre_change_samples": 1,
    "eligible_weeks": "active_non_bye",
    "series": "position opportunity metric (QB dropbacks, RB weighted_touches, WR/TE targets, K total_kick_attempts, DEF sacks)",
    "rule": (
        "Over the active, non-bye actual weeks that have the opportunity metric, the change week is the earliest week i "
        "with at least 1 earlier week and at least 2 weeks from i through the latest week, such that every week from i "
        "through the latest week individually clears the position's role threshold (POSITION_USAGE_THRESHOLDS: "
        "increase_pct strictly greater, or increase_per_game greater or equal) against the mean of the weeks before i, "
        "all in the same direction. post_change_window is weeks i..latest and pre_change_baseline is the weeks before i. "
        "When a change is detected, the role signal compares post_change_window against pre_change_baseline instead of "
        "the fixed last_2_weeks/last_3_weeks split. A single-week spike never qualifies, and neither does a shift that "
        "reverts before the latest week."
    ),
}
MINIMUM_SAMPLE_REQUIREMENTS: dict[str, dict[str, int]] = {
    "role_change_recent": {"recent": 2, "baseline": 1},
    "role_loss_recent": {"recent": 2, "baseline": 1},
    "projection_lagging_role_change": {"recent": 2, "baseline": 1},
    "season_average_stale": {"recent": 2, "baseline": 3},
    "recent_spike_against_stable_usage": {"recent": 2, "baseline": 1},
    "insufficient_post_change_sample": {"recent": 1, "baseline": 1},
    "low_touch_big_points": {"recent": 1, "baseline": 1},
    "low_target_big_points": {"recent": 1, "baseline": 1},
    "td_only_low_usage": {"recent": 1, "baseline": 1},
    "long_kick_spike": {"recent": 1, "baseline": 1},
    "def_td_spike": {"recent": 1, "baseline": 1},
}

MODIFIER_CAPS: dict[str, dict[str, float]] = {
    "context": {"min": -6.0, "max": 6.0},
    "trend": {"min": -4.0, "max": 4.0},
    "matchup": {"min": -2.0, "max": 2.0},
    "def_streaming_matchup": {"min": -3.0, "max": 3.0},
}
POSITION_MODIFIER_CAPS: dict[str, dict[str, dict[str, float]]] = {position: deepcopy(MODIFIER_CAPS) for position in POSITION_FAMILIES}
POSITION_MODIFIER_CAPS["DEF"]["matchup"] = deepcopy(MODIFIER_CAPS["def_streaming_matchup"])

POSITION_USAGE_THRESHOLDS: dict[str, dict[str, Any]] = {
    "QB": {"dropbacks": {"increase_pct": 0.15, "window": "last_2_weeks"}, "rush_attempts": {"increase_per_game": 2.5, "window": "last_2_weeks"}},
    "RB": {"weighted_touches": {"increase_pct": 0.30, "increase_per_game": 5.0, "window": "last_2_weeks"}, "targets": {"increase_per_game": 2.0, "window": "last_2_weeks"}},
    "WR": {"targets": {"increase_pct": 0.30, "increase_per_game": 2.5, "window": "last_2_weeks"}},
    "TE": {"targets": {"increase_pct": 0.30, "increase_per_game": 2.0, "window": "last_2_weeks"}, "red_zone_targets": {"straight_weeks": 2, "window": "last_2_weeks"}},
    "K": {"total_kick_attempts": {"increase_pct": 0.25, "window": "last_3_weeks"}},
    "DEF": {"sacks": {"increase_pct": 0.30, "window": "last_3_weeks"}, "points_allowed": {"improvement_pct": 0.20, "window": "last_3_weeks", "lower_is_better": True}},
}

# --- League-scoring TD dependency (v3) ---------------------------------------
# TD points come from the per-stat league scoring contributions computed at sync
# time (player_week_scoring_values, written by scoring.calculate_fantasy_points).
# A contribution key is a TD-scoring key when it ends in "_td" or contains "_td_"
# (pass_td, rush_td, rec_td, def_td, def_st_td, st_td, fum_rec_td, rec_td_40p,
# bonus_def_int_td_50p, ...). pass_int_td (an interception returned for a TD,
# a negative QB contribution) is a turnover penalty, not touchdown value, and is
# excluded explicitly.
TD_SCORING_KEY_EXCLUSIONS: tuple[str, ...] = ("pass_int_td",)
LONG_FIELD_GOAL_SCORING_KEYS: tuple[str, ...] = ("fgm_50_59", "fgm_60p", "fgm_50p")
FIELD_GOAL_YARDAGE_SCORING_KEYS: tuple[str, ...] = ("fgm_yds", "fgm_yds_over_30")
DEF_TURNOVER_SCORING_KEYS: tuple[str, ...] = ("int", "fum_rec")
TD_DEPENDENCY_METHOD = "league_scoring_contributions"
TD_DEPENDENCY_SOURCE = "player_week_scoring_values"
TD_DEPENDENCY_SCORE_FORMULA = "clamp(100 * sum(td_scoring_points) / sum(league fantasy_points), 0, 100) over the trend window"

# Per-position dependency formulas, evaluated by compute_td_dependency. Each
# numerator is a set of league scoring-contribution key families; the
# denominator is always the league fantasy_points total for the same rows.
TD_DEPENDENCY_FORMULAS: dict[str, dict[str, Any]] = {
    "QB": {"formula": "sum(passing_td_points) / sum(fantasy_points)", "numerator_families": ["passing_td"], "dependency_threshold": 0.45, "stability_counter_signal": "rush_attempts_per_game >= 4"},
    "RB": {"formula": "sum(rushing_td_points + receiving_td_points) / sum(fantasy_points)", "numerator_families": ["rushing_td", "receiving_td"], "dependency_threshold": 0.35, "stability_counter_signal": "weighted_touches_per_game >= 14"},
    "WR": {"formula": "sum(receiving_td_points) / sum(fantasy_points)", "numerator_families": ["receiving_td"], "dependency_threshold": 0.40, "stability_counter_signal": "targets_per_game >= 7"},
    "TE": {"formula": "sum(receiving_td_points) / sum(fantasy_points)", "numerator_families": ["receiving_td"], "dependency_threshold": 0.45, "stability_counter_signal": "targets_per_game >= 5"},
    "K": {"formula": "sum(long_field_goal_points) / sum(fantasy_points)", "numerator_families": ["long_field_goal"], "dependency_threshold": 0.40, "stability_counter_signal": "total_kick_attempts_last_3_increase_pct >= 0.25"},
    "DEF": {"formula": "sum(td_scoring_points + turnover_points) / sum(fantasy_points)", "numerator_families": ["passing_td", "rushing_td", "receiving_td", "defense_special_teams_td", "other_td", "turnover"], "dependency_threshold": 0.50, "stability_counter_signal": "sacks_increase_pct >= 0.30 or points_allowed_improvement_pct >= 0.20"},
}
# --- TD-dependency weighting (issue #32 calibration) --------------------------
# Only the league TD share above the position dependency_threshold counts as
# risk; ordinary TD scorers are not penalized.  The quantity is the all-TD
# league share (td_dependency_score, 0-100) that compare scoring already used.
#
# The weight is expressed once, as expected regression in league fantasy points
# per game for each percentage point of excess TD share, beyond ordinary
# regression to the mean.  Each consumer converts it to its own units:
#   compare_player_usage_context:  PRODUCTION_QUALITY_POINTS_PER_PPG (4.0) per ppg
#   score_waiver_move:              MOVE_SCORE_ACTUAL_PPG_WEIGHT (1.2 + 1.4) per ppg,
#                                   only when actual points feed the move value.
# Calibration (preliminary prior, 2026 weeks 1-4, one league scoring):
# OLS of delta_ppg (later window minus trailing window) on trailing ppg and
# excess share, across three overlapping window splits.  RB/WR/TE excess
# coefficients ranged -0.14 to -0.57 ppg per point, pooled skill -0.06 to
# -0.15; QB showed no extra regression (-0.00 to +0.29).  Values are shrunk
# toward the low end because samples are small (QB n=24-29, RB n=35-44,
# WR n=49-58, TE n=21-26; 1-13 flagged players per group).  K and DEF were not
# calibrated (their thresholds belong to different position formulas) and use
# a labeled prior.  See docs/player-context-decision-engine.md.
TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT: dict[str, float] = {
    "QB": 0.05,
    "RB": 0.15,
    "WR": 0.15,
    "TE": 0.15,
    "K": 0.10,
    "DEF": 0.10,
}
TD_DEPENDENCY_WEIGHT_PROVENANCE: dict[str, str] = {
    "QB": "calibrated_preliminary_prior",
    "RB": "calibrated_preliminary_prior",
    "WR": "calibrated_preliminary_prior",
    "TE": "calibrated_preliminary_prior",
    "K": "uncalibrated_prior",
    "DEF": "uncalibrated_prior",
}
# production_quality_score = recent points per game * 4.0 (player_usage_context).
PRODUCTION_QUALITY_POINTS_PER_PPG = 4.0


def td_dependency_excess_pct(position: str, td_dependency_score_value: float | None) -> float | None:
    """TD share (0-100) above the position dependency_threshold; None when not evaluable."""

    if td_dependency_score_value is None:
        return None
    formula = TD_DEPENDENCY_FORMULAS.get(str(position or "").upper())
    threshold = float(formula["dependency_threshold"]) * 100 if formula else 0.0
    return round(max(0.0, float(td_dependency_score_value) - threshold), 2)


SPIKE_THRESHOLDS: dict[str, dict[str, Any]] = {
    "QB": {"passing_tds": {"min": 3}, "low_rushing_floor": {"rush_attempts_max": 3}},
    "RB": {"total_tds": {"min": 2}, "low_touch_floor": {"weighted_touches_max": 12}},
    "WR": {"rec_tds": {"min": 2}, "low_target_floor": {"targets_max": 5}},
    "TE": {"rec_tds": {"min": 1}, "low_target_floor": {"targets_max": 4}},
    "K": {"made_50_plus": {"min": 2}, "long_kick_point_share": {"min": 0.40}},
    "DEF": {"def_or_special_teams_tds": {"min": 1}, "takeaways": {"min": 3}},
}

REASON_CODE_FIELDS: dict[str, dict[str, Any]] = {
    "code": {"type": "string", "required": True},
    "component": {"type": "string", "required": True, "allowed_values": SCORE_COMPONENT_KEYS},
    "polarity": {"type": "string", "required": True, "allowed_values": ("positive", "negative", "neutral")},
    "computability_tier": {"type": "string", "required": True, "allowed_values": tuple(COMPUTABILITY_TIERS)},
    "evidence": {"type": "array", "items": {"type": "string"}, "required": True},
    "severity": {"type": "string", "required": True, "allowed_values": ("low", "medium", "high")},
    "missing_inputs": {"type": "array", "items": {"type": "string"}, "required": True},
    "description": {"type": "string", "required": True},
}


def _reason(code: str, component: str, polarity: str, tier: str, evidence: Sequence[str], severity: str, description: str, missing_inputs: Sequence[str] = ()) -> dict[str, Any]:
    return {"code": code, "component": component, "polarity": polarity, "computability_tier": tier, "evidence": list(evidence), "severity": severity, "missing_inputs": list(missing_inputs), "description": description}


REASON_CODES: dict[str, dict[str, Any]] = {
    "role_change_recent": _reason("role_change_recent", "opportunity", "positive", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity increased beyond the position threshold"),
    "role_loss_recent": _reason("role_loss_recent", "opportunity", "negative", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity declined beyond the position threshold"),
    "projection_lagging_role_change": _reason("projection_lagging_role_change", "trend_change_score", "positive", "partial", ("recent opportunity", "projection delta"), "medium", "usage movement is ahead of the projection baseline"),
    "season_average_stale": _reason("season_average_stale", "season_context_score", "negative", "computed", ("recent points", "season points"), "low", "season average is stale relative to the recent active-week window"),
    "recent_spike_against_stable_usage": _reason("recent_spike_against_stable_usage", "one_off_risk", "negative", "partial", ("recent points", "stable opportunity"), "medium", "points rose without matching opportunity growth"),
    "insufficient_post_change_sample": _reason("insufficient_post_change_sample", "small_sample_risk", "negative", "computed", ("post-change active weeks",), "high", "too few active weeks exist after the detected role change"),
    "low_touch_big_points": _reason("low_touch_big_points", "one_off_risk", "negative", "computed", ("fantasy points", "weighted touches (rush_att + rec_tgt)"), "high", "skill-player points spiked on a low weighted-touch week"),
    "low_target_big_points": _reason("low_target_big_points", "one_off_risk", "negative", "computed", ("fantasy points", "targets"), "high", "receiving points spiked on a low-target week"),
    "low_route_big_points": _reason("low_route_big_points", "one_off_risk", "negative", "not_evaluable_missing_source", (), "high", "route-based spike cannot be evaluated without route data", ("routes",)),
    "td_only_low_usage": _reason("td_only_low_usage", "td_dependency", "negative", "computed", ("weighted touches or targets", "touchdown stats"), "high", "touchdown points dominate a low-usage performance"),
    "def_td_spike": _reason("def_td_spike", "one_off_risk", "negative", "computed", ("defensive touchdown stats",), "high", "defensive value was inflated by a touchdown"),
    "turnover_spike_without_pressure": _reason("turnover_spike_without_pressure", "one_off_risk", "negative", "partial", ("turnovers", "sacks or qb hits"), "medium", "turnovers rose without the available pressure proxy rising"),
    "long_kick_spike": _reason("long_kick_spike", "one_off_risk", "negative", "computed", ("long field goals", "kicking points"), "medium", "kicker value was inflated by long field goals"),
    "matchup_opponent_label_present": _reason("matchup_opponent_label_present", "matchup_adjustment", "neutral", "current_metadata_only", ("raw opponent label",), "low", "only the current weekly opponent label is available", ("matchup strength",)),
    "not_evaluable_missing_nfl_schedule": _reason("not_evaluable_missing_nfl_schedule", "matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "high", "NFL schedule/opponent join is missing; matchup adjustment is disabled", ("nfl schedule",)),
    "not_evaluable_missing_matchup_strength": _reason("not_evaluable_missing_matchup_strength", "matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "medium", "opponent strength data is missing", ("matchup strength",)),
    "not_evaluable_missing_matchup_weather": _reason("not_evaluable_missing_matchup_weather", "matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "low", "weather and stadium data are missing", ("weather",)),
    "not_evaluable_missing_matchup_implied_total": _reason("not_evaluable_missing_matchup_implied_total", "matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "low", "betting and implied-total data are missing", ("implied total",)),
    "not_evaluable_missing_matchup_pressure": _reason("not_evaluable_missing_matchup_pressure", "matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "medium", "opponent pressure context is missing", ("pressure matchup",)),
    "not_evaluable_missing_dropbacks": _reason("not_evaluable_missing_dropbacks", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "dropbacks are missing; this signal is not evaluable", ("dropbacks",)),
    "not_evaluable_missing_weighted_touches": _reason("not_evaluable_missing_weighted_touches", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "weighted touches (rush_att + rec_tgt) are missing; this signal is not evaluable", ("weighted_touches",)),
    "not_evaluable_missing_targets": _reason("not_evaluable_missing_targets", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "targets are missing; this signal is not evaluable", ("targets",)),
    "not_evaluable_missing_rush_attempts": _reason("not_evaluable_missing_rush_attempts", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "rush attempts are missing; this signal is not evaluable", ("rush_attempts",)),
    "not_evaluable_missing_pressure_proxy_rise": _reason("not_evaluable_missing_pressure_proxy_rise", "one_off_risk", "neutral", "not_evaluable_missing_source", (), "medium", "turnover spike risk is not evaluable without a pressure proxy", ("pressure_proxy_rise",)),
    "not_evaluable_missing_total_kick_attempts": _reason("not_evaluable_missing_total_kick_attempts", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "total kick attempts are missing; this signal is not evaluable", ("total_kick_attempts",)),
    "not_evaluable_missing_sacks": _reason("not_evaluable_missing_sacks", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "sacks are missing; this signal is not evaluable", ("sacks",)),
    "not_evaluable_missing_routes": _reason("not_evaluable_missing_routes", "context_confidence", "neutral", "not_evaluable_missing_source", (), "medium", "route participation is not available in normalized Sleeper data", ("routes",)),
    "not_evaluable_missing_target_share": _reason("not_evaluable_missing_target_share", "context_confidence", "neutral", "not_evaluable_missing_source", (), "medium", "target share is not available in normalized Sleeper data", ("target_share",)),
    "not_evaluable_missing_red_zone_share": _reason("not_evaluable_missing_red_zone_share", "context_confidence", "neutral", "not_evaluable_missing_source", (), "medium", "red-zone share needs a team red-zone denominator that Sleeper does not provide; absolute rush_rz_att/rec_rz_tgt counts are exposed in derived_metrics", ("red_zone_share",)),
    "injury_replacement": _reason("injury_replacement", "one_off_risk", "negative", "partial", ("post-change opportunity increase", "same-team same-position teammate absence in player_week_availability", "teammate pre-change actual opportunity"), "medium", "the role increase coincides with a same-team, same-position teammate's absence in every trend-window week, so it may be temporary"),
    "not_evaluable_missing_teammate_availability": _reason("not_evaluable_missing_teammate_availability", "one_off_risk", "neutral", "not_evaluable_missing_source", (), "medium", "the role increase cannot be checked for an injury replacement because week-specific teammate availability is missing for at least one trend-window week", ("teammate_availability",)),
    "not_evaluable_missing_scoring_contributions": _reason("not_evaluable_missing_scoring_contributions", "td_dependency", "neutral", "not_evaluable_missing_source", (), "medium", "league scoring contributions are missing for at least one trend-window week, so TD dependency is not evaluated (no heuristic fallback)", ("scoring_contributions",)),
    "not_evaluable_missing_air_yard_share": _reason("not_evaluable_missing_air_yard_share", "context_confidence", "neutral", "not_evaluable_missing_source", (), "medium", "air-yard share needs a team air-yards denominator that Sleeper does not provide; absolute rec_air_yd/pass_air_yd are exposed in derived_metrics", ("air_yard_share",)),
    "not_evaluable_missing_touch_share": _reason("not_evaluable_missing_touch_share", "context_confidence", "neutral", "not_evaluable_missing_source", (), "medium", "touch share is not available in normalized Sleeper data", ("touch_share",)),
    "current_metadata_only_depth_chart": _reason("current_metadata_only_depth_chart", "depth_chart_confidence", "neutral", "current_metadata_only", ("current depth metadata",), "medium", "depth chart metadata is current-only and not historical", ("historical depth chart",)),
    "current_metadata_only_injury": _reason("current_metadata_only_injury", "role_stability", "neutral", "current_metadata_only", ("current injury metadata",), "medium", "injury metadata is current-only and not historical", ("historical availability",)),
}

SCORE_COMPONENT_SCHEMA: dict[str, dict[str, Any]] = {
    component: {
        "modifier_cap": "matchup" if component == "matchup_adjustment" else ("trend" if component == "trend_change_score" else "context"),
        "direction": "negative" if component in {"td_dependency", "small_sample_risk", "one_off_risk", "context_confidence"} else "positive_or_negative",
        "reason_codes": [code for code, definition in REASON_CODES.items() if definition["component"] == component],
        "description": f"Deterministic {component.replace('_', ' ')} context component.",
    }
    for component in SCORE_COMPONENT_KEYS
}


def _eligible_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted((row for row in rows if row.get("active", True) and not row.get("bye", False)), key=lambda row: int(row.get("week", 0)))


def trend_window(rows: Iterable[Mapping[str, Any]], window: str) -> list[Mapping[str, Any]]:
    if window not in TREND_WINDOWS:
        raise KeyError(f"unknown trend window: {window}")
    eligible = _eligible_rows(rows)
    weeks = TREND_WINDOWS[window]["weeks"]
    return eligible if weeks is None else eligible[-weeks:]


def _average(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def compare_recent_to_baseline(recent: Sequence[Mapping[str, Any]], baseline: Sequence[Mapping[str, Any]], key: str) -> float | None:
    recent_average = _average(recent, key)
    baseline_average = _average(baseline, key)
    if recent_average is None or baseline_average is None:
        return None
    if baseline_average == 0:
        return None if recent_average == 0 else float("inf")
    return float((Decimal(str(recent_average)) - Decimal(str(baseline_average))) / abs(Decimal(str(baseline_average))))


def _missing_metric_reason(metric: str, *, component: str = "opportunity") -> dict[str, Any]:
    normalized = "".join(character if character.isalnum() else "_" for character in metric.lower()).strip("_")
    code = f"not_evaluable_missing_{normalized}"
    if code in REASON_CODES:
        return deepcopy(REASON_CODES[code])
    return _reason(
        code,
        component,
        "neutral",
        "not_evaluable_missing_source",
        (),
        "medium",
        f"{metric} is missing; this signal is not evaluable",
        (metric,),
    )


def _pct_change(value: float, base: float) -> float | None:
    if base == 0:
        return None if value == 0 else float("inf")
    return float((Decimal(str(value)) - Decimal(str(base))) / abs(Decimal(str(base))))


def _threshold_direction(threshold: Mapping[str, Any], value: float, base: float) -> str:
    """Classify value against base with the position role threshold (any-of pct/per-game)."""

    delta_pct = _pct_change(value, base)
    delta = Decimal(str(value)) - Decimal(str(base))
    increase_checks = []
    decrease_checks = []
    if threshold.get("increase_pct") is not None:
        limit = Decimal(str(threshold["increase_pct"]))
        increase_checks.append(delta_pct is not None and Decimal(str(delta_pct)) > limit)
        decrease_checks.append(delta_pct is not None and Decimal(str(delta_pct)) <= -limit)
    if threshold.get("increase_per_game") is not None:
        limit = Decimal(str(threshold["increase_per_game"]))
        increase_checks.append(delta >= limit)
        decrease_checks.append(delta <= -limit)
    if any(increase_checks):
        return "increased"
    if any(decrease_checks):
        return "decreased"
    return "stable"


def detect_change_point(position: str, rows: Sequence[Mapping[str, Any]], *, metric: str | None = None) -> dict[str, Any]:
    """Deterministic change-point detection on a position's opportunity series.

    See POST_CHANGE_WINDOW["rule"]. Returns ``status`` of ``change_detected``,
    ``no_change``, ``insufficient_sample``, or ``not_evaluable_missing_<metric>``.
    """

    position = position.upper()
    metric = metric or _OPPORTUNITY_METRIC_BY_POSITION.get(position) or {"K": "total_kick_attempts", "DEF": "sacks"}.get(position)
    base = {"rule": POST_CHANGE_WINDOW["rule"], "metric": metric, "change_week": None, "direction": None, "post_change_weeks": [], "pre_change_weeks": [], "post_change_average": None, "pre_change_average": None}
    if metric not in POSITION_USAGE_THRESHOLDS.get(position, {}):
        return {**base, "status": "not_applicable"}
    threshold = POSITION_USAGE_THRESHOLDS[position][metric]
    eligible = _eligible_rows(rows)
    series = [row for row in eligible if row.get(metric) is not None]
    if eligible and not series:
        return {**base, "status": f"not_evaluable_missing_{metric}"}
    minimum_pre = POST_CHANGE_WINDOW["minimum_pre_change_samples"]
    minimum_post = POST_CHANGE_WINDOW["minimum_samples"]
    if len(series) < minimum_pre + minimum_post:
        return {**base, "status": "insufficient_sample"}
    for index in range(minimum_pre, len(series) - minimum_post + 1):
        pre = series[:index]
        post = series[index:]
        pre_average = sum(float(row[metric]) for row in pre) / len(pre)
        directions = {_threshold_direction(threshold, float(row[metric]), pre_average) for row in post}
        if len(directions) == 1 and directions <= {"increased", "decreased"}:
            return {
                **base,
                "status": "change_detected",
                "change_week": int(post[0]["week"]),
                "direction": directions.pop(),
                "post_change_weeks": [int(row["week"]) for row in post],
                "pre_change_weeks": [int(row["week"]) for row in pre],
                "post_change_average": round(sum(float(row[metric]) for row in post) / len(post), 4),
                "pre_change_average": round(pre_average, 4),
            }
    return {**base, "status": "no_change"}


def post_change_rows(rows: Sequence[Mapping[str, Any]], change_point: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split eligible rows into (post_change_window, pre_change_baseline)."""

    eligible = _eligible_rows(rows)
    post_weeks = set(change_point.get("post_change_weeks") or [])
    pre_weeks = set(change_point.get("pre_change_weeks") or [])
    return [row for row in eligible if int(row["week"]) in post_weeks], [row for row in eligible if int(row["week"]) in pre_weeks]


def opportunity_signal(position: str, rows: Sequence[Mapping[str, Any]], *, metric: str, signal_code: str = "role_change_recent", recent_rows: Sequence[Mapping[str, Any]] | None = None, baseline_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    position = position.upper()
    threshold = POSITION_USAGE_THRESHOLDS[position][metric]
    all_rows = _eligible_rows(rows)
    if recent_rows is not None and baseline_rows is not None:
        # Explicit split (post_change_window vs pre_change_baseline).
        window = _eligible_rows(recent_rows)
        baseline = _eligible_rows(baseline_rows)
    else:
        window = trend_window(rows, threshold["window"])
        baseline = all_rows[:-len(window)] if len(all_rows) > len(window) else []
    if signal_code not in MINIMUM_SAMPLE_REQUIREMENTS:
        raise KeyError(f"unknown minimum-sample signal: {signal_code}")
    requirements = MINIMUM_SAMPLE_REQUIREMENTS[signal_code]
    if not any(row.get(metric) is not None for row in all_rows):
        reason = _missing_metric_reason(metric)
        return {"status": reason["code"], "delta_pct": None, "delta_per_game": None, "recent": window, "baseline": baseline, "reason_codes": [reason["code"]], "reasons": [reason]}
    if len(window) < requirements["recent"] or len(baseline) < requirements["baseline"]:
        return {"status": "insufficient_sample", "delta_pct": None, "delta_per_game": None, "recent": window, "baseline": baseline, "reason_codes": [], "reasons": []}
    delta_pct = compare_recent_to_baseline(window, baseline, metric)
    recent_average = _average(window, metric)
    baseline_average = _average(baseline, metric)
    if recent_average is None or baseline_average is None:
        reason = _missing_metric_reason(metric)
        return {"status": reason["code"], "delta_pct": None, "delta_per_game": None, "recent": window, "baseline": baseline, "reason_codes": [reason["code"]], "reasons": [reason]}
    delta_per_game = recent_average - baseline_average
    increase_checks = []
    if threshold.get("increase_pct") is not None:
        increase_checks.append(delta_pct is not None and Decimal(str(delta_pct)) > Decimal(str(threshold["increase_pct"])))
    if threshold.get("increase_per_game") is not None:
        increase_checks.append(Decimal(str(delta_per_game)) >= Decimal(str(threshold["increase_per_game"])))
    increased = any(increase_checks)
    decrease_checks = []
    if threshold.get("increase_pct") is not None:
        decrease_checks.append(delta_pct is not None and Decimal(str(delta_pct)) <= -Decimal(str(threshold["increase_pct"])))
    if threshold.get("increase_per_game") is not None:
        decrease_checks.append(Decimal(str(delta_per_game)) <= -Decimal(str(threshold["increase_per_game"])))
    improved = threshold.get("improvement_pct") is not None and delta_pct is not None and Decimal(str(delta_pct)) <= -Decimal(str(threshold["improvement_pct"]))
    status = "increased" if increased or improved else ("decreased" if any(decrease_checks) else "stable")
    return {"status": status, "delta_pct": delta_pct, "delta_per_game": delta_per_game, "recent": window, "baseline": baseline, "reason_codes": [], "reasons": []}


def compute_matchup_modifier(position: str, raw_modifier: float | None, *, nfl_schedule_available: bool, opponent_strength_available: bool = True) -> dict[str, Any]:
    if not nfl_schedule_available:
        return {"modifier": 0.0, "reason_codes": ["not_evaluable_missing_nfl_schedule"], "missing_inputs": ["nfl schedule"]}
    if not opponent_strength_available or raw_modifier is None:
        return {"modifier": 0.0, "reason_codes": ["not_evaluable_missing_matchup_strength"], "missing_inputs": ["matchup strength"]}
    cap_name = "def_streaming_matchup" if position.upper() == "DEF" else "matchup"
    return {"modifier": cap_modifier(cap_name, raw_modifier, position=position), "reason_codes": [], "missing_inputs": []}


def spike_reason_codes(position: str, row: Mapping[str, Any]) -> list[str]:
    """Classify position-specific one-week spikes from normalized row fields."""

    position = position.upper()
    points = float(row.get("points") or row.get("fantasy_points") or 0)
    thresholds = SPIKE_THRESHOLDS[position]
    codes: list[str] = []
    if position == "RB" and points >= 20:
        if row.get("weighted_touches") is None:
            codes.append("not_evaluable_missing_weighted_touches")
        elif int(row["weighted_touches"]) <= thresholds["low_touch_floor"]["weighted_touches_max"]:
            codes.append("low_touch_big_points")
    if position in {"WR", "TE"} and points >= 15:
        if row.get("targets") is None:
            codes.append("not_evaluable_missing_targets")
        elif int(row["targets"]) <= thresholds["low_target_floor"]["targets_max"]:
            codes.append("low_target_big_points")
    td_count = int(row.get("total_tds", 0) or row.get("rec_tds", 0) or row.get("pass_tds", 0) or row.get("passing_tds", 0))
    if position in {"RB", "WR", "TE", "QB"} and td_count >= (thresholds.get("total_tds") or thresholds.get("rec_tds") or thresholds.get("passing_tds"))["min"]:
        opportunity_field = {"RB": "weighted_touches", "WR": "targets", "TE": "targets", "QB": "rush_attempts"}[position]
        if row.get(opportunity_field) is None:
            codes.append(f"not_evaluable_missing_{opportunity_field}")
        else:
            codes.append("td_only_low_usage")
    long_kick_share = row.get("long_kick_point_share")
    if position == "K" and (int(row.get("made_50_plus") or 0) >= thresholds["made_50_plus"]["min"] or (long_kick_share is not None and float(long_kick_share) >= thresholds["long_kick_point_share"]["min"])):
        codes.append("long_kick_spike")
    if position == "DEF" and int(row.get("def_or_special_teams_tds", 0)) >= thresholds["def_or_special_teams_tds"]["min"]:
        codes.append("def_td_spike")
    if position == "DEF" and int(row.get("takeaways", 0)) >= thresholds["takeaways"]["min"]:
        if row.get("pressure_proxy_rise") is None:
            codes.append("not_evaluable_missing_pressure_proxy_rise")
        elif not row["pressure_proxy_rise"]:
            codes.append("turnover_spike_without_pressure")
    return list(dict.fromkeys(codes))


def cap_modifier(cap_name: str, value: float, *, position: str | None = None) -> float:
    caps = POSITION_MODIFIER_CAPS.get(position.upper(), MODIFIER_CAPS) if position else MODIFIER_CAPS
    if cap_name not in caps:
        raise KeyError(f"unknown modifier cap: {cap_name}")
    cap = caps[cap_name]
    bounded = max(Decimal(str(cap["min"])), min(Decimal(str(cap["max"])), Decimal(str(value))))
    return float(bounded.quantize(Decimal("0.01")))


_OPPORTUNITY_METRIC_BY_POSITION = {
    "QB": "dropbacks",
    "RB": "weighted_touches",
    "WR": "targets",
    "TE": "targets",
}
_REASON_MODIFIERS = {
    "role_change_recent": ("context", 4.0),
    "role_loss_recent": ("context", -4.0),
    # Bounded offset: role_change_recent (+4.0) + injury_replacement (-2.0) nets
    # +2.0, lower than a durable role change.
    "injury_replacement": ("context", -2.0),
    "projection_lagging_role_change": ("trend", 4.0),
    "season_average_stale": ("context", -3.0),
    "low_touch_big_points": ("context", -3.0),
    "low_target_big_points": ("context", -3.0),
    "td_only_low_usage": ("context", -3.0),
    "def_td_spike": ("context", -3.0),
    "turnover_spike_without_pressure": ("context", -3.0),
    "long_kick_spike": ("context", -4.5),
    "current_metadata_only_depth_chart": ("context", 2.0),
}
# Public read-only view for consumers (usage_context_integration) that decompose
# the context modifier by reason code.
REASON_MODIFIERS: Mapping[str, tuple[str, float]] = _REASON_MODIFIERS


def evaluate_player_usage_context(position: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the deterministic V1 rule subset used by the golden fixtures."""

    position = position.upper()
    recent = list(inputs.get("recent_weeks", ()))
    season = list(inputs.get("season_weeks", ()))
    rows = season + recent
    reason_codes: list[str] = []
    modifiers: dict[str, float] = {}
    opportunity_metric = _OPPORTUNITY_METRIC_BY_POSITION.get(position) or {
        "K": "total_kick_attempts",
        "DEF": "sacks",
    }.get(position)
    change_point = detect_change_point(position, rows, metric=opportunity_metric)
    trend = {"name": "last_2_weeks", "weeks": [int(row["week"]) for row in _eligible_rows(recent)], "baseline_weeks": [int(row["week"]) for row in _eligible_rows(season)], "change_point": change_point}
    signal: dict[str, Any] | None = None
    if rows and opportunity_metric in POSITION_USAGE_THRESHOLDS.get(position, {}):
        if change_point["status"] == "change_detected":
            post_rows, pre_rows = post_change_rows(rows, change_point)
            signal = opportunity_signal(position, rows, metric=opportunity_metric, recent_rows=post_rows, baseline_rows=pre_rows)
            trend = {"name": "post_change_window", "weeks": change_point["post_change_weeks"], "baseline_weeks": change_point["pre_change_weeks"], "change_point": change_point}
        else:
            signal = opportunity_signal(position, rows, metric=opportunity_metric)
            trend["name"] = POSITION_USAGE_THRESHOLDS[position][opportunity_metric]["window"]
            trend["weeks"] = [int(row["week"]) for row in signal["recent"]]
            trend["baseline_weeks"] = [int(row["week"]) for row in signal["baseline"]]
        if signal["status"] == "increased":
            reason_codes.append("role_change_recent")
        elif signal["status"] == "decreased":
            reason_codes.append("role_loss_recent")
        elif signal["status"].startswith("not_evaluable_missing_"):
            reason_codes.extend(signal["reason_codes"])

    injury = None
    if "role_change_recent" in reason_codes and position in INJURY_REPLACEMENT_POSITIONS and "teammate_availability" in inputs:
        injury = evaluate_injury_replacement(position, trend["weeks"], inputs.get("teammate_availability"), metric=opportunity_metric)
        if injury["status"] == "injury_replacement":
            reason_codes.append("injury_replacement")
        elif injury["status"] == "not_evaluable_missing_teammate_availability":
            reason_codes.append("not_evaluable_missing_teammate_availability")

    if any(row.get("projection_delta") is not None and float(row["projection_delta"]) < 0 for row in recent) and "role_change_recent" in reason_codes:
        reason_codes.append("projection_lagging_role_change")
    recent_points = _average(_eligible_rows(recent), "points")
    season_points = _average(_eligible_rows(season), "points")
    if recent_points is not None and season_points is not None and recent_points < season_points:
        if len(_eligible_rows(recent)) >= MINIMUM_SAMPLE_REQUIREMENTS["season_average_stale"]["recent"] and len(_eligible_rows(season)) >= MINIMUM_SAMPLE_REQUIREMENTS["season_average_stale"]["baseline"]:
            reason_codes.append("season_average_stale")

    spike_rows = trend_window(recent, "season_to_date")
    if spike_rows:
        reason_codes.extend(spike_reason_codes(position, spike_rows[-1]))
    metadata = inputs.get("current_metadata", {})
    if metadata.get("depth_chart_role"):
        reason_codes.append("current_metadata_only_depth_chart")

    matchup = inputs.get("matchup")
    if matchup is not None:
        matchup_result = compute_matchup_modifier(position, matchup.get("raw_modifier"), nfl_schedule_available=bool(matchup.get("nfl_schedule_available", False)), opponent_strength_available=bool(matchup.get("opponent_strength_available", True)))
        modifiers["matchup"] = matchup_result["modifier"]
        reason_codes.extend(matchup_result["reason_codes"])

    for code in reason_codes:
        cap_name, amount = _REASON_MODIFIERS.get(code, (None, 0.0))
        if cap_name:
            modifiers[cap_name] = modifiers.get(cap_name, 0.0) + amount
    capped_modifiers = {name: cap_modifier(name, value, position=position) for name, value in modifiers.items()}
    reasons = [deepcopy(REASON_CODES[code]) for code in reason_codes if code in REASON_CODES]
    reasons.extend(_missing_metric_reason(code.removeprefix("not_evaluable_missing_")) for code in reason_codes if code not in REASON_CODES and code.startswith("not_evaluable_missing_"))
    return {"reason_codes": reason_codes, "reasons": reasons, "modifiers": capped_modifiers, "capped_modifiers": capped_modifiers, "trend_window": trend, "injury_replacement": injury}


INJURY_REPLACEMENT_POSITIONS = ("RB", "WR", "TE")
INJURY_REPLACEMENT_POLICY: dict[str, Any] = {
    "positions": list(INJURY_REPLACEMENT_POSITIONS),
    "trigger": "role_change_recent (an opportunity increase) for RB/WR/TE",
    "evidence_source": "player_week_availability (week-specific availability snapshots; the row's own source and snapshot_at are reported)",
    "absence": "teammate row status in {out, inactive, ir, reserve} or injury_status in {out, ir} (the same predicate that marks a player's own week inactive)",
    "teammate_match": "same team for that week and same players.position as the evaluated player",
    "relevance": "the teammate's mean actual opportunity metric over the pre-change baseline weeks must be at least the position's increase_per_game threshold (RB 5.0 weighted touches, WR 2.5 targets, TE 2.0 targets)",
    "coincidence": "the same qualifying teammate is absent in every trend-window week",
    "missing": "if any trend-window week has no availability rows for the team, emit not_evaluable_missing_teammate_availability and apply no modifier",
    "modifier": "injury_replacement adds context -2.0 on top of role_change_recent (+4.0), so the net bounded context modifier is +2.0, lower than a durable role change",
}


def evaluate_injury_replacement(position: str, trend_weeks: Sequence[int], teammate_availability: Mapping[str, Any] | None, *, metric: str | None) -> dict[str, Any]:
    """Decide whether a role increase is covering a same-position teammate's absence.

    ``teammate_availability`` is built by the service from player_week_availability:
    ``{"evidence_source": str, "weeks": [{"week", "availability_present", "absent_teammates": [...]}]}``.
    Each absent teammate carries ``player_id``, ``status``, ``injury_status``,
    ``source``, ``snapshot_at``, and ``baseline_opportunity_per_game``.
    """

    position = position.upper()
    threshold = POSITION_USAGE_THRESHOLDS.get(position, {}).get(metric or "", {})
    relevance_floor = threshold.get("increase_per_game")
    result: dict[str, Any] = {"status": "no_teammate_absence", "trend_weeks": [int(week) for week in trend_weeks], "evidence_source": None, "missing_weeks": [], "teammates": [], "relevance_floor": relevance_floor, "policy": deepcopy(INJURY_REPLACEMENT_POLICY)}
    if not isinstance(teammate_availability, Mapping):
        result["status"] = "not_evaluable_missing_teammate_availability"
        result["missing_weeks"] = list(result["trend_weeks"])
        return result
    result["evidence_source"] = teammate_availability.get("evidence_source")
    by_week = {int(entry["week"]): entry for entry in teammate_availability.get("weeks", []) if entry.get("week") is not None}
    missing = [week for week in result["trend_weeks"] if not by_week.get(week, {}).get("availability_present")]
    if missing or not result["trend_weeks"]:
        result["status"] = "not_evaluable_missing_teammate_availability"
        result["missing_weeks"] = missing
        return result
    absent_sets = []
    details: dict[str, list[dict[str, Any]]] = {}
    for week in result["trend_weeks"]:
        qualifying = set()
        for teammate in by_week[week].get("absent_teammates", []):
            baseline = teammate.get("baseline_opportunity_per_game")
            if baseline is None or relevance_floor is None or Decimal(str(baseline)) < Decimal(str(relevance_floor)):
                continue
            teammate_id = str(teammate["player_id"])
            qualifying.add(teammate_id)
            details.setdefault(teammate_id, []).append({**dict(teammate), "week": week})
        absent_sets.append(qualifying)
    covered = sorted(set.intersection(*absent_sets)) if absent_sets else []
    if covered:
        result["status"] = "injury_replacement"
        result["teammates"] = [{"player_id": teammate_id, "weeks": details[teammate_id]} for teammate_id in covered]
    return result


DERIVED_METRICS_SCHEMA_VERSION = "player_usage_derived_metrics.v2"
DERIVED_METRIC_ROUNDING = 4
DERIVED_METRICS_POLICY: dict[str, str] = {
    "source": "Derived metrics use only rows from the actual `stats` source. Projection rows are never inputs.",
    "classification": "Every derived metric is classified `actual`.",
    "input_keys": "Each metric reads exact Sleeper stat keys from the normalized row; aliases are never summed.",
    "absent_numerator": "Sleeper omits zero-valued stat keys, so an absent numerator key in a present stats row counts as 0. `input_key_presence` records how many window rows actually had each key.",
    "absent_denominator": "A ratio sums numerator and denominator over the window rows where the denominator is present (ratio of sums, not mean of weekly ratios). No such row gives value null with `not_evaluable_missing_<denominator>`; never 0.",
    "composite_denominator": "A composite denominator such as `dropbacks` (pass_att + pass_sack) or `touches` (rush_att + rec) is present in a row when at least one component key is present; absent components count as 0.",
    "zero_denominator": "A denominator that sums to 0 gives value null with reason `denominator_zero`; never 0.",
    "max": "Longest-play metrics take the maximum over rows that have the key; none gives `not_evaluable_missing_<key>`.",
    "empty_window": "A window with no eligible actual rows gives value null with `not_evaluable_missing_actual_stats`.",
    "absolute_counts": "off_snp, st_snp, rec_air_yd, and pass_air_yd are absolute counts, never shares. actual_snap_share_proxy (off_snp / tm_off_snp) is the only share-like value and is a team-snap proxy, not a position-specific snap share.",
    "rounding": f"Ratio and per-game values are rounded to {DERIVED_METRIC_ROUNDING} decimal places.",
}


def _count_metric(name: str, keys: Sequence[str], formula: str | None = None, *, label: str = "absolute_count") -> dict[str, Any]:
    return {"name": name, "kind": "count", "label": label, "formula": formula or " + ".join(keys), "input_keys": list(keys), "denominator_key": None, "denominator_input_keys": []}


def _max_metric(name: str, key: str) -> dict[str, Any]:
    return {"name": name, "kind": "max", "label": "absolute_max", "formula": f"max({key})", "input_keys": [key], "denominator_key": None, "denominator_input_keys": []}


def _ratio_metric(name: str, numerator: Sequence[str], denominator_key: str, denominator_keys: Sequence[str], *, label: str = "rate") -> dict[str, Any]:
    numerator_text = " + ".join(numerator)
    denominator_text = " + ".join(denominator_keys)
    if len(numerator) > 1:
        numerator_text = f"({numerator_text})"
    if len(denominator_keys) > 1:
        denominator_text = f"({denominator_text})"
    return {"name": name, "kind": "ratio", "label": label, "formula": f"sum({numerator_text}) / sum({denominator_text})", "input_keys": list(numerator), "numerator_keys": list(numerator), "denominator_key": denominator_key, "denominator_input_keys": list(denominator_keys)}


DERIVED_METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    definition["name"]: definition
    for definition in (
        # QB passing volume and efficiency.
        _count_metric("pass_attempts", ("pass_att",)),
        _count_metric("pass_completions", ("pass_cmp",)),
        _count_metric("pass_yards", ("pass_yd",)),
        _count_metric("pass_tds", ("pass_td",)),
        _count_metric("pass_interceptions", ("pass_int",)),
        _count_metric("sacks_taken", ("pass_sack",)),
        _count_metric("dropbacks", ("pass_att", "pass_sack")),
        _ratio_metric("completion_rate", ("pass_cmp",), "pass_att", ("pass_att",)),
        _ratio_metric("yards_per_dropback", ("pass_yd",), "dropbacks", ("pass_att", "pass_sack")),
        _count_metric("pass_air_yards", ("pass_air_yd",)),
        # Rushing.
        _count_metric("rush_attempts", ("rush_att",)),
        _count_metric("rush_yards", ("rush_yd",)),
        _count_metric("rush_tds", ("rush_td",)),
        _ratio_metric("rush_yards_per_attempt", ("rush_yd",), "rush_att", ("rush_att",)),
        _count_metric("red_zone_rush_attempts", ("rush_rz_att",)),
        _count_metric("rush_yards_after_contact", ("rush_yac",)),
        _count_metric("rush_broken_tackles", ("rush_btkl",)),
        _max_metric("longest_rush", "rush_lng"),
        # Receiving.
        _count_metric("targets", ("rec_tgt",)),
        _count_metric("receptions", ("rec",)),
        _count_metric("rec_yards", ("rec_yd",)),
        _count_metric("rec_tds", ("rec_td",)),
        _count_metric("red_zone_targets", ("rec_rz_tgt",)),
        _count_metric("rec_air_yards", ("rec_air_yd",)),
        _count_metric("drops", ("rec_drop",)),
        _ratio_metric("catch_rate", ("rec",), "rec_tgt", ("rec_tgt",)),
        _ratio_metric("yards_per_target", ("rec_yd",), "rec_tgt", ("rec_tgt",)),
        # RB workload. `touches` is evidence; `weighted_touches` is the RB opportunity metric used by the rules.
        _count_metric("touches", ("rush_att", "rec")),
        _count_metric("weighted_touches", ("rush_att", "rec_tgt")),
        _ratio_metric("yards_per_touch", ("rush_yd", "rec_yd"), "touches", ("rush_att", "rec")),
        # Ball security.
        _count_metric("fumbles", ("fum",)),
        _count_metric("fumbles_lost", ("fum_lost",)),
        # Snaps: absolute counts plus one explicitly named proxy.
        _count_metric("offensive_snaps", ("off_snp",)),
        _count_metric("special_teams_snaps", ("st_snp",)),
        _ratio_metric("actual_snap_share_proxy", ("off_snp",), "tm_off_snp", ("tm_off_snp",), label="proxy"),
        # Kicking.
        _count_metric("fg_attempts", ("fga",)),
        _count_metric("fg_made", ("fgm",)),
        _count_metric("fg_missed", ("fgmiss",)),
        _count_metric("xp_attempts", ("xpa",)),
        _count_metric("xp_made", ("xpm",)),
        _count_metric("total_kick_attempts", ("fga", "xpa")),
        _count_metric("total_kick_makes", ("fgm", "xpm")),
        *(_count_metric(key, (key,)) for key in ("fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50_59", "fgm_60p", "fgm_50p", "fgmiss_20_29", "fgmiss_30_39", "fgmiss_40_49", "fgmiss_50_59", "fgmiss_60p", "fgmiss_50p")),
        _max_metric("longest_fg", "fgm_lng"),
        # Team defense.
        _count_metric("sacks", ("sack",)),
        _count_metric("qb_hits", ("qb_hit",)),
        _count_metric("tackles_for_loss", ("tkl_loss",)),
        _count_metric("interceptions", ("int",)),
        _count_metric("fumble_recoveries", ("fum_rec",)),
        _count_metric("takeaways", ("int", "fum_rec")),
        _count_metric("def_tds", ("def_td",)),
        _count_metric("def_st_tds", ("def_st_td",)),
        _count_metric("points_allowed", ("pts_allow",)),
        _count_metric("yards_allowed", ("yds_allow",)),
    )
}

_SNAP_METRICS = ("offensive_snaps", "special_teams_snaps", "actual_snap_share_proxy")
_RECEIVER_METRICS = ("targets", "receptions", "catch_rate", "yards_per_target", "rec_yards", "rec_tds", "red_zone_targets", "rec_air_yards", "drops")
_RUSH_PRODUCTION_METRICS = ("rush_attempts", "rush_yards", "rush_tds")
_BALL_SECURITY_METRICS = ("fumbles", "fumbles_lost")
POSITION_DERIVED_METRICS: dict[str, tuple[str, ...]] = {
    "QB": ("dropbacks", "pass_attempts", "pass_completions", "completion_rate", "pass_yards", "yards_per_dropback", "pass_tds", "pass_interceptions", "sacks_taken", "pass_air_yards", *_RUSH_PRODUCTION_METRICS, *_BALL_SECURITY_METRICS, "offensive_snaps", "actual_snap_share_proxy"),
    "RB": (*_RUSH_PRODUCTION_METRICS, "receptions", "targets", "touches", "weighted_touches", "rec_yards", "rec_tds", "yards_per_touch", "rush_yards_per_attempt", "red_zone_rush_attempts", "red_zone_targets", "rush_yards_after_contact", "rush_broken_tackles", "longest_rush", "rec_air_yards", "drops", *_BALL_SECURITY_METRICS, *_SNAP_METRICS),
    "WR": (*_RECEIVER_METRICS, *_RUSH_PRODUCTION_METRICS, *_BALL_SECURITY_METRICS, *_SNAP_METRICS),
    "TE": (*_RECEIVER_METRICS, *_RUSH_PRODUCTION_METRICS, *_BALL_SECURITY_METRICS, *_SNAP_METRICS),
    "K": ("fg_attempts", "fg_made", "fg_missed", "xp_attempts", "xp_made", "total_kick_attempts", "total_kick_makes", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50_59", "fgm_60p", "fgm_50p", "fgmiss_20_29", "fgmiss_30_39", "fgmiss_40_49", "fgmiss_50_59", "fgmiss_60p", "fgmiss_50p", "longest_fg", "special_teams_snaps"),
    "DEF": ("sacks", "qb_hits", "tackles_for_loss", "interceptions", "fumble_recoveries", "takeaways", "def_tds", "def_st_tds", "points_allowed", "yards_allowed"),
}


def _round_metric(value: float) -> float:
    return float(round(Decimal(str(value)), DERIVED_METRIC_ROUNDING))


def _row_stats(row: Mapping[str, Any]) -> Mapping[str, Any]:
    stats = row.get("raw_stats")
    return stats if isinstance(stats, Mapping) else {}


def _key_presence(rows: Sequence[Mapping[str, Any]], keys: Iterable[str]) -> dict[str, int]:
    return {key: sum(1 for row in rows if _row_stats(row).get(key) is not None) for key in keys}


def _sum_keys(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> float:
    return sum(float(_row_stats(row).get(key) or 0) for row in rows for key in keys)


def compute_derived_metric(name: str, rows: Sequence[Mapping[str, Any]], *, window: str) -> dict[str, Any]:
    """Compute one derived metric from exact stat keys over an actual-stats window."""

    definition = DERIVED_METRIC_DEFINITIONS[name]
    rows = [row for row in rows if row.get("source", "stats") == "stats"]
    input_keys = list(definition["input_keys"])
    denominator_keys = list(definition["denominator_input_keys"])
    record: dict[str, Any] = {
        "name": name,
        "kind": definition["kind"],
        "label": definition["label"],
        "formula": definition["formula"],
        "input_keys": input_keys,
        "input_key_presence": _key_presence(rows, input_keys),
        "denominator_key": definition["denominator_key"],
        "denominator_input_keys": denominator_keys,
        "denominator_value": None,
        "denominator_valid": None,
        "denominator_weeks": [],
        "value": None,
        "reason": None,
        "classification": "actual",
        "source": "stats",
        "window": window,
        "weeks": [int(row["week"]) for row in rows if row.get("week") is not None],
        "sample_size": len(rows),
    }
    if definition["kind"] == "count":
        record["per_game"] = None
    if definition["kind"] == "ratio":
        record["denominator_valid"] = False
    if not rows:
        record["reason"] = "not_evaluable_missing_actual_stats"
        return record
    if definition["kind"] == "count":
        total = _sum_keys(rows, input_keys)
        record["value"] = _round_metric(total)
        record["per_game"] = _round_metric(total / len(rows))
        return record
    if definition["kind"] == "max":
        key = input_keys[0]
        values = [float(_row_stats(row)[key]) for row in rows if _row_stats(row).get(key) is not None]
        if not values:
            record["reason"] = f"not_evaluable_missing_{key}"
        else:
            record["value"] = _round_metric(max(values))
        return record
    denominator_rows = [row for row in rows if any(_row_stats(row).get(key) is not None for key in denominator_keys)]
    record["denominator_weeks"] = [int(row["week"]) for row in denominator_rows if row.get("week") is not None]
    record["denominator_key_presence"] = _key_presence(rows, denominator_keys)
    if not denominator_rows:
        record["reason"] = f"not_evaluable_missing_{definition['denominator_key']}"
        return record
    denominator = _sum_keys(denominator_rows, denominator_keys)
    record["denominator_value"] = _round_metric(denominator)
    if denominator == 0:
        record["reason"] = "denominator_zero"
        return record
    record["denominator_valid"] = True
    record["value"] = _round_metric(_sum_keys(denominator_rows, definition["numerator_keys"]) / denominator)
    return record


def compute_derived_metrics(position: str, actual_rows: Iterable[Mapping[str, Any]], *, change_point: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the versioned derived-metrics evidence block for one player.

    Only actual `stats` rows are accepted; projection rows are dropped before
    any window is formed so a forecast can never be labeled observed usage.
    ``post_change_window`` is added only when ``change_point`` reports a
    detected change.
    """

    position = position.upper()
    actual = [row for row in actual_rows if row.get("source", "stats") == "stats"]
    windows: dict[str, Any] = {}
    window_rows: list[tuple[str, dict[str, Any], list[Mapping[str, Any]]]] = [(name, definition, trend_window(actual, name)) for name, definition in TREND_WINDOWS.items()]
    if change_point and change_point.get("status") == "change_detected":
        window_rows.append(("post_change_window", POST_CHANGE_WINDOW, post_change_rows(actual, change_point)[0]))
    for window_name, window_definition, rows in window_rows:
        windows[window_name] = {
            "weeks": [int(row["week"]) for row in rows],
            "sample_size": len(rows),
            "minimum_samples": window_definition["minimum_samples"],
            "sufficient_sample": len(rows) >= window_definition["minimum_samples"],
            "metrics": {name: compute_derived_metric(name, rows, window=window_name) for name in POSITION_DERIVED_METRICS[position]},
        }
    return {
        "schema_version": DERIVED_METRICS_SCHEMA_VERSION,
        "contract_version": PLAYER_USAGE_CONTEXT_SCHEMA_VERSION,
        "position": position,
        "source": "stats",
        "classification": "actual",
        "projection_rows_used": False,
        "implementation": "python_only",
        "metric_names": list(POSITION_DERIVED_METRICS[position]),
        "policy": deepcopy(DERIVED_METRICS_POLICY),
        "windows": windows,
    }


def td_scoring_key_family(stat_key: str) -> str | None:
    """Classify a league scoring-contribution key; None when it is not TD value."""

    key = str(stat_key)
    if key in LONG_FIELD_GOAL_SCORING_KEYS:
        return "long_field_goal"
    if key in DEF_TURNOVER_SCORING_KEYS:
        return "turnover"
    if key in TD_SCORING_KEY_EXCLUSIONS or not (key.endswith("_td") or "_td_" in key):
        return None
    stripped = key.removeprefix("bonus_")
    if stripped.startswith("pass_td"):
        return "passing_td"
    if stripped.startswith("rush_td"):
        return "rushing_td"
    if stripped.startswith("rec_td"):
        return "receiving_td"
    if stripped.startswith(("def_", "st_")):
        return "defense_special_teams_td"
    return "other_td"


_TD_FAMILIES = ("passing_td", "rushing_td", "receiving_td", "defense_special_teams_td", "other_td")


def _row_contributions(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    contributions = row.get("scoring_contributions")
    if not isinstance(contributions, Mapping) or row.get("league_fantasy_points") is None:
        return None
    return contributions


def _long_fg_not_separable(row: Mapping[str, Any], contributions: Mapping[str, Any]) -> bool:
    stats = _row_stats(row)
    long_makes = float(stats.get("fgm_50p") or 0) or (float(stats.get("fgm_50_59") or 0) + float(stats.get("fgm_60p") or 0))
    has_bucket_points = any(float(contributions.get(key) or 0) != 0 for key in LONG_FIELD_GOAL_SCORING_KEYS)
    has_yardage_points = any(float(contributions.get(key) or 0) != 0 for key in FIELD_GOAL_YARDAGE_SCORING_KEYS)
    return long_makes > 0 and not has_bucket_points and has_yardage_points


def compute_td_dependency(position: str, rows: Sequence[Mapping[str, Any]], *, window: str) -> dict[str, Any]:
    """Evaluate TD dependency from league scoring contributions (no heuristic).

    Each row must carry ``league_fantasy_points`` (player_week_rows.fantasy_points,
    non-null only when league scoring ran at sync time) and
    ``scoring_contributions`` ({stat_key: points} from player_week_scoring_values).
    Zero-point keys are omitted by calculate_fantasy_points, so an empty mapping
    is a valid "no TD points" row. A row without either field is missing.
    """

    position = position.upper()
    formula = TD_DEPENDENCY_FORMULAS[position]
    rows = [row for row in rows if row.get("source", "stats") == "stats"]
    weeks = [int(row["week"]) for row in rows if row.get("week") is not None]
    missing_weeks = [int(row["week"]) for row in rows if _row_contributions(row) is None]
    record: dict[str, Any] = {
        "method": TD_DEPENDENCY_METHOD,
        "source": TD_DEPENDENCY_SOURCE,
        "classification": "actual",
        "score_formula": TD_DEPENDENCY_SCORE_FORMULA,
        "window": window,
        "weeks": weeks,
        "sample_size": len(rows),
        "missing_contribution_weeks": missing_weeks,
        "td_scoring_keys_used": [],
        "excluded_keys_present": [],
        "td_points": None,
        "fantasy_points": None,
        "value": None,
        "reason": None,
        "position_formula": {"position": position, "formula": formula["formula"], "numerator_families": list(formula["numerator_families"]), "numerator_keys_used": [], "numerator_points": None, "denominator_points": None, "value": None, "dependency_threshold": formula["dependency_threshold"], "above_threshold": None, "reason": None},
    }
    position_record = record["position_formula"]
    if not rows:
        record["reason"] = position_record["reason"] = "not_evaluable_missing_actual_stats"
        return record
    if missing_weeks:
        record["reason"] = position_record["reason"] = "not_evaluable_missing_scoring_contributions"
        return record
    td_points = 0.0
    numerator_points = 0.0
    td_keys: set[str] = set()
    numerator_keys: set[str] = set()
    excluded: set[str] = set()
    not_separable = False
    for row in rows:
        contributions = _row_contributions(row) or {}
        if position == "K" and _long_fg_not_separable(row, contributions):
            not_separable = True
        for key, points in contributions.items():
            value = float(points or 0)
            if value == 0:
                continue
            family = td_scoring_key_family(key)
            if key in TD_SCORING_KEY_EXCLUSIONS:
                excluded.add(key)
            if family in _TD_FAMILIES:
                td_points += value
                td_keys.add(key)
            if family in formula["numerator_families"]:
                numerator_points += value
                numerator_keys.add(key)
    denominator = sum(float(row["league_fantasy_points"]) for row in rows)
    record["td_scoring_keys_used"] = sorted(td_keys)
    record["excluded_keys_present"] = sorted(excluded)
    record["td_points"] = _round_metric(td_points)
    record["fantasy_points"] = _round_metric(denominator)
    position_record["numerator_keys_used"] = sorted(numerator_keys)
    position_record["numerator_points"] = _round_metric(numerator_points)
    position_record["denominator_points"] = _round_metric(denominator)
    if denominator <= 0:
        record["reason"] = position_record["reason"] = "denominator_non_positive"
        return record
    record["value"] = _round_metric(td_points / denominator)
    if not_separable:
        # The league scores field goals by yardage (fgm_yds) rather than distance
        # buckets, so long-kick points cannot be isolated from other kicks.
        position_record["reason"] = "not_evaluable_long_field_goal_points_not_separable"
        return record
    position_record["value"] = _round_metric(numerator_points / denominator)
    position_record["above_threshold"] = position_record["value"] >= formula["dependency_threshold"]
    return record


def td_dependency_score(record: Mapping[str, Any]) -> float | None:
    value = record.get("value")
    if value is None:
        return None
    return round(max(0.0, min(100.0, float(value) * 100.0)), 2)


PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES: tuple[dict[str, Any], ...] = (
    {"id": "wr_high_points_low_usage", "case": "high_points_with_low_usage", "position": "WR", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 3, "points": 24, "rec_tds": 2}, {"week": 5, "active": True, "targets": 3, "points": 24, "rec_tds": 2}], "season_weeks": [{"week": 1, "active": True, "targets": 7, "points": 12}, {"week": 2, "active": True, "targets": 8, "points": 13}, {"week": 3, "active": True, "targets": 7, "points": 12}]}, "expected": {"reason_codes": ["role_loss_recent", "low_target_big_points", "td_only_low_usage"], "capped_modifiers": {"context": -6.0}}},
    {"id": "rb_rising_usage_lagging_projection", "case": "rising_usage_with_lagging_projections", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "weighted_touches": 19, "projection_delta": -2}, {"week": 5, "active": True, "weighted_touches": 20, "projection_delta": -2}], "season_weeks": [{"week": 1, "active": True, "weighted_touches": 14}, {"week": 2, "active": True, "weighted_touches": 14}, {"week": 3, "active": True, "weighted_touches": 14}]}, "expected": {"reason_codes": ["role_change_recent", "projection_lagging_role_change"], "capped_modifiers": {"trend": 4.0, "context": 4.0}}},
    {"id": "qb_high_projection_weak_actuals", "case": "high_projections_with_weak_actuals", "position": "QB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "dropbacks": 25, "points": 11, "projected_points": 23}, {"week": 5, "active": True, "dropbacks": 25, "points": 11, "projected_points": 23}], "season_weeks": [{"week": 1, "active": True, "dropbacks": 35, "points": 18}, {"week": 2, "active": True, "dropbacks": 36, "points": 19}, {"week": 3, "active": True, "dropbacks": 34, "points": 18}]}, "expected": {"reason_codes": ["role_loss_recent", "season_average_stale"], "capped_modifiers": {"context": -6.0}}},
    {"id": "rb_injury_fill_in", "case": "injury_fill_in", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "weighted_touches": 17}, {"week": 5, "active": True, "weighted_touches": 17}], "season_weeks": [{"week": 1, "active": True, "weighted_touches": 6}, {"week": 2, "active": True, "weighted_touches": 7}, {"week": 3, "active": True, "weighted_touches": 6}], "teammate_availability": {"evidence_source": "player_week_availability", "weeks": [{"week": week, "availability_present": True, "absent_teammates": [{"player_id": "rb_starter", "position": "RB", "status": "Inactive", "injury_status": "Out", "source": "sleeper_players_current_metadata", "snapshot_at": 1760000000.0 + week, "baseline_opportunity_per_game": 18.0}]} for week in (4, 5)]}}, "expected": {"reason_codes": ["role_change_recent", "injury_replacement"], "capped_modifiers": {"context": 2.0}}},
    {"id": "rb_role_change_week4_eval_week7", "case": "post_change_window", "position": "RB", "inputs": {"recent_weeks": [{"week": 6, "active": True, "weighted_touches": 14}, {"week": 7, "active": True, "weighted_touches": 14}], "season_weeks": [{"week": 1, "active": True, "weighted_touches": 10}, {"week": 2, "active": True, "weighted_touches": 10}, {"week": 3, "active": True, "weighted_touches": 10}, {"week": 4, "active": True, "weighted_touches": 14}, {"week": 5, "active": True, "weighted_touches": 14}]}, "expected": {"reason_codes": ["role_change_recent"], "capped_modifiers": {"context": 4.0}, "trend_window": {"name": "post_change_window", "weeks": [4, 5, 6, 7], "baseline_weeks": [1, 2, 3]}}},
    {"id": "te_buried_depth_breakout", "case": "buried_depth_chart_breakout", "position": "TE", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 5, "red_zone_targets": 1}, {"week": 5, "active": True, "targets": 5, "red_zone_targets": 1}], "season_weeks": [{"week": 1, "active": True, "targets": 2}, {"week": 2, "active": True, "targets": 2}, {"week": 3, "active": True, "targets": 2}], "current_metadata": {"depth_chart_role": "third_te"}}, "expected": {"reason_codes": ["role_change_recent", "current_metadata_only_depth_chart"], "capped_modifiers": {"context": 6.0}}},
    {"id": "k_long_kick_spike", "case": "k_long_kick_spike", "position": "K", "inputs": {"recent_weeks": [{"week": 4, "active": True, "total_kick_attempts": 3, "made_50_plus": 2, "long_kick_point_share": 0.56}, {"week": 5, "active": True, "total_kick_attempts": 3, "made_50_plus": 2, "long_kick_point_share": 0.56}], "season_weeks": [{"week": 1, "active": True, "total_kick_attempts": 3, "made_50_plus": 0}, {"week": 2, "active": True, "total_kick_attempts": 3, "made_50_plus": 0}, {"week": 3, "active": True, "total_kick_attempts": 3, "made_50_plus": 0}]}, "expected": {"reason_codes": ["long_kick_spike"], "capped_modifiers": {"context": -4.5}}},
    {"id": "def_td_turnover_spike", "case": "def_touchdown_or_turnover_spike", "position": "DEF", "inputs": {"recent_weeks": [{"week": 4, "active": True, "def_or_special_teams_tds": 1, "takeaways": 4, "sacks": 1, "pressure_proxy_rise": False}, {"week": 5, "active": True, "def_or_special_teams_tds": 1, "takeaways": 4, "sacks": 1, "pressure_proxy_rise": False}], "season_weeks": [{"week": 1, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}, {"week": 2, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}, {"week": 3, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}], "matchup": {"raw_modifier": 4, "nfl_schedule_available": True}}, "expected": {"reason_codes": ["def_td_spike", "turnover_spike_without_pressure"], "capped_modifiers": {"context": -6.0, "matchup": 3.0}}},
    {"id": "qb_missing_matchup_source", "case": "missing_matchup_source", "position": "QB", "inputs": {"matchup": {"raw_modifier": 2.75, "nfl_schedule_available": False}}, "expected": {"reason_codes": ["not_evaluable_missing_nfl_schedule"], "capped_modifiers": {"matchup": 0.0}}},
)


def reason_codes_by_component() -> dict[str, list[str]]:
    by_component = {component: [] for component in SCORE_COMPONENT_KEYS}
    for code, definition in REASON_CODES.items():
        by_component[definition["component"]].append(code)
    return {component: sorted(codes) for component, codes in by_component.items()}


def player_usage_context_contract() -> dict[str, Any]:
    return {
        "schema_version": PLAYER_USAGE_CONTEXT_SCHEMA_VERSION,
        "positions": list(POSITION_FAMILIES),
        "computability_tiers": deepcopy(COMPUTABILITY_TIERS),
        "trend_windows": deepcopy(TREND_WINDOWS),
        "minimum_sample_requirements": deepcopy(MINIMUM_SAMPLE_REQUIREMENTS),
        "modifier_caps": deepcopy(MODIFIER_CAPS),
        "position_modifier_caps": deepcopy(POSITION_MODIFIER_CAPS),
        "reason_code_schema": {"schema_version": PLAYER_USAGE_CONTEXT_SCHEMA_VERSION, "namespace": "usage", "fields": deepcopy(REASON_CODE_FIELDS), "codes": deepcopy(REASON_CODES)},
        "score_components": deepcopy(SCORE_COMPONENT_SCHEMA),
        "position_usage_thresholds": deepcopy(POSITION_USAGE_THRESHOLDS),
        "post_change_window": deepcopy(POST_CHANGE_WINDOW),
        "injury_replacement": deepcopy(INJURY_REPLACEMENT_POLICY),
        "td_dependency_formulas": deepcopy(TD_DEPENDENCY_FORMULAS),
        "td_dependency": {
            "method": TD_DEPENDENCY_METHOD,
            "source": TD_DEPENDENCY_SOURCE,
            "score_formula": TD_DEPENDENCY_SCORE_FORMULA,
            "td_scoring_key_rule": "contribution keys ending in '_td' or containing '_td_'",
            "excluded_keys": list(TD_SCORING_KEY_EXCLUSIONS),
            "long_field_goal_keys": list(LONG_FIELD_GOAL_SCORING_KEYS),
            "turnover_keys": list(DEF_TURNOVER_SCORING_KEYS),
            "missing": "not_evaluable_missing_scoring_contributions; no heuristic fallback",
            "implementation": "python and worker (worker labels td_dependency_method, and falls back to a labeled heuristic only when league scoring settings are unavailable)",
        },
        "spike_thresholds": deepcopy(SPIKE_THRESHOLDS),
        "golden_fixtures": deepcopy(list(PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES)),
        "derived_metrics": {
            "schema_version": DERIVED_METRICS_SCHEMA_VERSION,
            "implementation": "python_only",
            "policy": deepcopy(DERIVED_METRICS_POLICY),
            "definitions": deepcopy(DERIVED_METRIC_DEFINITIONS),
            "position_metrics": {position: list(names) for position, names in POSITION_DERIVED_METRICS.items()},
        },
    }
