from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

PLAYER_USAGE_CONTEXT_SCHEMA_VERSION = "player_usage_context.v1"
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
    "last_2_weeks": {"weeks": 2, "minimum_samples": 2, "eligible_weeks": "active_non_bye"},
    "last_3_weeks": {"weeks": 3, "minimum_samples": 3, "eligible_weeks": "active_non_bye"},
    "last_4_weeks": {"weeks": 4, "minimum_samples": 3, "eligible_weeks": "active_non_bye"},
    "season_to_date": {"weeks": None, "minimum_samples": 1, "eligible_weeks": "active_non_bye"},
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
    "RB": {"touches": {"increase_pct": 0.30, "increase_per_game": 5.0, "window": "last_2_weeks"}, "targets": {"increase_per_game": 2.0, "window": "last_2_weeks"}},
    "WR": {"targets": {"increase_pct": 0.30, "increase_per_game": 2.5, "window": "last_2_weeks"}},
    "TE": {"targets": {"increase_pct": 0.30, "increase_per_game": 2.0, "window": "last_2_weeks"}, "red_zone_targets": {"straight_weeks": 2, "window": "last_2_weeks"}},
    "K": {"total_kick_attempts": {"increase_pct": 0.25, "window": "last_3_weeks"}},
    "DEF": {"sacks": {"increase_pct": 0.30, "window": "last_3_weeks"}, "points_allowed": {"improvement_pct": 0.20, "window": "last_3_weeks", "lower_is_better": True}},
}

