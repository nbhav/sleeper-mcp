from __future__ import annotations

from sleeper_tooling.matchup_model import (
    MATCHUP_MODEL_VERSION,
    build_matchup_profile,
)


def _row(position: str, points: float, **stats: float) -> dict[str, object]:
    return {
        "player_id": f"{position}-{points}",
        "position": position,
        "team": "DEN",
        "fantasy_points": points,
        "stats": stats,
    }


def test_supported_schedule_join_computes_position_matchup_and_availability() -> None:
    result = build_matchup_profile(
        player_id="qb-1",
        season=2026,
        week=4,
        player={"position": "QB", "team": "DEN"},
        opponent="KC",
        opponent_rows=[_row("QB", 30), _row("QB", 28)],
        league_rows=[_row("QB", 10), _row("QB", 10), _row("QB", 10), _row("QB", 10)],
    )

    assert result["model_version"] == MATCHUP_MODEL_VERSION
    assert result["opponent"] == "KC"
    assert result["matchup_adjustment"] == 2.0
    assert result["source_availability"] == {
        "normalized_player_stats": False,
        "nfl_schedule": True,
        "historical_opponent_stats": True,
        "enriched_provider": False,
    }
    assert "not_evaluable_missing_nfl_schedule" not in result["missing_inputs"]
    assert "not_evaluable_missing_pressure" in result["missing_inputs"]


def test_missing_schedule_disables_matchup_instead_of_guessing() -> None:
    result = build_matchup_profile(
        player_id="rb-1",
        season=2026,
        week=4,
        player={"position": "RB", "team": "DEN"},
        opponent=None,
        schedule_available=False,
        opponent_rows=[_row("RB", 40, rush_yd=120)],
        league_rows=[_row("RB", 10), _row("RB", 11)],
    )

    assert result["matchup_adjustment"] == 0.0
    assert "not_evaluable_missing_nfl_schedule" in result["missing_inputs"]
    assert result["source_availability"]["nfl_schedule"] is False


def test_defense_uses_three_point_cap_and_reports_missing_enriched_inputs() -> None:
    result = build_matchup_profile(
        player_id="def-1",
        season=2026,
        week=4,
        player={"position": "DEF", "team": "DEN"},
        opponent="LV",
        opponent_rows=[_row("DEF", 35), _row("DEF", 35)],
        league_rows=[_row("DEF", 5), _row("DEF", 6), _row("DEF", 7)],
    )

    assert result["matchup_adjustment"] == 3.0
    assert result["matchup_cap"] == 3.0
    assert "not_evaluable_missing_implied_totals" in result["missing_inputs"]
    assert "not_evaluable_missing_pressure" in result["missing_inputs"]


def test_provider_fields_are_used_without_removing_other_missing_reasons() -> None:
    result = build_matchup_profile(
        player_id="k-1",
        season=2026,
        week=4,
        player={"position": "K", "team": "DEN"},
        opponent="KC",
        opponent_rows=[_row("K", 9)],
        league_rows=[_row("K", 9)],
        provider_context={"weather": {"wind_mph": 4}, "implied_totals": 23.5},
    )

    assert "not_evaluable_missing_weather" not in result["missing_inputs"]
    assert "not_evaluable_missing_implied_totals" not in result["missing_inputs"]
    assert result["evidence"]["provider_context"]["implied_totals"] == 23.5
