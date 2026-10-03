from __future__ import annotations

from sleeper_tooling.db import SleeperNormalizedRepository
from sleeper_tooling.matchup_model import (
    MATCHUP_MODEL_VERSION,
    build_matchup_profile,
    build_repository_matchup_profile,
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
        "weekly_availability": False,
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


def test_qb_features_use_pass_sack_and_unsupported_position_is_explicit() -> None:
    result = build_matchup_profile(
        player_id="qb-1",
        season=2026,
        week=4,
        player={"position": "QB", "team": "DEN"},
        opponent="KC",
        opponent_rows=[_row("QB", 10, pass_sack=4)],
        league_rows=[_row("QB", 10, pass_sack=1)],
        availability={"status": "Active"},
    )
    assert result["evidence"]["opponent_features"]["pass_sacks_per_game"] == 4.0
    assert result["source_availability"]["weekly_availability"] is True

    unsupported = build_matchup_profile(
        player_id="idp-1",
        season=2026,
        week=4,
        player={"position": "DL", "team": "DEN"},
        opponent="KC",
        availability={"status": "Active"},
    )
    assert unsupported["position"] == "DL"
    assert unsupported["matchup_adjustment"] == 0.0
    assert "unsupported_position" in unsupported["missing_inputs"]


def test_repository_profile_joins_historical_row_opponent_and_availability(tmp_path) -> None:
    repository = SleeperNormalizedRepository(tmp_path / "normalized.sqlite")
    repository.upsert_player_week_rows(
        season=2026,
        week=1,
        source="stats",
        rows=[
            {"player_id": "qb-1", "team": "DEN", "position": "QB", "stats": {"pass_sack": 1}, "opponent": "KC", "fantasy_points": 20},
            {"player_id": "kc-qb", "team": "KC", "position": "QB", "stats": {"pass_sack": 9}, "opponent": "DEN", "fantasy_points": 99},
            {"player_id": "oak-qb", "team": "OAK", "position": "QB", "stats": {"pass_sack": 2}, "opponent": "KC", "fantasy_points": 10},
        ],
    )
    repository.upsert_team_week_schedule(
        season=2026,
        week=1,
        rows=[{"team": "DEN", "opponent": "KC", "home_away": "away"}],
        source="test",
    )
    repository.upsert_player_week_availability(
        season=2026,
        week=1,
        rows=[{"team": "DEN", "player_id": "qb-1", "status": "Active"}],
        source="test",
    )

    result = build_repository_matchup_profile(
        repository, player_id="qb-1", season=2026, week=1
    )

    assert result["evidence"]["historical_games"] == 1
    assert result["evidence"]["opponent_features"]["points_allowed_per_game"] == 10.0
    assert result["evidence"]["weekly_availability"]["status"] == "Active"
    assert result["source_availability"]["weekly_availability"] is True
    repository.close()


def test_repository_profile_returns_missing_source_result(tmp_path) -> None:
    repository = SleeperNormalizedRepository(tmp_path / "normalized.sqlite")
    result = build_repository_matchup_profile(
        repository, player_id="missing", season=2026, week=1
    )
    assert result["matchup_adjustment"] == 0.0
    assert "not_evaluable_missing_normalized_stats_row" in result["missing_inputs"]
    repository.close()
