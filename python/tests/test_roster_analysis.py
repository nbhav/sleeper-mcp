from __future__ import annotations

from sleeper_tooling.roster_analysis import (
    build_league_roster_analysis,
    build_roster_analysis,
)


def test_roster_analysis_flags_low_rb_wr_depth_and_questionable_starter_without_coverage() -> None:
    report = build_roster_analysis(**base_inputs())

    need_positions = {row["position"]: row for row in report["need_positions"]}
    assert "RB" in need_positions
    assert "WR" in need_positions
    assert "TE" in need_positions
    assert "no playable bench cover" in need_positions["WR"]["reasons"]
    assert "starter risk lacks coverage" in need_positions["TE"]["reasons"]

    te_risk = next(row for row in report["injury_risks"] if row["position"] == "TE")
    assert te_risk["severity"] == "high"
    assert te_risk["coverage_status"] == "thin"

    assert report["position_groups"]["RB"]["playable_count"] == 2
    assert report["position_groups"]["WR"]["bench_count"] == 0
    assert report["position_groups"]["WR"]["replacement_risk"] == "medium"


def test_roster_analysis_protects_ir_and_last_playable_coverage() -> None:
    inputs = base_inputs()
    inputs["players"]["bench_rb_playable"] = player("Bench RB Playable", "RB")
    inputs["projection_rows"].append({"player_id": "bench_rb_playable", "points": 9})
    inputs["rosters"][0]["players"].append("bench_rb_playable")

    report = build_roster_analysis(**inputs)
    protected = {row["player_id"]: row for row in report["protected_players"]}

    assert "ir_rb" in protected
    assert "reserve/IR protected by default" in protected["ir_rb"]["reasons"]
    assert "bench_rb_playable" in protected
    assert "last playable backup at position" in protected["bench_rb_playable"]["reasons"]


def test_roster_analysis_makes_low_value_qb_backup_movable_in_one_qb_league() -> None:
    report = build_roster_analysis(**base_inputs())

    protected_ids = {row["player_id"] for row in report["protected_players"]}
    movable = {row["player_id"]: row for row in report["movable_players"]}

    assert "bench_qb" not in protected_ids
    assert movable["bench_qb"]["reason"] == (
        "1QB backup has low need without starter injury/bye or high-value stash profile"
    )
    assert "QB" not in {row["position"] for row in report["need_positions"]}


def test_roster_analysis_classifies_elite_k_def_holds_and_streamers() -> None:
    inputs = base_inputs()
    inputs["players"]["bench_k_elite"] = player("Bench K Elite", "K")
    inputs["players"]["bench_def_streamer"] = player("Bench DEF Streamer", "DEF")
    inputs["projection_rows"].extend(
        [
            {"player_id": "bench_k_elite", "points": 9.4},
            {"player_id": "bench_def_streamer", "points": 5.2},
        ]
    )
    inputs["rosters"][0]["players"].extend(["bench_k_elite", "bench_def_streamer"])

    report = build_roster_analysis(**inputs)
    protected = {row["player_id"]: row for row in report["protected_players"]}
    movable = {row["player_id"]: row for row in report["movable_players"]}
    streaming = {row["position"]: row for row in report["streaming_slots"]}

    assert report["position_groups"]["K"]["k_def_classification"] == "elite hold"
    assert "elite K/DEF hold" in protected["bench_k_elite"]["reasons"]
    assert streaming["DEF"]["classification"] == "streamer"
    assert movable["bench_def_streamer"]["reason"] == "DEF is streamer"


def test_league_roster_analysis_uses_same_api_for_opponent_weaknesses() -> None:
    inputs = base_inputs()
    report = build_league_roster_analysis(
        league_id=inputs["league_id"],
        season=inputs["season"],
        week=inputs["week"],
        league=inputs["league"],
        users=inputs["users"],
        rosters=inputs["rosters"],
        matchups=inputs["matchups"],
        players=inputs["players"],
        projection_rows=inputs["projection_rows"],
    )

    teams_by_id = {team["roster_id"]: team for team in report["teams"]}
    opponent_needs = {row["position"] for row in teams_by_id[2]["need_positions"]}

    assert set(teams_by_id) == {1, 2}
    assert teams_by_id[1]["team_name"] == "My Team"
    assert teams_by_id[2]["team_name"] == "Opponent"
    assert "RB" in opponent_needs
    assert teams_by_id[2]["position_groups"]["RB"]["replacement_risk"] == "high"


