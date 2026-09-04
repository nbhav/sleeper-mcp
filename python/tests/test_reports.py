from __future__ import annotations

from sleeper_tooling.reports import (
    build_league_week_report,
    enrich_trending_players,
    flatten_player_map,
    flatten_player_rows,
    top_players_by_position,
    top_players_by_team,
)


def test_build_league_week_report_joins_users_rosters_matchups_and_players() -> None:
    report = build_league_week_report(
        users=[
            {
                "user_id": "u1",
                "display_name": "Neil",
                "metadata": {"team_name": "Mile High"},
            }
        ],
        rosters=[{"roster_id": 1, "owner_id": "u1"}],
        matchups=[
            {
                "matchup_id": 2,
                "roster_id": 1,
                "points": 101.2,
                "starters": ["p1"],
                "players": ["p1", "p2"],
                "players_points": {"p1": 20.5, "p2": 4.1},
            }
        ],
        players={
            "p1": {"full_name": "Starter One", "team": "DEN", "position": "RB"},
            "p2": {"full_name": "Bench Two", "team": "KC", "position": "WR"},
        },
    )

    assert report[0]["team_name"] == "Mile High"
    assert report[0]["starters"][0]["name"] == "Starter One"
    assert report[0]["bench"][0]["points"] == 4.1


def test_flatten_player_rows_defaults_missing_stats_to_zero() -> None:
    rows = flatten_player_rows(
        [
            {
                "player_id": "1",
                "player": {"full_name": "Missing Stats", "position": "QB"},
                "stats": {},
            }
        ]
    )

    assert rows == [
        {
            "player_id": "1",
            "name": "Missing Stats",
            "team": "",
            "position": "QB",
            "points": 0,
        }
    ]


def test_flatten_player_map_turns_id_keyed_map_into_rows() -> None:
    rows = flatten_player_map(
        {
            "2": {
                "full_name": "Player Two",
                "team": "DEN",
                "position": "WR",
                "fantasy_positions": ["WR", "FLEX"],
                "status": "Active",
            }
        }
    )

    assert rows == [
        {
            "player_id": "2",
            "name": "Player Two",
            "team": "DEN",
            "position": "WR",
            "fantasy_positions": "WR,FLEX",
            "status": "Active",
            "injury_status": "",
        }
    ]


def test_top_players_by_position_ranks_each_position_independently() -> None:
    rows = [
        {"player_id": "1", "name": "QB One", "position": "QB", "team": "DEN", "points": 10},
        {"player_id": "2", "name": "QB Two", "position": "QB", "team": "KC", "points": 20},
        {"player_id": "3", "name": "RB One", "position": "RB", "team": "LV", "points": 5},
        {"player_id": "4", "name": "RB Two", "position": "RB", "team": "LAC", "points": 15},
    ]

    leaders = top_players_by_position(rows, limit=1)

    assert leaders == [
        {
            "position_rank": 1,
            "player_id": "2",
            "name": "QB Two",
            "position": "QB",
            "team": "KC",
            "points": 20,
        },
        {
            "position_rank": 1,
            "player_id": "4",
            "name": "RB Two",
            "position": "RB",
            "team": "LAC",
            "points": 15,
        },
    ]


def test_top_players_by_team_selects_one_leader_per_team() -> None:
    rows = [
        {"player_id": "1", "name": "Back One", "position": "RB", "team": "DEN", "points": 10},
        {"player_id": "2", "name": "Back Two", "position": "RB", "team": "DEN", "points": 20},
        {"player_id": "3", "name": "Back Three", "position": "RB", "team": "KC", "points": 15},
    ]

    leaders = top_players_by_team(rows)

    assert leaders == [
        {
            "team_rank": 1,
            "player_id": "2",
            "name": "Back Two",
            "position": "RB",
            "team": "DEN",
            "points": 20,
        },
        {
            "team_rank": 1,
            "player_id": "3",
            "name": "Back Three",
            "position": "RB",
            "team": "KC",
            "points": 15,
        },
    ]


def test_enrich_trending_players_joins_player_metadata() -> None:
    rows = enrich_trending_players(
        [{"player_id": "10", "count": 50}],
        {
            "10": {
                "full_name": "Trending Player",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": "Questionable",
            }
        },
        trend_type="add",
    )

    assert rows == [
        {
            "player_id": "10",
            "name": "Trending Player",
            "team": "DEN",
            "position": "RB",
            "trend_type": "add",
            "count": 50,
            "status": "Active",
            "injury_status": "Questionable",
        }
    ]
