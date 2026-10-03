from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
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


def _reason(code: str, component: str, polarity: str, tier: str, evidence: Sequence[str], severity: str, description: str, missing_inputs: Sequence[str] = ()) -> dict[str, Any]:
    return {"code": code, "component": component, "polarity": polarity, "computability_tier": tier, "evidence": list(evidence), "severity": severity, "missing_inputs": list(missing_inputs), "description": description}


REASON_CODES: dict[str, dict[str, Any]] = {
    "role_change_recent": _reason("role_change_recent", "opportunity", "positive", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity increased beyond the position threshold"),
    "role_loss_recent": _reason("role_loss_recent", "opportunity", "negative", "partial", ("recent opportunity", "season baseline"), "medium", "recent opportunity declined beyond the position threshold"),
    "projection_lagging_role_change": _reason("projection_lagging_role_change", "trend_change_score", "positive", "partial", ("recent opportunity", "projection delta"), "medium", "usage movement is ahead of the projection baseline"),
    "season_average_stale": _reason("season_average_stale", "season_context_score", "negative", "computed", ("recent points", "season points"), "low", "season average is stale relative to the recent active-week window"),
    "recent_spike_against_stable_usage": _reason("recent_spike_against_stable_usage", "one_off_risk", "negative", "partial", ("recent points", "stable opportunity"), "medium", "points rose without matching opportunity growth"),
    "insufficient_post_change_sample": _reason("insufficient_post_change_sample", "small_sample_risk", "negative", "computed", ("post-change active weeks",), "high", "too few active weeks exist after the detected role change"),
    "low_touch_big_points": _reason("low_touch_big_points", "one_off_risk", "negative", "computed", ("fantasy points", "rush attempts", "receptions"), "high", "skill-player points spiked on a low-touch week"),
    "low_target_big_points": _reason("low_target_big_points", "one_off_risk", "negative", "computed", ("fantasy points", "targets"), "high", "receiving points spiked on a low-target week"),
    "low_route_big_points": _reason("low_route_big_points", "one_off_risk", "negative", "not_evaluable_missing_source", (), "high", "route-based spike cannot be evaluated without route data", ("routes",)),
    "td_only_low_usage": _reason("td_only_low_usage", "td_dependency", "negative", "computed", ("touches or targets", "touchdown stats"), "high", "touchdown points dominate a low-usage performance"),
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
    "not_evaluable_missing_touches": _reason("not_evaluable_missing_touches", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "touches are missing; this signal is not evaluable", ("touches",)),
    "not_evaluable_missing_targets": _reason("not_evaluable_missing_targets", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "targets are missing; this signal is not evaluable", ("targets",)),
    "not_evaluable_missing_rush_attempts": _reason("not_evaluable_missing_rush_attempts", "opportunity", "neutral", "not_evaluable_missing_source", (), "medium", "rush attempts are missing; this signal is not evaluable", ("rush_attempts",)),
    "not_evaluable_missing_pressure_proxy_rise": _reason("not_evaluable_missing_pressure_proxy_rise", "one_off_risk", "neutral", "not_evaluable_missing_source", (), "medium", "turnover spike risk is not evaluable without a pressure proxy", ("pressure_proxy_rise",)),
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


def opportunity_signal(position: str, rows: Sequence[Mapping[str, Any]], *, metric: str, signal_code: str = "role_change_recent") -> dict[str, Any]:
    position = position.upper()
    threshold = POSITION_USAGE_THRESHOLDS[position][metric]
    window = trend_window(rows, threshold["window"])
    all_rows = _eligible_rows(rows)
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
        if row.get("touches") is None:
            codes.append("not_evaluable_missing_touches")
        elif int(row["touches"]) <= thresholds["low_touch_floor"]["touches_max"]:
            codes.append("low_touch_big_points")
    if position in {"WR", "TE"} and points >= 15:
        if row.get("targets") is None:
            codes.append("not_evaluable_missing_targets")
        elif int(row["targets"]) <= thresholds["low_target_floor"]["targets_max"]:
            codes.append("low_target_big_points")
    td_count = int(row.get("total_tds", 0) or row.get("rec_tds", 0) or row.get("passing_tds", 0))
    if position in {"RB", "WR", "TE", "QB"} and td_count >= (thresholds.get("total_tds") or thresholds.get("rec_tds") or thresholds.get("passing_tds"))["min"]:
        opportunity_field = {"RB": "touches", "WR": "targets", "TE": "targets", "QB": "rush_attempts"}[position]
        if row.get(opportunity_field) is None:
            codes.append(f"not_evaluable_missing_{opportunity_field}")
        else:
            codes.append("td_only_low_usage")
    if position == "K" and (int(row.get("made_50_plus", 0)) >= thresholds["made_50_plus"]["min"] or float(row.get("long_kick_point_share", 0)) >= thresholds["long_kick_point_share"]["min"]):
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
    "RB": "touches",
    "WR": "targets",
    "TE": "targets",
}
_REASON_MODIFIERS = {
    "role_change_recent": ("context", 4.0),
    "role_loss_recent": ("context", -4.0),
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


def evaluate_player_usage_context(position: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the deterministic V1 rule subset used by the golden fixtures."""

    position = position.upper()
    recent = list(inputs.get("recent_weeks", ()))
    season = list(inputs.get("season_weeks", ()))
    rows = season + recent
    reason_codes: list[str] = []
    modifiers: dict[str, float] = {}
    opportunity_metric = _OPPORTUNITY_METRIC_BY_POSITION.get(position)
    if rows and opportunity_metric in POSITION_USAGE_THRESHOLDS.get(position, {}):
        signal = opportunity_signal(position, rows, metric=opportunity_metric)
        if signal["status"] == "increased":
            reason_codes.append("role_change_recent")
        elif signal["status"] == "decreased":
            reason_codes.append("role_loss_recent")
        elif signal["status"].startswith("not_evaluable_missing_"):
            reason_codes.extend(signal["reason_codes"])

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
    return {"reason_codes": reason_codes, "reasons": reasons, "modifiers": capped_modifiers, "capped_modifiers": capped_modifiers}


PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES: tuple[dict[str, Any], ...] = (
    {"id": "wr_high_points_low_usage", "case": "high_points_with_low_usage", "position": "WR", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 3, "points": 24, "rec_tds": 2}, {"week": 5, "active": True, "targets": 3, "points": 24, "rec_tds": 2}], "season_weeks": [{"week": 1, "active": True, "targets": 7, "points": 12}, {"week": 2, "active": True, "targets": 8, "points": 13}, {"week": 3, "active": True, "targets": 7, "points": 12}]}, "expected": {"reason_codes": ["role_loss_recent", "low_target_big_points", "td_only_low_usage"], "capped_modifiers": {"context": -6.0}}},
    {"id": "rb_rising_usage_lagging_projection", "case": "rising_usage_with_lagging_projections", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "touches": 19, "projection_delta": -2}, {"week": 5, "active": True, "touches": 20, "projection_delta": -2}], "season_weeks": [{"week": 1, "active": True, "touches": 14}, {"week": 2, "active": True, "touches": 14}, {"week": 3, "active": True, "touches": 14}]}, "expected": {"reason_codes": ["role_change_recent", "projection_lagging_role_change"], "capped_modifiers": {"trend": 4.0, "context": 4.0}}},
    {"id": "qb_high_projection_weak_actuals", "case": "high_projections_with_weak_actuals", "position": "QB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "dropbacks": 25, "points": 11, "projected_points": 23}, {"week": 5, "active": True, "dropbacks": 25, "points": 11, "projected_points": 23}], "season_weeks": [{"week": 1, "active": True, "dropbacks": 35, "points": 18}, {"week": 2, "active": True, "dropbacks": 36, "points": 19}, {"week": 3, "active": True, "dropbacks": 34, "points": 18}]}, "expected": {"reason_codes": ["role_loss_recent", "season_average_stale"], "capped_modifiers": {"context": -6.0}}},
    {"id": "rb_injury_fill_in", "case": "injury_fill_in", "position": "RB", "inputs": {"recent_weeks": [{"week": 4, "active": True, "touches": 17, "injury_fill_in": True}, {"week": 5, "active": True, "touches": 17, "injury_fill_in": True}], "season_weeks": [{"week": 1, "active": True, "touches": 6}, {"week": 2, "active": True, "touches": 7}, {"week": 3, "active": True, "touches": 6}]}, "expected": {"reason_codes": ["role_change_recent"], "capped_modifiers": {"context": 4.0}}},
    {"id": "te_buried_depth_breakout", "case": "buried_depth_chart_breakout", "position": "TE", "inputs": {"recent_weeks": [{"week": 4, "active": True, "targets": 5, "red_zone_targets": 1}, {"week": 5, "active": True, "targets": 5, "red_zone_targets": 1}], "season_weeks": [{"week": 1, "active": True, "targets": 2}, {"week": 2, "active": True, "targets": 2}, {"week": 3, "active": True, "targets": 2}], "current_metadata": {"depth_chart_role": "third_te"}}, "expected": {"reason_codes": ["role_change_recent", "current_metadata_only_depth_chart"], "capped_modifiers": {"context": 6.0}}},
    {"id": "k_long_kick_spike", "case": "k_long_kick_spike", "position": "K", "inputs": {"recent_weeks": [{"week": 4, "active": True, "made_50_plus": 2, "long_kick_point_share": 0.56}, {"week": 5, "active": True, "made_50_plus": 2, "long_kick_point_share": 0.56}], "season_weeks": [{"week": 1, "active": True, "made_50_plus": 0}, {"week": 2, "active": True, "made_50_plus": 0}, {"week": 3, "active": True, "made_50_plus": 0}]}, "expected": {"reason_codes": ["long_kick_spike"], "capped_modifiers": {"context": -4.5}}},
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
        "td_dependency_formulas": deepcopy(TD_DEPENDENCY_FORMULAS),
        "spike_thresholds": deepcopy(SPIKE_THRESHOLDS),
        "golden_fixtures": deepcopy(list(PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES)),
    }