def base_inputs() -> dict:
    roster_one_players = [
        "qb1",
        "rb1",
        "rb2",
        "wr1",
        "wr2",
        "wr3",
        "te1",
        "k1",
        "def1",
        "bench_qb",
        "bench_rb_low",
        "ir_rb",
    ]
    roster_two_players = [
        "opp_qb",
        "opp_rb1",
        "opp_rb2",
        "opp_wr1",
        "opp_wr2",
        "opp_wr3",
        "opp_te",
        "opp_k",
        "opp_def",
    ]
    all_players = {
        "qb1": player("Starter QB", "QB"),
        "rb1": player("RB One", "RB"),
        "rb2": player("RB Two", "RB"),
        "wr1": player("WR One", "WR"),
        "wr2": player("WR Two", "WR"),
        "wr3": player("WR Three", "WR"),
        "te1": player("Risky TE", "TE", injury_status="Questionable"),
        "k1": player("Starter K", "K"),
        "def1": player("Starter DEF", "DEF"),
        "bench_qb": player("Bench QB", "QB"),
        "bench_rb_low": player("Bench RB Low", "RB"),
        "ir_rb": player("IR RB", "RB", status="Injured Reserve"),
        "opp_qb": player("Opponent QB", "QB"),
        "opp_rb1": player("Opponent RB One", "RB"),
        "opp_rb2": player("Opponent RB Two", "RB"),
        "opp_wr1": player("Opponent WR One", "WR"),
        "opp_wr2": player("Opponent WR Two", "WR"),
        "opp_wr3": player("Opponent WR Three", "WR"),
        "opp_te": player("Opponent TE", "TE"),
        "opp_k": player("Opponent K", "K"),
        "opp_def": player("Opponent DEF", "DEF"),
    }
    projections = {
        "qb1": 20,
        "rb1": 13,
        "rb2": 10,
        "wr1": 16,
        "wr2": 12,
        "wr3": 9,
        "te1": 7,
        "k1": 6,
        "def1": 5.5,
        "bench_qb": 15,
        "bench_rb_low": 3,
        "ir_rb": 9,
        "opp_qb": 18,
        "opp_rb1": 9,
        "opp_rb2": 2,
        "opp_wr1": 17,
        "opp_wr2": 13,
        "opp_wr3": 11,
        "opp_te": 7,
        "opp_k": 6,
        "opp_def": 6,
    }
    return {
        "league_id": "league-1",
        "roster_id": 1,
        "season": 2026,
        "week": 4,
        "league": {
            "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "K", "DEF", "BN", "IR"]
        },
        "users": [
            {"user_id": "u1", "display_name": "Neil", "metadata": {"team_name": "My Team"}},
            {"user_id": "u2", "display_name": "Opponent"},
        ],
        "rosters": [
            {
                "roster_id": 1,
                "owner_id": "u1",
                "players": roster_one_players,
                "reserve": ["ir_rb"],
            },
            {
                "roster_id": 2,
                "owner_id": "u2",
                "players": roster_two_players,
            },
        ],
        "matchups": [
            {
                "roster_id": 1,
                "starters": roster_one_players[:9],
                "players": roster_one_players,
                "players_points": {"qb1": 4.2},
            },
            {
                "roster_id": 2,
                "starters": roster_two_players,
                "players": roster_two_players,
                "players_points": {},
            },
        ],
        "players": all_players,
        "projection_rows": [
            {"player_id": player_id, "points": points}
            for player_id, points in projections.items()
        ],
    }


def player(
    name: str,
    position: str,
    *,
    status: str = "Active",
    injury_status: str = "",
    bye_week: int = 11,
) -> dict:
    return {
        "full_name": name,
        "position": position,
        "fantasy_positions": [position],
        "team": "DEN",
        "status": status,
        "injury_status": injury_status,
        "bye_week": bye_week,
    }