TD_DEPENDENCY_FORMULAS: dict[str, dict[str, Any]] = {
    "QB": {"formula": "passing_td_points / max(fantasy_points, 1)", "dependency_threshold": 0.45, "stability_counter_signal": "rush_attempts_per_game >= 4"},
    "RB": {"formula": "(rush_td_points + rec_td_points) / max(fantasy_points, 1)", "dependency_threshold": 0.35, "stability_counter_signal": "touches_per_game >= 14"},
    "WR": {"formula": "rec_td_points / max(fantasy_points, 1)", "dependency_threshold": 0.40, "stability_counter_signal": "targets_per_game >= 7"},
    "TE": {"formula": "rec_td_points / max(fantasy_points, 1)", "dependency_threshold": 0.45, "stability_counter_signal": "targets_per_game >= 5"},
    "K": {"formula": "long_field_goal_points / max(kicking_points, 1)", "dependency_threshold": 0.40, "stability_counter_signal": "total_kick_attempts_last_3_increase_pct >= 0.25"},
    "DEF": {"formula": "(def_td_points + turnover_points) / max(defense_points, 1)", "dependency_threshold": 0.50, "stability_counter_signal": "sacks_increase_pct >= 0.30 or points_allowed_improvement_pct >= 0.20"},
}
SPIKE_THRESHOLDS: dict[str, dict[str, Any]] = {
    "QB": {"passing_tds": {"min": 3}, "low_rushing_floor": {"rush_attempts_max": 3}},
    "RB": {"total_tds": {"min": 2}, "low_touch_floor": {"touches_max": 12}},
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


def _reason(component: str, polarity: str, tier: str, evidence: Sequence[str], severity: str, description: str, missing_inputs: Sequence[str] = ()) -> dict[str, Any]:
    return {"component": component, "polarity": polarity, "computability_tier": tier, "evidence": list(evidence), "severity": severity, "missing_inputs": list(missing_inputs), "description": description}


REASON_CODES: dict[str, dict[str, Any]] = {
    "role_change_recent": _reason("opportunity", "positive", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity increased beyond the position threshold"),
    "role_loss_recent": _reason("opportunity", "negative", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity declined beyond the position threshold"),
    "projection_lagging_role_change": _reason("trend_change_score", "positive", "partial", ("recent opportunity", "projection delta"), "medium", "usage movement is ahead of the projection baseline"),
    "season_average_stale": _reason("season_context_score", "negative", "computed", ("recent points", "season points"), "low", "season average is stale relative to the recent active-week window"),
    "recent_spike_against_stable_usage": _reason("one_off_risk", "negative", "partial", ("recent points", "stable opportunity"), "medium", "points rose without matching opportunity growth"),
    "insufficient_post_change_sample": _reason("small_sample_risk", "negative", "computed", ("post-change active weeks",), "high", "too few active weeks exist after the detected role change"),
    "low_touch_big_points": _reason("one_off_risk", "negative", "computed", ("fantasy points", "rush attempts", "receptions"), "high", "skill-player points spiked on a low-touch week"),
    "low_target_big_points": _reason("one_off_risk", "negative", "computed", ("fantasy points", "targets"), "high", "receiving points spiked on a low-target week"),
    "low_route_big_points": _reason("one_off_risk", "negative", "not_evaluable_missing_source", (), "high", "route-based spike cannot be evaluated without route data", ("routes",)),
    "td_only_low_usage": _reason("td_dependency", "negative", "computed", ("touches or targets", "touchdown stats"), "high", "touchdown points dominate a low-usage performance"),
    "def_td_spike": _reason("one_off_risk", "negative", "computed", ("defensive touchdown stats",), "high", "defensive value was inflated by a touchdown"),
    "turnover_spike_without_pressure": _reason("one_off_risk", "negative", "partial", ("turnovers", "sacks or qb hits"), "medium", "turnovers rose without the available pressure proxy rising"),
    "long_kick_spike": _reason("one_off_risk", "negative", "computed", ("long field goals", "kicking points"), "medium", "kicker value was inflated by long field goals"),
    "matchup_opponent_label_present": _reason("matchup_adjustment", "neutral", "current_metadata_only", ("raw opponent label",), "low", "only the current weekly opponent label is available", ("matchup strength",)),
    "not_evaluable_missing_nfl_schedule": _reason("matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "high", "NFL schedule/opponent join is missing; matchup adjustment is disabled", ("nfl schedule",)),
    "not_evaluable_missing_matchup_strength": _reason("matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "medium", "opponent strength data is missing", ("matchup strength",)),
    "not_evaluable_missing_matchup_weather": _reason("matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "low", "weather and stadium data are missing", ("weather",)),
    "not_evaluable_missing_matchup_implied_total": _reason("matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "low", "betting and implied-total data are missing", ("implied total",)),
    "not_evaluable_missing_matchup_pressure": _reason("matchup_adjustment", "neutral", "not_evaluable_missing_source", (), "medium", "opponent pressure context is missing", ("pressure matchup",)),
    "current_metadata_only_depth_chart": _reason("depth_chart_confidence", "neutral", "current_metadata_only", ("current depth metadata",), "medium", "depth chart metadata is current-only and not historical", ("historical depth chart",)),
    "current_metadata_only_injury": _reason("role_stability", "neutral", "current_metadata_only", ("current injury metadata",), "medium", "injury metadata is current-only and not historical", ("historical availability",)),
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
    return (recent_average - baseline_average) / abs(baseline_average)


def opportunity_signal(position: str, rows: Sequence[Mapping[str, Any]], *, metric: str) -> dict[str, Any]:
    threshold = POSITION_USAGE_THRESHOLDS[position.upper()][metric]
    window = trend_window(rows, threshold["window"])
    all_rows = _eligible_rows(rows)
    baseline = all_rows[:-len(window)] if len(all_rows) > len(window) else []
    minimum = TREND_WINDOWS[threshold["window"]]["minimum_samples"]
    if len(window) < minimum or not baseline:
        return {"status": "insufficient_sample", "delta_pct": None, "recent": window, "baseline": baseline}
    delta_pct = compare_recent_to_baseline(window, baseline, metric)
    delta_per_game = (_average(window, metric) or 0) - (_average(baseline, metric) or 0)
    increased = ((threshold.get("increase_pct") is not None and delta_pct is not None and delta_pct >= threshold["increase_pct"]) or (threshold.get("increase_per_game") is not None and delta_per_game >= threshold["increase_per_game"]))
    improved = threshold.get("improvement_pct") is not None and delta_pct is not None and delta_pct <= -threshold["improvement_pct"]
    return {"status": "increased" if increased or improved else "stable", "delta_pct": delta_pct, "delta_per_game": delta_per_game, "recent": window, "baseline": baseline}


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
    if position == "RB" and points >= 20 and int(row.get("touches", 0)) <= thresholds["low_touch_floor"]["touches_max"]:
        codes.append("low_touch_big_points")
    if position in {"WR", "TE"} and points >= 15 and int(row.get("targets", 0)) <= thresholds["low_target_floor"]["targets_max"]:
        codes.append("low_target_big_points")
    td_count = int(row.get("total_tds", 0) or row.get("rec_tds", 0) or row.get("passing_tds", 0))
    if position in {"RB", "WR", "TE", "QB"} and td_count >= (thresholds.get("total_tds") or thresholds.get("rec_tds") or thresholds.get("passing_tds"))["min"]:
        codes.append("td_only_low_usage")
    if position == "K" and (int(row.get("made_50_plus", 0)) >= thresholds["made_50_plus"]["min"] or float(row.get("long_kick_point_share", 0)) >= thresholds["long_kick_point_share"]["min"]):
        codes.append("long_kick_spike")
    if position == "DEF" and int(row.get("def_or_special_teams_tds", 0)) >= thresholds["def_or_special_teams_tds"]["min"]:
        codes.append("def_td_spike")
    if position == "DEF" and int(row.get("takeaways", 0)) >= thresholds["takeaways"]["min"] and not row.get("pressure_proxy_rise", False):
        codes.append("turnover_spike_without_pressure")
    return codes


def cap_modifier(cap_name: str, value: float, *, position: str | None = None) -> float:
    caps = POSITION_MODIFIER_CAPS.get(position.upper(), MODIFIER_CAPS) if position else MODIFIER_CAPS
    if cap_name not in caps:
        raise KeyError(f"unknown modifier cap: {cap_name}")
    cap = caps[cap_name]
    return round(max(cap["min"], min(cap["max"], float(value))), 2)


PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES: tuple[dict[str, Any], ...] = (
    {"id": "wr_high_points_low_usage", "case": "high_points_with_low_usage", "position": "WR", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 3, "points": 24, "rec_tds": 2}], "season_weeks": [{"week": 1, "active": True, "targets": 7, "points": 12}, {"week": 2, "active": True, "targets": 8, "points": 13}, {"week": 3, "active": True, "targets": 7, "points": 12}]}, "expected": {"reason_codes": ["low_target_big_points", "td_only_low_usage"], "capped_modifiers": {"context": -6.0}}},
    {"id": "rb_rising_usage_lagging_projection", "case": "rising_usage_with_lagging_projections", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "touches": 20, "projection_delta": -2}], "season_weeks": [{"week": 1, "active": True, "touches": 14}, {"week": 2, "active": True, "touches": 14}, {"week": 3, "active": True, "touches": 14}]}, "expected": {"reason_codes": ["role_change_recent", "projection_lagging_role_change"], "capped_modifiers": {"trend": 4.0}}},
    {"id": "qb_high_projection_weak_actuals", "case": "high_projections_with_weak_actuals", "position": "QB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "dropbacks": 25, "points": 11, "projected_points": 23}], "season_weeks": [{"week": 1, "active": True, "dropbacks": 35, "points": 18}, {"week": 2, "active": True, "dropbacks": 36, "points": 19}, {"week": 3, "active": True, "dropbacks": 34, "points": 18}]}, "expected": {"reason_codes": ["season_average_stale"], "capped_modifiers": {"context": -3.0}}},
    {"id": "rb_injury_fill_in", "case": "injury_fill_in", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "touches": 17, "injury_fill_in": True}], "season_weeks": [{"week": 1, "active": True, "touches": 6}, {"week": 2, "active": True, "touches": 7}, {"week": 3, "active": True, "touches": 6}]}, "expected": {"reason_codes": ["role_change_recent"], "capped_modifiers": {"context": 4.0}}},
    {"id": "te_buried_depth_breakout", "case": "buried_depth_chart_breakout", "position": "TE", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 5, "red_zone_targets": 1}], "season_weeks": [{"week": 1, "active": True, "targets": 2}, {"week": 2, "active": True, "targets": 2}, {"week": 3, "active": True, "targets": 2}], "current_metadata": {"depth_chart_role": "third_te"}}, "expected": {"reason_codes": ["role_change_recent", "current_metadata_only_depth_chart"], "capped_modifiers": {"context": 6.0}}},
    {"id": "k_long_kick_spike", "case": "k_long_kick_spike", "position": "K", "inputs": {"recent_weeks": [{"week": 4, "active": True, "made_50_plus": 2, "long_kick_point_share": 0.56}], "season_weeks": [{"week": 1, "active": True, "made_50_plus": 0}, {"week": 2, "active": True, "made_50_plus": 0}, {"week": 3, "active": True, "made_50_plus": 0}]}, "expected": {"reason_codes": ["long_kick_spike"], "capped_modifiers": {"context": -4.5}}},
    {"id": "def_td_turnover_spike", "case": "def_touchdown_or_turnover_spike", "position": "DEF", "inputs": {"recent_weeks": [{"week": 4, "active": True, "def_or_special_teams_tds": 1, "takeaways": 4, "sacks": 1}], "season_weeks": [{"week": 1, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}, {"week": 2, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}, {"week": 3, "active": True, "def_or_special_teams_tds": 0, "takeaways": 1, "sacks": 4}]}, "expected": {"reason_codes": ["def_td_spike", "turnover_spike_without_pressure"], "capped_modifiers": {"context": -6.0, "def_streaming_matchup": 3.0}}},
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
        "td_dependency_formulas": deepcopy(TD_DEPENDENCY_FORMULAS),
        "spike_thresholds": deepcopy(SPIKE_THRESHOLDS),
        "golden_fixtures": deepcopy(list(PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES)),
    }
