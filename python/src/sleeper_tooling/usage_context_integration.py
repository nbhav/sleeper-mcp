"""Integrate player_usage_context (v3) into decision outputs (issue #32).

Three layers, kept separate so each stays auditable:

1. ``summarize_usage_context`` turns a full v3 profile into a compact,
   JSON-safe summary block for player cards, lineup rows, and waiver rows.
2. ``player_usage_adjustment`` decomposes the profile's reason codes into
   bounded scoring components (opportunity/role, trend change, one-off risk,
   role uncertainty, TD dependency, matchup) in move-score points.
3. ``usage_context_move_adjustment`` turns the add and drop sides into one
   capped move-level delta for ``score_waiver_move``.

Missing data is never zero: every unavailable input is reported with an
explicit ``not_evaluable_*`` status, and a not-evaluable side means the move
adjustment is not applied.  Points-based signals are excluded from scoring so
context does not double count ``player_values`` projections or recent scoring.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from sleeper_tooling.context_providers import ContextInput
from sleeper_tooling.player_usage_context import POSITION_OPPORTUNITY, PlayerUsageContextService
from sleeper_tooling.player_usage_context_rules import (
    MODIFIER_CAPS,
    PRODUCTION_QUALITY_POINTS_PER_PPG,
    REASON_MODIFIERS,
    TD_DEPENDENCY_FORMULAS,
    TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT,
    TD_DEPENDENCY_WEIGHT_PROVENANCE,
    td_dependency_excess_pct,
)

USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION = "usage_context_summary.v1"
USAGE_CONTEXT_ADJUSTMENT_VERSION = "usage_context_adjustment.v1"

# score_waiver_move weights actual-based three-week and season values at these
# multipliers; TD-inflated actual points reach move_score through both.
MOVE_SCORE_THREE_WEEK_WEIGHT = 1.2
MOVE_SCORE_SEASON_WEIGHT = 1.4
MOVE_SCORE_ACTUAL_PPG_WEIGHT = MOVE_SCORE_THREE_WEEK_WEIGHT + MOVE_SCORE_SEASON_WEIGHT

# Per-player component caps (move-score points).  The per-player total and the
# move-level add-minus-drop delta both use the epic's context cap (-6..+6).
COMPONENT_CAPS: dict[str, tuple[float, float]] = {
    "opportunity_role": (-4.0, 4.0),
    "trend_change": (MODIFIER_CAPS["trend"]["min"], MODIFIER_CAPS["trend"]["max"]),
    "one_off_risk": (-6.0, 0.0),
    "role_uncertainty": (-2.0, 0.0),
    "td_dependency": (-3.0, 0.0),
    "matchup": (MODIFIER_CAPS["matchup"]["min"], MODIFIER_CAPS["matchup"]["max"]),
}
PLAYER_TOTAL_CAP = (MODIFIER_CAPS["context"]["min"], MODIFIER_CAPS["context"]["max"])
MOVE_DELTA_CAP = (MODIFIER_CAPS["context"]["min"], MODIFIER_CAPS["context"]["max"])
ROLE_UNCERTAINTY_PENALTY = -1.0
SMALL_TREND_SAMPLE_PENALTY = -1.0
MINIMUM_TREND_WEEKS = 2

OPPORTUNITY_ROLE_CODES = ("role_change_recent", "role_loss_recent", "injury_replacement")
TREND_CHANGE_CODES = ("projection_lagging_role_change",)
ONE_OFF_CODES = (
    "low_touch_big_points",
    "low_target_big_points",
    "td_only_low_usage",
    "def_td_spike",
    "turnover_spike_without_pressure",
    "long_kick_spike",
)
# Usage-based spikes (big points on low measured opportunity) that downgrade a
# waiver recommend to watch.  td_only_low_usage is excluded: the v3 rule fires on
# multi-TD weeks whenever the opportunity field is present, regardless of volume.
SEVERE_ONE_OFF_CODES = ("low_touch_big_points", "low_target_big_points")
POSITIVE_SIGNAL_CODES = ("role_change_recent", "projection_lagging_role_change")
RISK_SIGNAL_CODES = ("role_loss_recent", "injury_replacement", "season_average_stale")
# Reason codes deliberately excluded from scoring, with the reason.
EXCLUDED_SCORING_CODES: dict[str, str] = {
    "season_average_stale": "points-based (recent vs season fantasy points); recent scoring is already priced by player_values",
    "current_metadata_only_depth_chart": "current-only metadata present for nearly every player; not week-specific evidence",
}

# Join between usage-context missing inputs and context_providers.ContextInput.
# The two vocabularies differ: usage context says red_zone_share/implied_total,
# providers say red_zone_use/implied_totals.  Inputs mapped to None are
# intentionally unmapped because the provider metric is a different quantity.
# The mapping is evidence only: provider metrics do not feed scoring.
USAGE_INPUT_PROVIDER_INPUTS: dict[str, str | None] = {
    "red_zone_share": ContextInput.RED_ZONE_USE.value,
    "routes": ContextInput.ROUTES.value,
    "target_share": ContextInput.TARGET_SHARE.value,
    "pressure": ContextInput.PRESSURE.value,
    "implied_total": ContextInput.IMPLIED_TOTALS.value,
    "weather": ContextInput.WEATHER.value,
    # carry_share counts carries only; touch_share also includes targets.
    "touch_share": None,
    # provider air_yards is an absolute count, not a share of team air yards.
    "air_yard_share": None,
    # NFL schedule comes from the team_week_schedule store, not a provider.
    "nfl_schedule": None,
    "actual_stats": None,
}

SUMMARY_SCORE_KEYS = (
    "opportunity_score",
    "role_stability_score",
    "trend_change_score",
    "small_sample_risk",
    "one_off_risk",
    "td_dependency_score",
    "context_confidence",
)

WINDOW_RULE = "completed weeks before the decision week (evaluated_through_week = decision_week - 1)"


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return round(max(bounds[0], min(bounds[1], value)), 2)


def summarize_usage_context(profile: Mapping[str, Any], *, decision_week: int | None = None) -> dict[str, Any]:
    """Compact usage-context block from a full player_usage_context profile."""

    scores = profile.get("scores") or {}
    codes = list(profile.get("reason_codes") or [])
    position = str(profile.get("position") or "").upper()
    trend = profile.get("trend_window") or {}
    change_point = trend.get("change_point") or {}
    td_record = profile.get("td_dependency") or {}
    td_score = scores.get("td_dependency_score")
    method = profile.get("td_dependency_method") or td_record.get("method")
    if method != "league_scoring_contributions":
        # The Worker heuristic is not a league share; thresholds do not apply.
        td_score_for_excess = None
        td_status = "not_evaluable_missing_league_scoring_contributions"
    elif td_score is None:
        td_score_for_excess = None
        td_status = str(td_record.get("reason") or "not_evaluable_missing_scoring_contributions")
    else:
        td_score_for_excess = td_score
        td_status = "evaluated"
    formula = TD_DEPENDENCY_FORMULAS.get(position)
    missing = list(profile.get("missing_inputs") or [])
    injury = profile.get("injury_replacement") or {}
    status = "evaluated" if profile.get("actual_first") else "not_evaluable_missing_actual_stats"
    return {
        "schema_version": USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION,
        "source_schema_version": profile.get("schema_version"),
        "status": status,
        "player_id": str(profile.get("player_id") or ""),
        "position": position,
        "decision_week": decision_week,
        "evaluated_through_week": profile.get("week"),
        "window_rule": WINDOW_RULE,
        "data_source": profile.get("data_source"),
        "role_label": profile.get("role_label"),
        "context_confidence": scores.get("context_confidence"),
        "trend_window": {
            "name": trend.get("name"),
            "weeks": list(trend.get("weeks") or []),
            "baseline_weeks": list(trend.get("baseline_weeks") or []),
            "change_status": change_point.get("status"),
            "change_week": change_point.get("change_week"),
            "change_direction": change_point.get("direction"),
        },
        "positive_signals": [code for code in codes if code in POSITIVE_SIGNAL_CODES],
        "risk_signals": [code for code in codes if code in RISK_SIGNAL_CODES],
        "one_off_flags": [code for code in codes if code in ONE_OFF_CODES],
        "reason_codes": codes,
        "not_evaluable": [code for code in codes if code.startswith("not_evaluable_")],
        "missing_inputs": missing,
        "provider_inputs_for_missing": {name: USAGE_INPUT_PROVIDER_INPUTS.get(name) for name in missing},
        "injury_replacement": injury.get("status") if isinstance(injury, Mapping) else None,
        "td_dependency": {
            "status": td_status,
            "method": method,
            "window": td_record.get("window"),
            "score": td_score,
            "threshold_pct": round(float(formula["dependency_threshold"]) * 100, 2) if formula else None,
            "excess_pct": td_dependency_excess_pct(position, td_score_for_excess),
        },
        "score_components": {key: scores.get(key) for key in SUMMARY_SCORE_KEYS},
    }


def unavailable_usage_context(status: str, *, player_id: str = "", position: str | None = None, decision_week: int | None = None, detail: str | None = None) -> dict[str, Any]:
    """A summary with the full shape whose status says why it was not evaluated."""

    return {
        "schema_version": USAGE_CONTEXT_SUMMARY_SCHEMA_VERSION,
        "source_schema_version": None,
        "status": status,
        "player_id": str(player_id or ""),
        "position": str(position or "").upper() or None,
        "decision_week": decision_week,
        "evaluated_through_week": (decision_week - 1) if decision_week else None,
        "window_rule": WINDOW_RULE,
        "data_source": None,
        "role_label": None,
        "context_confidence": None,
        "trend_window": None,
        "positive_signals": [],
        "risk_signals": [],
        "one_off_flags": [],
        "reason_codes": [],
        "not_evaluable": [status] if status.startswith("not_evaluable_") else [],
        "missing_inputs": [],
        "provider_inputs_for_missing": {},
        "injury_replacement": None,
        "td_dependency": None,
        "score_components": None,
        **({"detail": detail} if detail else {}),
    }


def is_evaluated(summary: Mapping[str, Any] | None) -> bool:
    return bool(summary) and summary.get("status") == "evaluated"


def player_usage_adjustment(summary: Mapping[str, Any] | None, *, actuals_in_value: bool) -> dict[str, Any]:
    """Bounded per-player scoring components in move-score points.

    ``actuals_in_value`` says whether this row's move value uses recent actual
    points; the TD-dependency correction only applies then, because projections
    do not carry the TD-inflated actual scoring it corrects.
    """

    if not is_evaluated(summary):
        status = (summary or {}).get("status") or "not_evaluable_missing_usage_context"
        return {"status": status, "total": None, "components": {}}
    assert summary is not None
    codes = list(summary.get("reason_codes") or [])
    position = str(summary.get("position") or "").upper()
    components: dict[str, dict[str, Any]] = {}

    def from_codes(name: str, allowed: Iterable[str], *, skip: Iterable[str] = ()) -> None:
        used = [code for code in codes if code in allowed and code not in set(skip)]
        raw = sum(REASON_MODIFIERS[code][1] for code in used if code in REASON_MODIFIERS)
        components[name] = {"status": "evaluated", "value": _clamp(raw, COMPONENT_CAPS[name]), "raw": round(raw, 2), "codes": used, "cap": list(COMPONENT_CAPS[name])}

    td = summary.get("td_dependency") or {}
    td_evaluable = td.get("status") == "evaluated" and td.get("excess_pct") is not None
    from_codes("opportunity_role", OPPORTUNITY_ROLE_CODES)
    from_codes("trend_change", TREND_CHANGE_CODES)
    # When league TD share is evaluable it prices TD-heavy scoring; skip the
    # td_only_low_usage spike code so the same touchdowns are not counted twice.
    from_codes("one_off_risk", ONE_OFF_CODES, skip=("td_only_low_usage",) if td_evaluable else ())

    uncertainty_codes = []
    uncertainty = 0.0
    if summary.get("role_label") == "uncertain_role":
        uncertainty += ROLE_UNCERTAINTY_PENALTY
        uncertainty_codes.append("uncertain_role")
    trend_weeks = (summary.get("trend_window") or {}).get("weeks") or []
    if len(trend_weeks) < MINIMUM_TREND_WEEKS:
        uncertainty += SMALL_TREND_SAMPLE_PENALTY
        uncertainty_codes.append("small_trend_sample")
    components["role_uncertainty"] = {"status": "evaluated", "value": _clamp(uncertainty, COMPONENT_CAPS["role_uncertainty"]), "raw": uncertainty, "codes": uncertainty_codes, "cap": list(COMPONENT_CAPS["role_uncertainty"])}

    weight = TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT.get(position)
    if not td_evaluable:
        components["td_dependency"] = {"status": td.get("status") or "not_evaluable_missing_scoring_contributions", "value": None}
    elif not actuals_in_value:
        components["td_dependency"] = {"status": "not_applicable_actual_points_not_in_move_value", "value": None, "excess_pct": td.get("excess_pct")}
    else:
        excess = float(td["excess_pct"])
        raw = -(MOVE_SCORE_ACTUAL_PPG_WEIGHT * float(weight or 0.0) * excess)
        components["td_dependency"] = {
            "status": "evaluated",
            "value": _clamp(raw, COMPONENT_CAPS["td_dependency"]),
            "raw": round(raw, 2),
            "excess_pct": excess,
            "ppg_regression_per_excess_pct": weight,
            "move_score_per_ppg": MOVE_SCORE_ACTUAL_PPG_WEIGHT,
            "weight_provenance": TD_DEPENDENCY_WEIGHT_PROVENANCE.get(position),
            "cap": list(COMPONENT_CAPS["td_dependency"]),
        }

    matchup_code = "not_evaluable_missing_nfl_schedule" if "not_evaluable_missing_nfl_schedule" in codes else "not_evaluable_missing_matchup_strength"
    components["matchup"] = {"status": matchup_code, "value": None}

    evaluated_values = [float(row["value"]) for row in components.values() if row.get("value") is not None]
    raw_total = sum(evaluated_values)
    return {
        "status": "evaluated",
        "total": _clamp(raw_total, PLAYER_TOTAL_CAP),
        "raw_total": round(raw_total, 2),
        "cap": list(PLAYER_TOTAL_CAP),
        "components": components,
        "excluded_codes": {code: EXCLUDED_SCORING_CODES[code] for code in codes if code in EXCLUDED_SCORING_CODES},
        "severe_one_off_flags": [code for code in codes if code in SEVERE_ONE_OFF_CODES],
    }


def usage_context_move_adjustment(
    add_summary: Mapping[str, Any] | None,
    drop_summary: Mapping[str, Any] | None,
    *,
    add_actuals_in_value: bool,
    drop_actuals_in_value: bool,
    has_drop: bool,
) -> dict[str, Any]:
    """Move-level add-minus-drop context delta, applied only when both sides are evaluated."""

    if add_summary is None and drop_summary is None:
        return {"version": USAGE_CONTEXT_ADJUSTMENT_VERSION, "status": "not_requested", "applied_delta": 0.0, "delta": None}
    add = player_usage_adjustment(add_summary, actuals_in_value=add_actuals_in_value)
    drop = player_usage_adjustment(drop_summary, actuals_in_value=drop_actuals_in_value) if has_drop else {"status": "no_drop_candidate", "total": None, "components": {}}
    base = {"version": USAGE_CONTEXT_ADJUSTMENT_VERSION, "add": add, "drop": drop, "cap": list(MOVE_DELTA_CAP)}
    if add["total"] is None or drop["total"] is None:
        missing_side = "add" if add["total"] is None else "drop"
        reason = add["status"] if missing_side == "add" else drop["status"]
        return {**base, "status": f"not_applied_{missing_side}_{reason}", "applied_delta": 0.0, "delta": None}
    raw_delta = float(add["total"]) - float(drop["total"])
    delta = _clamp(raw_delta, MOVE_DELTA_CAP)
    return {**base, "status": "applied", "raw_delta": round(raw_delta, 2), "delta": delta, "applied_delta": delta}


def usage_context_reasoning(adjustment: Mapping[str, Any] | None) -> str | None:
    """One-line explanation of the context adjustment for reasoning summaries."""

    if not adjustment or adjustment.get("status") == "not_requested":
        return None
    if adjustment.get("status") != "applied":
        return f"usage context not applied ({adjustment.get('status')})"
    parts = []
    for side in ("add", "drop"):
        components = (adjustment.get(side) or {}).get("components") or {}
        codes = [code for row in components.values() for code in row.get("codes", [])]
        if codes:
            parts.append(f"{side}: {', '.join(codes)}")
    detail = f" [{'; '.join(parts)}]" if parts else ""
    return f"usage context {float(adjustment['delta']):+.2f}{detail}"


def compare_td_dependency_penalty(profile: Mapping[str, Any]) -> float:
    """TD penalty for compare_player_usage_context on the production_quality scale.

    Only the TD share above the position threshold counts; the calibrated
    ppg regression is converted with PRODUCTION_QUALITY_POINTS_PER_PPG.
    """

    position = str(profile.get("position") or "").upper()
    if (profile.get("td_dependency_method") or "league_scoring_contributions") != "league_scoring_contributions":
        return 0.0
    excess = td_dependency_excess_pct(position, (profile.get("scores") or {}).get("td_dependency_score"))
    if excess is None:
        return 0.0
    weight = TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT.get(position, 0.0)
    return round(PRODUCTION_QUALITY_POINTS_PER_PPG * weight * excess, 2)


class UsageContextProvider:
    """Memoized compact usage-context summaries for one tool call.

    Contexts for a decision in week W are evaluated through week W - 1 so a
    partially synced in-progress week never counts as completed usage.
    """

    def __init__(
        self,
        repository_factory: Callable[[], Any],
        *,
        season: int,
        decision_week: int,
        service_factory: Callable[[Any], Any] = PlayerUsageContextService,
    ) -> None:
        self._repository_factory = repository_factory
        self._service_factory = service_factory
        self._service: Any | None = None
        self.season = int(season)
        self.decision_week = int(decision_week)
        self.context_week = self.decision_week - 1
        self._memo: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, player_id: Any, position: Any = None) -> dict[str, Any]:
        pid = str(player_id or "")
        pos = str(position or "").upper()
        key = (pid, pos)
        if key not in self._memo:
            self._memo[key] = self._build(pid, pos)
        return self._memo[key]

    def get_many(self, rows: Iterable[Mapping[str, Any]], *, id_key: str = "player_id", position_key: str = "position") -> dict[str, dict[str, Any]]:
        return {str(row.get(id_key) or ""): self.get(row.get(id_key), row.get(position_key)) for row in rows if str(row.get(id_key) or "") not in ("", "0")}

    def _build(self, player_id: str, position: str) -> dict[str, Any]:
        kwargs = {"player_id": player_id, "position": position or None, "decision_week": self.decision_week}
        if player_id in ("", "0"):
            return unavailable_usage_context("not_evaluable_missing_player_id", **kwargs)
        if position and position not in POSITION_OPPORTUNITY:
            return unavailable_usage_context("not_evaluable_unsupported_position", **kwargs)
        if self.context_week < 1:
            return unavailable_usage_context("not_evaluable_no_completed_weeks", **kwargs)
        try:
            if self._service is None:
                self._service = self._service_factory(self._repository_factory())
            profile = self._service.build(player_id=player_id, season=self.season, week=self.context_week, position=position or None)
        except ValueError as exc:
            return unavailable_usage_context("not_evaluable_unsupported_position", detail=str(exc), **kwargs)
        except sqlite3.Error as exc:
            return unavailable_usage_context("not_evaluable_missing_normalized_repository", detail=str(exc), **kwargs)
        return summarize_usage_context(profile, decision_week=self.decision_week)


def not_evaluated_outside_budget(player_id: Any, position: Any, decision_week: int | None) -> dict[str, Any]:
    return unavailable_usage_context("not_evaluated_outside_context_budget", player_id=str(player_id or ""), position=position, decision_week=decision_week)


def attach_usage_contexts(rows: Iterable[dict[str, Any]], usage_contexts: Mapping[str, Mapping[str, Any]] | None, *, decision_week: int | None = None, id_key: str = "player_id") -> list[dict[str, Any]]:
    """Copies of rows with ``usage_context`` set; rows outside the mapping get an explicit status."""

    rows = list(rows)
    if usage_contexts is None:
        return rows
    output = []
    for row in rows:
        pid = str(row.get(id_key) or "")
        context = usage_contexts.get(pid) or row.get("usage_context") or not_evaluated_outside_budget(pid, row.get("position"), decision_week)
        output.append({**row, "usage_context": dict(context)})
    return output
