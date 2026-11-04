"""Usage context in player cards, lineup/waiver output, and score_waiver_move (issue #32)."""

from __future__ import annotations

from typing import Any

import pytest

from sleeper_tooling.context_providers import ContextInput
from sleeper_tooling.db import SleeperNormalizedRepository
from sleeper_tooling.decision_reports import score_waiver_move, waiver_recommendation
from sleeper_tooling.mcp_tools import FantasyToolRunner
from sleeper_tooling.player_usage_context_rules import TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT
from sleeper_tooling.sync import SQLiteNormalizedRepositoryAdapter
from sleeper_tooling.usage_context_integration import (
    MOVE_SCORE_ACTUAL_PPG_WEIGHT,
    USAGE_INPUT_PROVIDER_INPUTS,
    UsageContextProvider,
    attach_usage_contexts,
    compare_td_dependency_penalty,
    player_usage_adjustment,
    usage_context_move_adjustment,
)
from test_mcp_tools import FakeLineupClient, FakeMcpClient

SCORING = {"rush_yd": 0.1, "rush_td": 6, "rec": 1, "rec_yd": 0.1, "rec_td": 6}


def summary(position: str = "RB", *, codes: tuple[str, ...] = (), role: str = "established_role", td_score: float | None = 0.0, td_status: str = "evaluated", trend_weeks: tuple[int, ...] = (3, 4), status: str = "evaluated") -> dict[str, Any]:
    threshold = {"QB": 45.0, "RB": 35.0, "WR": 40.0, "TE": 45.0}.get(position, 50.0)
    return {
        "status": status,
        "position": position,
        "role_label": role,
        "reason_codes": list(codes),
        "trend_window": {"weeks": list(trend_weeks)},
        "td_dependency": {
            "status": td_status,
            "score": td_score,
            "threshold_pct": threshold,
            "excess_pct": None if td_score is None or td_status != "evaluated" else max(0.0, td_score - threshold),
        },
    }


# --- per-player components -------------------------------------------------------


def test_role_change_is_positive_and_points_or_metadata_codes_are_excluded() -> None:
    result = player_usage_adjustment(summary(codes=("role_change_recent", "season_average_stale", "current_metadata_only_depth_chart")), actuals_in_value=False)

    assert result["components"]["opportunity_role"]["value"] == 4.0
    assert result["total"] == 4.0
    assert set(result["excluded_codes"]) == {"season_average_stale", "current_metadata_only_depth_chart"}


def test_injury_replacement_nets_a_smaller_role_bonus() -> None:
    result = player_usage_adjustment(summary(codes=("role_change_recent", "injury_replacement")), actuals_in_value=False)

    assert result["components"]["opportunity_role"]["value"] == 2.0


def test_normal_td_scorer_is_not_penalized_and_excess_uses_calibrated_weight() -> None:
    normal = player_usage_adjustment(summary(td_score=30.0), actuals_in_value=True)
    heavy = player_usage_adjustment(summary(td_score=37.0), actuals_in_value=True)

    assert normal["components"]["td_dependency"]["value"] == 0.0
    expected = -round(MOVE_SCORE_ACTUAL_PPG_WEIGHT * TD_DEPENDENCY_REGRESSION_PPG_PER_EXCESS_PCT["RB"] * 2.0, 2)
    assert heavy["components"]["td_dependency"]["value"] == expected
    assert heavy["components"]["td_dependency"]["weight_provenance"] == "calibrated_preliminary_prior"


def test_td_penalty_is_capped() -> None:
    result = player_usage_adjustment(summary(td_score=90.0), actuals_in_value=True)

    assert result["components"]["td_dependency"]["value"] == -3.0


def test_td_penalty_needs_actual_points_in_move_value() -> None:
    result = player_usage_adjustment(summary(td_score=90.0), actuals_in_value=False)

    assert result["components"]["td_dependency"]["value"] is None
    assert result["components"]["td_dependency"]["status"] == "not_applicable_actual_points_not_in_move_value"


