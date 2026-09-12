from __future__ import annotations

from sleeper_tooling.player_values import build_player_value, build_player_values


def test_league_scoring_influences_player_value() -> None:
    player = {
        "full_name": "Reception Back",
        "team": "DEN",
        "position": "RB",
        "fantasy_positions": ["RB"],
    }
    projection = {
        "player_id": "rb1",
        "stats": {"rush_yd": 50, "rec": 6, "rec_yd": 30},
    }

    ppr_value = build_player_value(
        player_id="rb1",
        player=player,
        projection_row=projection,
        scoring_settings={"rush_yd": 0.1, "rec_yd": 0.1, "rec": 1},
        replacement_baselines={"RB": 8},
    )
    standard_value = build_player_value(
        player_id="rb1",
        player=player,
        projection_row=projection,
        scoring_settings={"rush_yd": 0.1, "rec_yd": 0.1},
        replacement_baselines={"RB": 8},
    )

    assert ppr_value["week_value"] == 14
    assert standard_value["week_value"] == 8
    assert ppr_value["decision_value"] > standard_value["decision_value"]
    assert ppr_value["scoring_source"] == "league_scoring"
    assert ppr_value["source_metadata"]["week_score"]["scoring_rules_matched"] == 3


def test_missing_projection_or_stat_context_degrades_gracefully() -> None:
    value = build_player_value(
        player_id="missing",
        player={"full_name": "Context Only", "team": "KC", "position": "WR"},
    )

    assert value["player_id"] == "missing"
    assert value["week_value"] == 0
    assert value["three_week_value"] == 0
    assert value["season_value"] == 0
    assert value["scoring_source"] == "missing"
    assert "week_projection_or_stat" in value["source_metadata"]["missing_context"]
    assert value["context_sources"] == ["sleeper_players"]


def test_questionable_player_receives_availability_adjustment() -> None:
    projection = {"player_id": "wr1", "points": 20}
    healthy = build_player_value(
        player_id="wr1",
        player={"full_name": "Healthy Wideout", "position": "WR", "status": "Active"},
        projection_row=projection,
    )
    questionable = build_player_value(
        player_id="wr1",
        player={
            "full_name": "Risky Wideout",
            "position": "WR",
            "status": "Active",
            "injury_status": "Questionable",
        },
        projection_row=projection,
    )

    assert questionable["week_value"] == 17
    assert questionable["three_week_value"] < healthy["three_week_value"]
    assert questionable["season_value"] < healthy["season_value"]
    assert questionable["role_tag"] == "injury_risk"
    assert questionable["source_metadata"]["availability"]["tag"] == "questionable"


def test_k_def_elite_hold_and_streamer_behavior_do_not_overrank_skill_depth() -> None:
    elite_def = build_player_value(
        player_id="def-elite",
        player={
            "full_name": "Elite Defense",
            "position": "DEF",
            "team": "PIT",
            "rostered_percent": 95,
        },
        projection_row={"player_id": "def-elite", "points": 9},
        recent_rows=[
            {"player_id": "def-elite", "points": 12},
            {"player_id": "def-elite", "points": 11},
        ],
        replacement_baselines={"DEF": 7},
    )
    streamer_k = build_player_value(
        player_id="k-stream",
        player={
            "full_name": "One Week Kicker",
            "position": "K",
            "team": "LV",
            "rostered_percent": 5,
        },
        projection_row={"player_id": "k-stream", "points": 12},
        replacement_baselines={"K": 7},
    )
    skill_depth = build_player_value(
        player_id="wr-depth",
        player={
            "full_name": "Multi Week Depth",
            "position": "WR",
            "team": "BUF",
            "rostered_percent": 35,
        },
        projection_row={"player_id": "wr-depth", "points": 9},
        recent_rows=[
            {"player_id": "wr-depth", "points": 9},
            {"player_id": "wr-depth", "points": 8},
        ],
        replacement_baselines={"WR": 8, "K": 7, "DEF": 7},
    )

    assert elite_def["value_tier"] == "elite_hold"
    assert elite_def["role_tag"] == "elite_hold"
    assert streamer_k["value_tier"] == "streamer"
    assert streamer_k["decision_value"] <= 8
    assert skill_depth["decision_value"] > streamer_k["decision_value"]


def test_value_above_replacement_shape_and_many_player_ranking() -> None:
    values = build_player_values(
        players={
            "high": {"full_name": "High RB", "position": "RB"},
            "low": {"full_name": "Low RB", "position": "RB"},
        },
        projection_rows=[
            {"player_id": "low", "points": 8},
            {"player_id": "high", "points": 13},
        ],
        replacement_baselines={"RB": {"week": 8, "three_week": 8.5, "season": 9}},
    )

    assert [row["player_id"] for row in values] == ["high", "low"]
    assert values[0]["replacement_value"] == {
        "week": 8.0,
        "three_week": 8.5,
        "season": 9.0,
        "decision": 8.62,
    }
    assert values[0]["value_above_replacement"] == {
        "week": 5.0,
        "three_week": 4.5,
        "season": 4.6,
        "decision": 4.65,
    }
