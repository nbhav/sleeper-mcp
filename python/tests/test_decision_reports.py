from __future__ import annotations

from sleeper_tooling.decision_reports import (
    build_injury_watch,
    build_trade_opportunities,
    build_waiver_watch,
    compare_available_player,
    group_waiver_options_by_position,
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

    assert [row["player_id"] for row in rows] == ["high", "low"]
    assert rows[0]["projected_points"] == 12
    assert rows[0]["market_type"] == "unknown"
    assert rows[0]["acquisition_action"] == "watch"
    assert rows[0]["source_metadata"]["player_context"] == [
        "sleeper_players",
        "sleeper_projections",
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


def test_waiver_matrix_does_not_make_playable_skill_depth_universal_drop() -> None:
    roster_players = [
        roster_row("elite-qb", "Elite QB", "QB", 24, lineup_status="starter"),
        roster_row("rb1", "Starter RB", "RB", 14, lineup_status="starter"),
        roster_row("wr1", "Starter WR", "WR", 13, lineup_status="starter"),
        roster_row("travis", "Travis Hunter", "WR", 9, lineup_status="bench"),
        roster_row("backup-qb", "Bench QB", "QB", 12, lineup_status="bench"),
        roster_row("deep-k", "Bench K", "K", 4, lineup_status="bench"),
    ]
    candidates = [
        waiver_candidate("add-qb", "Waiver QB", "QB", 16, market_type="waiver"),
        waiver_candidate("add-k", "Waiver K", "K", 9, market_type="waiver"),
        waiver_candidate("add-def", "Waiver DEF", "DEF", 8, market_type="waiver"),
        waiver_candidate("add-rb", "Waiver RB", "RB", 10, market_type="waiver"),
    ]

    rows = [
        compare_available_player(candidate, roster_players)
        for candidate in candidates
    ]

    assert {row["add_position"]: row["drop_player_id"] for row in rows} == {
        "QB": "deep-k",
        "K": "deep-k",
        "DEF": "deep-k",
        "RB": "deep-k",
    }
    assert all(row["drop_player_id"] != "travis" for row in rows)
    assert any(
        rejected["player_id"] == "travis"
        and rejected["reason"] == "last playable backup at position"
        for row in rows
        for rejected in row["rejected_drop_reasoning"]
    )


def test_waiver_matrix_downgrades_backup_qb_behind_healthy_elite_starter() -> None:
    roster_players = [
        roster_row("elite-qb", "Elite QB", "QB", 24, lineup_status="starter"),
        roster_row("rb-depth", "RB Depth", "RB", 6, lineup_status="bench"),
    ]
    row = compare_available_player(
        waiver_candidate("backup-qb", "Backup QB", "QB", 16, market_type="waiver"),
        roster_players,
    )

    assert row["drop_player_id"] == "rb-depth"
    assert row["projected_gain_over_drop"] == 10
    assert row["recommendation"] == "watch"
    assert row["acquisition_action"] == "watch"
    assert row["stash_penalty"] >= 16
    assert row["positional_need_score"] < 0


def test_waiver_matrix_downgrades_k_def_streamer_over_skill_depth() -> None:
    roster_players = [
        roster_row("starter-k", "Starter K", "K", 7, lineup_status="starter"),
        roster_row("wr-depth", "WR Depth", "WR", 6, lineup_status="bench"),
    ]
    row = compare_available_player(
        waiver_candidate("streamer-k", "Streamer K", "K", 9, market_type="waiver"),
        roster_players,
    )

    assert row["drop_player_id"] == "wr-depth"
    assert row["projected_gain_over_drop"] == 3
    assert row["recommendation"] == "reject"
    assert row["acquisition_action"] == "watch"
    assert row["streamer_penalty"] >= 20


def test_waiver_matrix_keeps_roster_sensible_positive_move() -> None:
    roster_players = [
        roster_row("starter-rb", "Starter RB", "RB", 13, lineup_status="starter"),
        roster_row("drop-rb", "Drop RB", "RB", 5, lineup_status="bench"),
    ]
    row = compare_available_player(
        waiver_candidate("free-rb", "Free RB", "RB", 12, market_type="waiver"),
        roster_players,
    )

    assert row["drop_player_id"] == "drop-rb"
    assert row["recommendation"] == "recommend"
    assert row["acquisition_action"] == "submit_waiver_claim"
    assert row["move_score"] > 0
    assert row["week_value_delta"] == 7


def test_waiver_grouping_ranks_by_move_score_and_keeps_watch_rows() -> None:
    roster_players = [
        roster_row("starter-k", "Starter K", "K", 7, lineup_status="starter"),
        roster_row("drop-rb", "Drop RB", "RB", 5, lineup_status="bench"),
    ]
    grouped = group_waiver_options_by_position(
        candidates=[
            waiver_candidate("free-rb", "Free RB", "RB", 12, market_type="waiver"),
            waiver_candidate("unknown-rb", "Unknown RB", "RB", 11, market_type="unknown"),
        ],
        roster_players=roster_players,
        positions=["RB"],
        per_position_limit=2,
    )

    assert grouped["RB"][0]["recommendation"] == "recommend"
    assert grouped["RB"][1]["recommendation"] == "watch"
    assert grouped["RB"][1]["acquisition_action"] == "watch"
    assert grouped["RB"][1]["market_confidence"] == "low"


def roster_row(
    player_id: str,
    name: str,
    position: str,
    projected_points: float,
    *,
    lineup_status: str,
    status: str = "Active",
    injury_status: str = "",
) -> dict[str, object]:
    return {
        "player_id": player_id,
        "name": name,
        "team": "DEN",
        "position": position,
        "fantasy_positions": [position],
        "lineup_status": lineup_status,
        "slot": "BN" if lineup_status == "bench" else position,
        "projected_points": projected_points,
        "active_roster_spot": lineup_status != "reserve",
        "status": status,
        "injury_status": injury_status,
    }


def waiver_candidate(
    player_id: str,
    name: str,
    position: str,
    projected_points: float,
    *,
    market_type: str,
    rostered_percent: float | None = None,
) -> dict[str, object]:
    return {
        "player_id": player_id,
        "name": name,
        "team": "KC",
        "position": position,
        "fantasy_positions": [position],
        "projected_points": projected_points,
        "market_type": market_type,
        "rostered_percent": rostered_percent,
        "add_trend_count": 0,
        "drop_trend_count": 0,
        "net_trend_count": 0,
    }


def test_trade_package_matrix_supports_required_package_sizes() -> None:
    report = build_trade_opportunities(**trade_matrix_fixture())
    rb_needy = next(team for team in report["teams"] if team["team_name"] == "RB Needy")

    assert report["supported_package_types"] == ["1:1", "2:1", "1:2", "3:2", "2:3"]
    assert {"1:1", "2:1", "1:2", "3:2", "2:3"} <= {
        row["package_type"] for row in rb_needy["package_matrix"]
    }
    top = rb_needy["offer_angles"][0]
    assert "RB" in top["opponent_need_matched"]
    assert {
        "ask",
        "offer",
        "my_gain",
        "opponent_gain",
        "my_week_value_delta",
        "opponent_week_value_delta",
        "my_roster_balance_after",
        "opponent_roster_balance_after",
        "reasoning_summary",
        "rejection_reasons",
    } <= set(top)


def test_trade_offers_vary_by_opponent_need() -> None:
    report = build_trade_opportunities(**trade_matrix_fixture())
    rb_needy = next(team for team in report["teams"] if team["team_name"] == "RB Needy")
    te_needy = next(team for team in report["teams"] if team["team_name"] == "TE Needy")

    rb_offer_positions = {player["position"] for player in rb_needy["offer_angles"][0]["offer"]}
    te_offer_positions = {player["position"] for player in te_needy["offer_angles"][0]["offer"]}

    assert "RB" in rb_offer_positions
    assert "TE" in te_offer_positions
    assert rb_offer_positions != te_offer_positions


def test_trade_matrix_rejects_no_opponent_need_and_my_roster_damage() -> None:
    report = build_trade_opportunities(**trade_matrix_fixture())
    balanced = next(team for team in report["teams"] if team["team_name"] == "Balanced")
    rb_needy = next(team for team in report["teams"] if team["team_name"] == "RB Needy")

    assert any(
        "does not address opponent need or enough value fairness" in row["rejection_reasons"]
        for row in balanced["package_matrix"]
    )
    assert any(
        row["package_type"] in {"2:1", "3:2"}
        and "harms my roster balance" in row["rejection_reasons"]
        for row in rb_needy["package_matrix"]
    )


def test_trade_matrix_suppresses_repeated_generic_packages() -> None:
    report = build_trade_opportunities(**trade_matrix_fixture())
    balanced = next(team for team in report["teams"] if team["team_name"] == "Balanced")
    balanced_too = next(team for team in report["teams"] if team["team_name"] == "Balanced Too")

    assert not balanced["offer_angles"]
    assert not balanced_too["offer_angles"]
    assert any(
        "repeated generic package without independent opponent fit" in row["rejection_reasons"]
        for row in balanced["package_matrix"] + balanced_too["package_matrix"]
    )


def trade_matrix_fixture() -> dict[str, object]:
    users = [
        {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "Me"}},
        {"user_id": "u2", "display_name": "RB Needy", "metadata": {"team_name": "RB Needy"}},
        {"user_id": "u3", "display_name": "TE Needy", "metadata": {"team_name": "TE Needy"}},
        {"user_id": "u4", "display_name": "Balanced", "metadata": {"team_name": "Balanced"}},
        {"user_id": "u5", "display_name": "Balanced Too", "metadata": {"team_name": "Balanced Too"}},
    ]
    points = {
        "my-qb": ("My QB", "QB", 20),
        "my-rb1": ("My RB1", "RB", 15),
        "my-wr1": ("My WR1", "WR", 14),
        "my-te1": ("My TE1", "TE", 10),
        "my-flex": ("My Flex", "WR", 13),
        "offer-rb": ("Offer RB", "RB", 12),
        "offer-rb2": ("Offer RB2", "RB", 11),
        "offer-wr": ("Offer WR", "WR", 10),
        "offer-te": ("Offer TE", "TE", 8),
        "offer-te2": ("Offer TE2", "TE", 7),
        "opp-rb-low": ("Opp Low RB", "RB", 6),
        "opp-wr1": ("Opp WR1", "WR", 15),
        "opp-wr2": ("Opp WR2", "WR", 14),
        "opp-wr3": ("Opp WR3", "WR", 13),
        "opp-te1": ("Opp TE1", "TE", 10),
        "opp-te2": ("Opp TE2", "TE", 7),
        "opp-te-low": ("Opp Low TE", "TE", 4),
        "opp-rb1": ("Opp RB1", "RB", 14),
        "opp-rb2": ("Opp RB2", "RB", 13),
        "opp-rb3": ("Opp RB3", "RB", 9),
        "bal-rb": ("Balanced RB", "RB", 14),
        "bal-wr": ("Balanced WR", "WR", 14),
        "bal-te": ("Balanced TE", "TE", 10),
        "bal-flex": ("Balanced Flex", "WR", 12),
        "bal-bench-rb": ("Balanced Bench RB", "RB", 9),
        "bal-bench-wr": ("Balanced Bench WR", "WR", 9),
        "bal-bench-te": ("Balanced Bench TE", "TE", 7),
        "bal2-rb": ("Balanced2 RB", "RB", 14),
        "bal2-wr": ("Balanced2 WR", "WR", 14),
        "bal2-te": ("Balanced2 TE", "TE", 10),
        "bal2-flex": ("Balanced2 Flex", "WR", 12),
        "bal2-bench-rb": ("Balanced2 Bench RB", "RB", 9),
        "bal2-bench-wr": ("Balanced2 Bench WR", "WR", 9),
        "bal2-bench-te": ("Balanced2 Bench TE", "TE", 7),
    }
    rosters = [
        {
            "roster_id": 1,
            "owner_id": "u1",
            "players": [
                "my-qb",
                "my-rb1",
                "my-wr1",
                "my-te1",
                "my-flex",
                "offer-rb",
                "offer-rb2",
                "offer-wr",
                "offer-te",
                "offer-te2",
            ],
        },
        {"roster_id": 2, "owner_id": "u2", "players": ["my-qb", "opp-rb-low", "opp-wr1", "opp-te1", "opp-wr2", "opp-wr3", "opp-te2"]},
        {"roster_id": 3, "owner_id": "u3", "players": ["my-qb", "opp-rb1", "opp-wr1", "opp-te-low", "opp-wr2", "opp-rb2", "opp-rb3"]},
        {"roster_id": 4, "owner_id": "u4", "players": ["my-qb", "bal-rb", "bal-wr", "bal-te", "bal-flex", "bal-bench-rb", "bal-bench-wr", "bal-bench-te"]},
        {"roster_id": 5, "owner_id": "u5", "players": ["my-qb", "bal2-rb", "bal2-wr", "bal2-te", "bal2-flex", "bal2-bench-rb", "bal2-bench-wr", "bal2-bench-te"]},
    ]
    matchups = [
        trade_matchup(1, ["my-qb", "my-rb1", "my-wr1", "my-te1", "my-flex"], rosters[0]["players"]),
        trade_matchup(2, ["my-qb", "opp-rb-low", "opp-wr1", "opp-te1", "opp-wr2"], rosters[1]["players"]),
        trade_matchup(3, ["my-qb", "opp-rb1", "opp-wr1", "opp-te-low", "opp-wr2"], rosters[2]["players"]),
        trade_matchup(4, ["my-qb", "bal-rb", "bal-wr", "bal-te", "bal-flex"], rosters[3]["players"]),
        trade_matchup(5, ["my-qb", "bal2-rb", "bal2-wr", "bal2-te", "bal2-flex"], rosters[4]["players"]),
    ]
    return {
        "league_id": "league-1",
        "roster_id": 1,
        "season": 2026,
        "week": 1,
        "league": {"roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "BN", "BN", "BN", "BN", "BN"]},
        "lineup": {"team_name": "Me", "roster_slots": ["QB", "RB", "WR", "TE", "FLEX"]},
        "matchups": matchups,
        "users": users,
        "rosters": rosters,
        "players": {
            player_id: {
                "full_name": name,
                "position": position,
                "fantasy_positions": [position],
                "team": "FA",
                "status": "Active",
                "injury_status": "",
            }
            for player_id, (name, position, _) in points.items()
        },
        "projection_rows": [
            {"player_id": player_id, "points": value, "player": {"full_name": name, "position": position}}
            for player_id, (name, position, value) in points.items()
        ],
        "positions": ["QB", "RB", "WR", "TE"],
        "targets_per_team": 4,
        "offers_per_team": 3,
    }


def trade_matchup(roster_id: int, starters: list[str], players: list[str]) -> dict[str, object]:
    return {
        "roster_id": roster_id,
        "matchup_id": roster_id,
        "points": 0,
        "starters": starters,
        "players": players,
        "players_points": {},
    }