def test_missing_scoring_contributions_are_not_evaluable_not_zero() -> None:
    result = player_usage_adjustment(summary(td_score=None, td_status="not_evaluable_missing_scoring_contributions"), actuals_in_value=True)

    assert result["components"]["td_dependency"] == {"status": "not_evaluable_missing_scoring_contributions", "value": None}


def test_td_only_low_usage_is_not_double_counted_when_td_share_is_evaluable() -> None:
    with_share = player_usage_adjustment(summary(codes=("td_only_low_usage",), td_score=20.0), actuals_in_value=True)
    without_share = player_usage_adjustment(summary(codes=("td_only_low_usage",), td_score=None, td_status="not_evaluable_missing_scoring_contributions"), actuals_in_value=True)

    assert with_share["components"]["one_off_risk"]["value"] == 0.0
    assert without_share["components"]["one_off_risk"]["value"] == -3.0


def test_role_uncertainty_and_matchup_status() -> None:
    result = player_usage_adjustment(summary(role="uncertain_role", trend_weeks=(4,)), actuals_in_value=False)

    assert result["components"]["role_uncertainty"]["value"] == -2.0
    assert result["components"]["matchup"]["value"] is None
    assert result["components"]["matchup"]["status"].startswith("not_evaluable_missing_")


def test_not_evaluated_summary_has_no_total() -> None:
    result = player_usage_adjustment(summary(status="not_evaluable_missing_actual_stats"), actuals_in_value=True)

    assert result == {"status": "not_evaluable_missing_actual_stats", "total": None, "components": {}}


# --- move-level adjustment ------------------------------------------------------


def test_move_adjustment_is_capped_add_minus_drop() -> None:
    adjustment = usage_context_move_adjustment(
        summary(codes=("role_change_recent", "projection_lagging_role_change")),
        summary(codes=("role_loss_recent",), role="role_declining"),
        add_actuals_in_value=False,
        drop_actuals_in_value=False,
        has_drop=True,
    )

    assert adjustment["status"] == "applied"
    assert adjustment["raw_delta"] == 10.0
    assert adjustment["delta"] == 6.0
    assert adjustment["applied_delta"] == 6.0


def test_move_adjustment_is_not_applied_when_one_side_is_not_evaluated() -> None:
    adjustment = usage_context_move_adjustment(
        summary(codes=("role_change_recent",)),
        summary(status="not_evaluable_missing_actual_stats"),
        add_actuals_in_value=False,
        drop_actuals_in_value=False,
        has_drop=True,
    )

    assert adjustment["status"] == "not_applied_drop_not_evaluable_missing_actual_stats"
    assert adjustment["delta"] is None
    assert adjustment["applied_delta"] == 0.0


def test_move_adjustment_not_requested_without_context() -> None:
    adjustment = usage_context_move_adjustment(None, None, add_actuals_in_value=False, drop_actuals_in_value=False, has_drop=True)

    assert adjustment["status"] == "not_requested"
    assert adjustment["applied_delta"] == 0.0


# --- protections ----------------------------------------------------------------


def _gate(**overrides: Any) -> str:
    params: dict[str, Any] = {
        "candidate": {"market_type": "free_agent", "position": "RB"},
        "drop_candidate": {"player_id": "d", "position": "RB"},
        "roster_players": [],
        "move_score": 10.0,
        "base_move_score": 10.0,
        "projected_gain": 5.0,
        "damage_score": 0.0,
        "streamer_penalty": 0.0,
        "stash_penalty": 0.0,
        "usage_adjustment": None,
    }
    params.update(overrides)
    return waiver_recommendation(**params)


def test_context_cannot_lift_a_move_past_roster_damage_or_unknown_market_gates() -> None:
    assert _gate(damage_score=25.0, base_move_score=-1.0, move_score=5.0) == "reject"
    assert _gate(candidate={"market_type": "unknown", "position": "RB"}, base_move_score=4.0, move_score=9.0) == "reject"
    assert _gate(drop_candidate={}, move_score=50.0) == "reject"


def test_severe_one_off_flag_caps_recommend_at_watch() -> None:
    severe = {"add": {"severe_one_off_flags": ["low_touch_big_points"]}}

    assert _gate() == "recommend"
    assert _gate(usage_adjustment=severe) == "watch"


