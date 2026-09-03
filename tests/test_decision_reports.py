from __future__ import annotations

from sleeper_tooling.decision_reports import (
    build_injury_watch,
    build_waiver_watch,
    is_injury_relevant,
    rostered_player_ids,
)


def test_rostered_player_ids_collects_all_roster_players() -> None:
    assert rostered_player_ids(
        [
            {"players": ["1", "2"]},
            {"players": ["3", None]},
        ]
    ) == {"1", "2", "3"}


def test_build_waiver_watch_filters_rostered_players_and_sorts_by_projection() -> None:
    rows = build_waiver_watch(
        trends=[
            {"player_id": "rostered", "count": 100},
            {"player_id": "low", "count": 500},
            {"player_id": "high", "count": 10},
            {"player_id": "wrong-position", "count": 999},
        ],
        players={
            "rostered": {"full_name": "Rostered RB", "team": "DEN", "position": "RB"},
            "low": {"full_name": "Low RB", "team": "KC", "position": "RB"},
            "high": {"full_name": "High RB", "team": "LV", "position": "RB"},
            "wrong-position": {"full_name": "Wrong WR", "team": "LAC", "position": "WR"},
        },
        projection_rows=[
            {"player_id": "low", "points": 8, "sleeper_points": 10},
            {"player_id": "high", "points": 12, "sleeper_points": 11},
        ],
        rosters=[{"players": ["rostered"]}],
        positions=["RB"],
        trend_type="add",
    )

    assert rows == [
        {
            "player_id": "high",
            "name": "High RB",
            "team": "LV",
            "position": "RB",
            "trend_type": "add",
            "trend_count": 10,
            "projected_points": 12,
            "sleeper_projected_points": 11,
            "status": "",
            "injury_status": "",
        },
        {
            "player_id": "low",
            "name": "Low RB",
            "team": "KC",
            "position": "RB",
            "trend_type": "add",
            "trend_count": 500,
            "projected_points": 8,
            "sleeper_projected_points": 10,
            "status": "",
            "injury_status": "",
        },
    ]


def test_is_injury_relevant_uses_injury_status_or_non_active_status() -> None:
    assert is_injury_relevant({"status": "Active", "injury_status": ""}) is False
    assert is_injury_relevant({"status": "Active", "injury_status": "Questionable"}) is True
    assert is_injury_relevant({"status": "Injured Reserve", "injury_status": ""}) is True


def test_build_injury_watch_lists_rostered_injury_relevant_players() -> None:
    rows = build_injury_watch(
        users=[
            {
                "user_id": "u1",
                "display_name": "Neil",
                "metadata": {"team_name": "Mile High"},
            }
        ],
        rosters=[{"roster_id": 1, "owner_id": "u1", "players": ["healthy", "hurt"]}],
        players={
            "healthy": {"full_name": "Healthy Player", "status": "Active"},
            "hurt": {
                "full_name": "Hurt Player",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": "Questionable",
            },
        },
    )

    assert rows == [
        {
            "roster_id": 1,
            "owner_id": "u1",
            "team_name": "Mile High",
            "player_id": "hurt",
            "name": "Hurt Player",
            "team": "DEN",
            "position": "RB",
            "status": "Active",
            "injury_status": "Questionable",
        }
    ]
