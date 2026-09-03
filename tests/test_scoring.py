from __future__ import annotations

from sleeper_tooling.scoring import calculate_fantasy_points, flatten_scored_player_rows


def test_calculate_fantasy_points_applies_league_scoring_settings() -> None:
    points, breakdown = calculate_fantasy_points(
        {"rush_yd": 75, "rush_td": 1, "rec": 3, "unused": 100},
        {"rush_yd": 0.1, "rush_td": 6, "rec": 0.5},
    )

    assert points == 15
    assert breakdown == {"rush_yd": 7.5, "rush_td": 6, "rec": 1.5}


def test_calculate_fantasy_points_ignores_missing_and_nonnumeric_values() -> None:
    points, breakdown = calculate_fantasy_points(
        {"rush_yd": 10, "rush_td": None},
        {"rush_yd": "0.1", "rush_td": 6, "bad": "nope"},
    )

    assert points == 1
    assert breakdown == {"rush_yd": 1}


def test_flatten_scored_player_rows_preserves_sleeper_points_for_comparison() -> None:
    rows = flatten_scored_player_rows(
        [
            {
                "player_id": "1",
                "player": {"full_name": "Custom Scored", "team": "DEN", "position": "RB"},
                "stats": {"rush_yd": 100, "rec": 4, "pts_ppr": 20},
            }
        ],
        {"rush_yd": 0.1, "rec": 0.5},
    )

    assert rows == [
        {
            "player_id": "1",
            "name": "Custom Scored",
            "team": "DEN",
            "position": "RB",
            "points": 12,
            "sleeper_points": 20,
            "scoring_rules_matched": 2,
            "scoring_breakdown": {"rush_yd": 10, "rec": 2},
        }
    ]