def test_score_waiver_move_records_base_and_adjusted_scores() -> None:
    candidate = {"player_id": "add", "name": "Add RB", "position": "RB", "projected_points": 12, "market_type": "free_agent", "usage_context": summary(codes=("role_change_recent",))}
    drop = {"player_id": "drop", "name": "Drop RB", "position": "RB", "projected_points": 4, "lineup_status": "bench", "usage_context": summary(codes=("role_loss_recent",), role="role_declining")}
    starter = {"player_id": "start", "name": "Start RB", "position": "RB", "projected_points": 15, "lineup_status": "starter"}

    move = score_waiver_move(candidate=candidate, drop_candidate=drop, roster_players=[starter, drop], rejected_drops=[])

    assert move["usage_context_adjustment"]["status"] == "applied"
    assert move["move_score"] == round(move["move_score_before_context"] + 6.0, 2)
    assert "usage context +6.00" in move["reasoning_summary"]


def test_score_waiver_move_without_drop_stays_rejected() -> None:
    candidate = {"player_id": "add", "name": "Add RB", "position": "RB", "projected_points": 40, "market_type": "free_agent", "usage_context": summary(codes=("role_change_recent",))}

    move = score_waiver_move(candidate=candidate, drop_candidate={}, roster_players=[], rejected_drops=[])

    assert move["recommendation"] == "reject"
    assert move["usage_context_adjustment"]["applied_delta"] == 0.0


# --- provider ---------------------------------------------------------------------


class RecordingService:
    def __init__(self, repository: Any) -> None:
        self.calls: list[dict[str, Any]] = []

    def build(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs.get("position") == "XX":
            raise ValueError("position must be one of QB, RB, WR, TE, K, DEF")
        return {"schema_version": "player_usage_context.v3", "player_id": kwargs["player_id"], "position": kwargs.get("position") or "RB", "week": kwargs["week"], "actual_first": True, "scores": {"td_dependency_score": 10.0}, "td_dependency_method": "league_scoring_contributions", "reason_codes": [], "missing_inputs": ["red_zone_share"]}


def test_provider_uses_completed_weeks_and_memoizes() -> None:
    services: list[RecordingService] = []

    def factory(repository: Any) -> RecordingService:
        services.append(RecordingService(repository))
        return services[-1]

    provider = UsageContextProvider(lambda: object(), season=2026, decision_week=6, service_factory=factory)
    first = provider.get("p1", "RB")
    provider.get("p1", "RB")

    assert len(services) == 1 and len(services[0].calls) == 1
    assert services[0].calls[0]["week"] == 5
    assert first["evaluated_through_week"] == 5
    assert first["decision_week"] == 6
    assert first["provider_inputs_for_missing"] == {"red_zone_share": "red_zone_use"}


@pytest.mark.parametrize(
    ("player_id", "position", "week", "status"),
    [
        ("p1", "RB", 1, "not_evaluable_no_completed_weeks"),
        ("0", "RB", 5, "not_evaluable_missing_player_id"),
        ("p1", "LB", 5, "not_evaluable_unsupported_position"),
    ],
)
def test_provider_reports_explicit_not_evaluable_status(player_id: str, position: str, week: int, status: str) -> None:
    provider = UsageContextProvider(lambda: object(), season=2026, decision_week=week, service_factory=RecordingService)

    result = provider.get(player_id, position)

    assert result["status"] == status
    assert result["not_evaluable"] == [status]
    assert result["td_dependency"] is None


def test_attach_marks_candidates_outside_the_budget() -> None:
    rows = attach_usage_contexts([{"player_id": "a", "position": "RB"}, {"player_id": "b", "position": "WR"}], {"a": {"status": "evaluated"}}, decision_week=5)

    assert rows[0]["usage_context"] == {"status": "evaluated"}
    assert rows[1]["usage_context"]["status"] == "not_evaluated_outside_context_budget"


def test_provider_input_mapping_joins_red_zone_and_leaves_different_metrics_unmapped() -> None:
    valid = {item.value for item in ContextInput}

    assert USAGE_INPUT_PROVIDER_INPUTS["red_zone_share"] == ContextInput.RED_ZONE_USE.value == "red_zone_use"
    assert USAGE_INPUT_PROVIDER_INPUTS["implied_total"] == "implied_totals"
    assert USAGE_INPUT_PROVIDER_INPUTS["touch_share"] is None
    assert USAGE_INPUT_PROVIDER_INPUTS["air_yard_share"] is None
    assert all(value in valid for value in USAGE_INPUT_PROVIDER_INPUTS.values() if value is not None)


def test_compare_td_penalty_ignores_heuristic_scores() -> None:
    profile = {"position": "RB", "td_dependency_method": "heuristic", "scores": {"td_dependency_score": 90}}

    assert compare_td_dependency_penalty(profile) == 0.0


# --- end to end through FantasyToolRunner ------------------------------------------


def _stats(player_id: str, team: str, rush_att: float, rec_tgt: float, rush_td: float = 0) -> dict[str, Any]:
    return {"player_id": player_id, "player": {"full_name": player_id, "team": team, "position": "RB"}, "stats": {"rush_att": rush_att, "rec_tgt": rec_tgt, "rec": rec_tgt, "rush_yd": rush_att * 4, "rush_td": rush_td, "pts_ppr": rush_att * 0.4}}


def usage_repository(tmp_path) -> SQLiteNormalizedRepositoryAdapter:
    """Production-shaped repository (adapter over the SQLite store)."""
    repo = SleeperNormalizedRepository(tmp_path / "usage.db")
    repo.upsert_players({
        "free-rb": {"full_name": "Free RB", "team": "DEN", "position": "RB", "status": "Active"},
        "drop-rb": {"full_name": "Drop RB", "team": "LV", "position": "RB", "status": "Active"},
    })
    # free-rb: weighted touches 6, 6 then 18, 18 (role change from week 3).
    # drop-rb: 18, 18 then 5, 5 (role loss from week 3).
    weekly = {1: ((5, 1), (15, 3)), 2: ((5, 1), (15, 3)), 3: ((15, 3), (4, 1)), 4: ((15, 3), (4, 1))}
    for week, ((free_att, free_tgt), (drop_att, drop_tgt)) in weekly.items():
        repo.upsert_player_week_rows(
            season=2026,
            week=week,
            source="stats",
            rows=[_stats("free-rb", "DEN", free_att, free_tgt), _stats("drop-rb", "LV", drop_att, drop_tgt)],
            scoring_settings=SCORING,
        )
    return SQLiteNormalizedRepositoryAdapter(repo)


def test_player_card_includes_usage_context_through_last_completed_week(tmp_path) -> None:
    repo = usage_repository(tmp_path)
    runner = FantasyToolRunner(client_factory=lambda: FakeMcpClient(), players_cache=tmp_path / "players.json", decision_repository=repo)

    card = runner.player_card(player_id="free-rb", league_id="league-1", season=2026, week=5, weeks_back=2)

    context = card["usage_context"]
    assert context["status"] == "evaluated"
    assert context["evaluated_through_week"] == 4
    assert context["role_label"] == "emerging_rotation"
    assert "role_change_recent" in context["positive_signals"]
    assert context["td_dependency"]["status"] == "evaluated"
    assert context["td_dependency"]["excess_pct"] == 0.0
    assert context["provider_inputs_for_missing"]["red_zone_share"] == "red_zone_use"
    assert card["chart_data"]["weekly_points"]  # existing fields stay
    repo.close()


def test_player_card_can_skip_usage_context(tmp_path) -> None:
    runner = FantasyToolRunner(client_factory=lambda: FakeMcpClient(), players_cache=tmp_path / "players.json", decision_repository=usage_repository(tmp_path))

    card = runner.player_card(player_id="free-rb", season=2026, week=5, include_usage_context=False)

    assert "usage_context" not in card


def test_lineup_recommendations_apply_bounded_context_to_waiver_moves(tmp_path) -> None:
    repo = usage_repository(tmp_path)
    runner = FantasyToolRunner(client_factory=lambda: FakeLineupClient(), players_cache=tmp_path / "players.json", decision_repository=repo)

    report = runner.lineup_recommendations(league_id="league-1", roster_id=1, season=2026, week=5, positions="RB,WR", min_delta=1, limit=3)

    pick = report["waiver_comparisons"][0] if report["waiver_comparisons"] else report["waiver_watch_items"][0]
    assert pick["add_player_id"] == "free-rb"
    assert pick["drop_player_id"] == "drop-rb"
    adjustment = pick["usage_context_adjustment"]
    assert adjustment["status"] == "applied"
    assert adjustment["add"]["components"]["opportunity_role"]["codes"] == ["role_change_recent"]
    assert adjustment["drop"]["components"]["opportunity_role"]["codes"] == ["role_loss_recent"]
    assert pick["move_score"] == round(pick["move_score_before_context"] + adjustment["delta"], 2)
    assert pick["add_usage_context"]["evaluated_through_week"] == 4
    assert pick["drop_usage_context"]["role_label"] == "role_declining"
    assert pick["projected_gain_over_drop"] == 25  # projection evidence is unchanged
    lineup_rows = {row["player_id"]: row for row in report["current_lineup"]["lineup_table"]}
    assert lineup_rows["drop-rb"]["usage_context"]["status"] == "evaluated"
    assert lineup_rows["starter-rb"]["usage_context"]["status"] == "not_evaluable_missing_actual_stats"
    start_sit = report["start_sit"][0]
    assert start_sit["projected_gain"] == 10.0
    assert "start_usage_context" in start_sit and "sit_usage_context" in start_sit
    assert any("usage context" in line for line in start_sit["evidence"])
    repo.close()


def test_my_lineup_rows_carry_usage_context(tmp_path) -> None:
    repo = usage_repository(tmp_path)
    runner = FantasyToolRunner(client_factory=lambda: FakeLineupClient(), players_cache=tmp_path / "players.json", decision_repository=repo)

    lineup = runner.my_lineup(league_id="league-1", roster_id=1, season=2026, week=5, positions="RB,WR")

    rows = {row["player_id"]: row for row in lineup["lineup_table"]}
    assert rows["drop-rb"]["usage_context"]["risk_signals"] == ["role_loss_recent"]
    assert all("usage_context" in row for row in lineup["starters"])
    repo.close()


def test_waiver_wire_by_position_marks_candidates_outside_budget(tmp_path) -> None:
    repo = usage_repository(tmp_path)
    runner = FantasyToolRunner(client_factory=lambda: FakeLineupClient(), players_cache=tmp_path / "players.json", decision_repository=repo)

    report = runner.waiver_wire_by_position(league_id="league-1", roster_id=1, season=2026, week=5, positions="RB,WR", per_position_limit=1)

    accepted = [row for rows in report["by_position"].values() for row in rows]
    assert accepted and all(row["add_usage_context"]["status"] != "not_evaluated_outside_context_budget" for row in accepted)
    rb = report["by_position"]["RB"][0]
    assert rb["add_player_id"] == "free-rb"
    assert rb["usage_context_adjustment"]["status"] == "applied"
    statuses = {row["add_usage_context"]["status"] for rows in report["diagnostics_by_position"].values() for row in rows}
    assert statuses <= {"evaluated", "not_evaluable_missing_actual_stats", "not_evaluated_outside_context_budget"}
    repo.close()


def test_waiver_wire_watch_candidates_carry_usage_context(tmp_path) -> None:
    repo = usage_repository(tmp_path)
    runner = FantasyToolRunner(client_factory=lambda: FakeLineupClient(), players_cache=tmp_path / "players.json", decision_repository=repo)

    report = runner.waiver_wire_watch(league_id="league-1", season=2026, week=5, positions="RB,WR")

    by_id = {row["player_id"]: row for row in report["candidates"]}
    assert by_id["free-rb"]["usage_context"]["role_label"] == "emerging_rotation"
    repo.close()
