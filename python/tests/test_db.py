from __future__ import annotations

import sqlite3

from sleeper_tooling.db import ApiResponseCache, SleeperNormalizedRepository


NORMALIZED_TABLES = {
    "players",
    "player_external_ids",
    "league_settings",
    "league_users",
    "rosters",
    "roster_players",
    "matchups",
    "matchup_players",
    "transactions",
    "player_week_rows",
    "player_week_stat_values",
    "player_week_scoring_values",
    "team_week_schedule",
    "player_role_snapshots",
    "player_week_availability",
    "sync_runs",
}


def test_normalized_schema_migrates_idempotently(tmp_path) -> None:
    db_path = tmp_path / "sleeper.db"
    first_repo = SleeperNormalizedRepository(db_path)
    first_repo.close()
    second_repo = SleeperNormalizedRepository(db_path)

    rows = second_repo._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    indexes = second_repo._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()

    assert NORMALIZED_TABLES.issubset({row["name"] for row in rows})
    assert {
        "idx_player_week_rows_player",
        "idx_player_external_ids_provider",
        "idx_player_week_stat_trends",
        "idx_matchups_league_week",
        "idx_roster_players_player",
        "idx_team_week_schedule_lookup",
        "idx_player_role_snapshots_lookup",
        "idx_player_week_availability_lookup",
        "idx_sync_runs_freshness",
    }.issubset({row["name"] for row in indexes})
    second_repo.close()


def test_upserts_use_stable_natural_keys_without_duplicates(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    players = {
        "1": {
            "first_name": "Original",
            "last_name": "Player",
            "full_name": "Original Player",
            "team": "DEN",
            "position": "RB",
            "fantasy_positions": ["RB"],
            "depth_chart_order": 2,
            "depth_chart_position": "RB",
        }
    }
    assert repo.upsert_players(players) == 1
    players["1"]["full_name"] = "Updated Player"
    assert repo.upsert_players(players) == 1

    assert repo.upsert_league_settings(
        {
            "league_id": "league-1",
            "name": "Test League",
            "season": "2026",
            "status": "in_season",
            "sport": "nfl",
            "scoring_settings": {"rec": 0.5},
            "roster_positions": ["QB", "RB"],
            "settings": {"playoff_week_start": 15},
        }
    ) == 1
    assert repo.upsert_league_settings(
        {
            "league_id": "league-1",
            "name": "Updated League",
            "season": "2026",
            "scoring_settings": {"rec": 1},
        }
    ) == 1

    assert repo.upsert_league_users(
        "league-1",
        [{"user_id": "user-1", "display_name": "One"}],
    ) == 1
    assert repo.upsert_league_users(
        "league-1",
        [{"user_id": "user-1", "display_name": "One Updated"}],
    ) == 1

    assert repo.upsert_rosters(
        "league-1",
        [
            {
                "roster_id": 1,
                "owner_id": "user-1",
                "players": ["1", "2"],
                "starters": ["1"],
                "reserve": ["2"],
            }
        ],
    ) == {"rosters": 1, "roster_players": 2}
    assert repo.upsert_rosters(
        "league-1",
        [
            {
                "roster_id": 1,
                "owner_id": "user-1",
                "players": ["1", "3"],
                "starters": ["3"],
            }
        ],
    ) == {"rosters": 1, "roster_players": 2}

    counts = {
        table: repo._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("players", "league_settings", "league_users", "rosters")
    }
    roster_players = repo.list_roster_players("league-1", roster_id=1)

    assert counts == {
        "players": 1,
        "league_settings": 1,
        "league_users": 1,
        "rosters": 1,
    }
    assert repo.get_player("1")["full_name"] == "Updated Player"
    assert repo.get_league_settings("league-1")["name"] == "Updated League"
    assert repo.list_league_users("league-1")[0]["display_name"] == "One Updated"
    assert [row["player_id"] for row in roster_players] == ["1", "3"]
    assert {row["player_id"]: row["slot_type"] for row in roster_players} == {
        "1": "bench",
        "3": "starter",
    }
    assert repo.get_player("1")["depth_chart_order"] == 2
    assert repo.get_player("1")["depth_chart_position"] == "RB"
    repo.close()


def test_player_external_ids_are_normalized_and_queryable(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    assert repo.upsert_players(
        {
            "1": {
                "full_name": "Linked Player",
                "espn_id": "12345",
                "rotowire_id": 67890,
                "yahoo_id": "",
                "sportradar_id": None,
            },
            "2": {
                "full_name": "Unlinked Player",
                "position": "RB",
            },
        }
    ) == 2

    linked_ids = repo.list_player_external_ids("1")
    unlinked_ids = repo.list_player_external_ids("2")

    assert {
        row["provider"]: (row["external_id"], row["source"])
        for row in linked_ids
    } == {
        "espn": ("12345", "sleeper_players"),
        "rotowire": ("67890", "sleeper_players"),
    }
    assert repo.get_player_external_id("1", "ESPN")["external_id"] == "12345"
    assert unlinked_ids == []
    repo.close()


def test_player_external_ids_update_and_remove_missing_ids(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    repo.upsert_players(
        {
            "1": {
                "full_name": "Changing Player",
                "espn_id": "old-espn",
                "yahoo_id": "old-yahoo",
            }
        }
    )
    repo.upsert_players(
        {
            "1": {
                "full_name": "Changing Player",
                "espn_id": "new-espn",
            }
        }
    )

    assert {
        row["provider"]: row["external_id"]
        for row in repo.list_player_external_ids("1")
    } == {"espn": "new-espn"}
    assert repo.get_player_external_id("1", "yahoo") is None
    repo.close()


def test_player_week_upsert_extracts_numeric_stats_and_scoring_values(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    result = repo.upsert_player_week_rows(
        season=2026,
        week=1,
        source="stats",
        rows=[
            {
                "player_id": "1",
                "player": {"full_name": "Runner One", "team": "DEN", "position": "RB"},
                "stats": {
                    "rush_yd": 85,
                    "rec": 4,
                    "new_unknown_usage_key": 2.5,
                    "weather": "snow",
                    "game_active": True,
                    "pts_ppr": 16.5,
                },
            }
        ],
        scoring_settings={"rush_yd": 0.1, "rec": 0.5, "new_unknown_usage_key": 1},
    )

    stat_values = repo.list_player_week_stat_values(
        season=2026,
        week=1,
        source="stats",
        player_id="1",
    )
    scoring_values = repo.list_player_week_scoring_values(
        season=2026,
        week=1,
        source="stats",
        player_id="1",
    )
    weekly_rows = repo.list_player_week_rows(season=2026, week=1, source="stats")

    assert result == {
        "player_week_rows": 1,
        "player_week_stat_values": 4,
        "player_week_scoring_values": 3,
    }
    assert {row["stat_key"]: row["stat_value"] for row in stat_values} == {
        "new_unknown_usage_key": 2.5,
        "pts_ppr": 16.5,
        "rec": 4,
        "rush_yd": 85,
    }
    assert {row["stat_key"]: row["points"] for row in scoring_values} == {
        "new_unknown_usage_key": 2.5,
        "rec": 2,
        "rush_yd": 8.5,
    }
    assert weekly_rows[0]["sleeper_points"] == 16.5
    assert weekly_rows[0]["fantasy_points"] == 13

    assert repo.upsert_player_week_rows(
        season=2026,
        week=1,
        source="stats",
        rows=[
            {
                "player_id": "1",
                "player": {"full_name": "Runner One", "team": "DEN", "position": "RB"},
                "stats": {
                    "rush_yd": 85,
                    "rec": 4,
                    "new_unknown_usage_key": 2.5,
                    "weather": "snow",
                    "game_active": True,
                    "pts_ppr": 16.5,
                },
            }
        ],
        scoring_settings={"rush_yd": 0.1, "rec": 0.5, "new_unknown_usage_key": 1},
    ) == result
    assert (
        repo._connection.execute("SELECT COUNT(*) FROM player_week_rows").fetchone()[0]
        == 1
    )
    assert (
        repo._connection.execute(
            "SELECT COUNT(*) FROM player_week_stat_values"
        ).fetchone()[0]
        == 4
    )
    assert (
        repo._connection.execute(
            "SELECT COUNT(*) FROM player_week_scoring_values"
        ).fetchone()[0]
        == 3
    )
    repo.close()


def test_player_week_raw_json_preserves_nonnumeric_stats(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    repo.upsert_player_week_rows(
        season=2026,
        week=2,
        source="projections",
        rows=[
            {
                "player_id": "2",
                "player": {"full_name": "Receiver Two"},
                "stats": {
                    "rec": 6,
                    "opponent": "KC",
                    "note": {"source": "manual"},
                },
            }
        ],
    )

    stat_values = repo.list_player_week_stat_values(
        season=2026,
        week=2,
        source="projections",
        player_id="2",
    )
    weekly_row = repo.list_player_week_rows(
        season=2026,
        week=2,
        source="projections",
        player_id="2",
    )[0]

    assert {row["stat_key"] for row in stat_values} == {"rec"}
    assert weekly_row["raw"]["stats"]["opponent"] == "KC"
    assert weekly_row["raw"]["stats"]["note"] == {"source": "manual"}
    repo.close()


def test_team_week_schedule_upsert_and_missing_status_reason(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    missing = repo.team_week_schedule_status(
        season=2026,
        week=1,
        teams=["DEN"],
    )

    assert missing == {
        "season": 2026,
        "week": 1,
        "evaluable": False,
        "available_teams": [],
        "missing_teams": ["DEN"],
        "reason": "not_evaluable_missing_nfl_schedule",
    }

    assert repo.upsert_team_week_schedule(
        season=2026,
        week=1,
        source="manual_fixture",
        rows=[
            {
                "team": "den",
                "opponent": "kc",
                "home_away": "H",
                "game_timestamp": "1780000000",
            }
        ],
    ) == 1

    schedule = repo.get_team_week_schedule(season=2026, week=1, team="DEN")
    present = repo.team_week_schedule_status(
        season=2026,
        week=1,
        teams=["DEN"],
    )

    assert schedule["team"] == "DEN"
    assert schedule["opponent"] == "KC"
    assert schedule["home_away"] == "home"
    assert schedule["game_timestamp"] == 1780000000
    assert present == {
        "season": 2026,
        "week": 1,
        "evaluable": True,
        "available_teams": ["DEN"],
        "missing_teams": [],
    }
    repo.close()


def test_schedule_source_precedence_filtering_and_source_replacement(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")
    repo.upsert_team_week_schedule(
        season=2026,
        week=1,
        source="local_db",
        rows=[{"team": "DEN", "opponent": "LV"}],
    )
    repo.upsert_team_week_schedule(
        season=2026,
        week=1,
        source="manual_fixture",
        rows=[{"team": "DEN", "opponent": "KC"}],
    )
    repo.upsert_team_week_schedule(
        season=2026,
        week=1,
        source="sleeper_nfl_schedule",
        rows=[{"team": "DEN", "opponent": "LAC"}],
    )

    assert repo.get_team_week_schedule(season=2026, week=1, team="DEN")["opponent"] == "LAC"
    assert repo.get_team_week_schedule(
        season=2026, week=1, team="DEN", source="manual_fixture"
    )["opponent"] == "KC"

    repo.replace_team_week_schedule_source(
        season=2026,
        source="manual_fixture",
        rows=[{"week": 2, "team": "DEN", "opponent": "BUF"}],
    )
    assert repo.get_team_week_schedule(
        season=2026, week=1, team="DEN", source="manual_fixture"
    ) is None
    assert repo.get_team_week_schedule(
        season=2026, week=2, team="DEN", source="manual_fixture"
    )["opponent"] == "BUF"
    assert repo.get_team_week_schedule(season=2026, week=1, team="DEN")["opponent"] == "LAC"
    repo.close()


def test_legacy_team_schedule_context_migrates_without_overwriting_normalized_rows(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE team_schedule_context (
            season INTEGER NOT NULL,
            team TEXT NOT NULL,
            bye_week INTEGER,
            schedule_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'local_db',
            updated_at REAL NOT NULL,
            PRIMARY KEY (season, team)
        )
        """
    )
    connection.execute(
        "INSERT INTO team_schedule_context VALUES (?, ?, ?, ?, ?, ?)",
        (2026, "den", 8, '{"1": {"opponent": "KC", "home_away": "H"}}', "local_db", 12),
    )
    connection.commit()
    connection.close()

    repo = SleeperNormalizedRepository(db_path)
    migrated = repo.get_team_week_schedule(season=2026, week=1, team="DEN")
    bye = repo.get_team_week_schedule(season=2026, week=8, team="DEN")
    assert migrated["opponent"] == "KC"
    assert migrated["home_away"] == "home"
    assert bye["is_bye"] == 1
    assert repo._connection.execute(
        "SELECT COUNT(*) FROM team_schedule_context"
    ).fetchone()[0] == 1

    repo.upsert_team_week_schedule(
        season=2026,
        week=1,
        source="local_db",
        rows=[{"team": "DEN", "opponent": "LAC"}],
    )
    repo.close()
    reopened = SleeperNormalizedRepository(db_path)
    assert reopened.get_team_week_schedule(season=2026, week=1, team="DEN")["opponent"] == "LAC"
    reopened.close()


def test_role_and_availability_snapshots_preserve_current_metadata_flag(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    counts = repo.upsert_current_player_metadata_snapshots(
        season=2026,
        week=3,
        snapshot_at=1234,
        players={
            "1": {
                "full_name": "Current Back",
                "team": "den",
                "status": "Active",
                "injury_status": "Questionable",
                "depth_chart_order": "2",
                "depth_chart_position": "RB",
            },
            "2": {
                "full_name": "Missing Team",
                "position": "WR",
            },
        },
    )

    role_rows = repo.list_player_role_snapshots(
        season=2026,
        week=3,
        team="DEN",
    )
    availability_rows = repo.list_player_week_availability(
        season=2026,
        week=3,
        player_id="1",
    )

    assert counts == {
        "player_role_snapshots": 1,
        "player_week_availability": 1,
    }
    assert role_rows[0]["player_id"] == "1"
    assert role_rows[0]["depth_chart_order"] == 2
    assert role_rows[0]["projected_role_label"] == "RB"
    assert role_rows[0]["current_metadata_only"] == 1
    assert role_rows[0]["source"] == "sleeper_players_current_metadata"
    assert role_rows[0]["snapshot_at"] == 1234
    assert availability_rows[0]["status"] == "Active"
    assert availability_rows[0]["injury_status"] == "Questionable"
    assert availability_rows[0]["reserve_tags"] == []
    repo.close()


def test_role_and_availability_snapshot_upserts_are_keyed_by_week_team_player_source(
    tmp_path,
) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    role_rows = [
        {
            "team": "KC",
            "player_id": "10",
            "depth_chart_order": 1,
            "projected_role_label": "lead_back",
            "status": "Active",
            "injury_status": None,
        }
    ]
    availability_rows = [
        {
            "team": "KC",
            "player_id": "10",
            "status": "Active",
            "injury_status": None,
            "reserve_tags": ["IR"],
        }
    ]

    assert repo.upsert_player_role_snapshots(
        season=2026,
        week=4,
        source="manual_depth_chart",
        snapshot_at=2000,
        rows=role_rows,
    ) == 1
    role_rows[0]["projected_role_label"] = "committee_back"
    assert repo.upsert_player_role_snapshots(
        season=2026,
        week=4,
        source="manual_depth_chart",
        snapshot_at=2001,
        rows=role_rows,
    ) == 1
    assert repo.upsert_player_week_availability(
        season=2026,
        week=4,
        source="manual_injury_report",
        snapshot_at=2000,
        rows=availability_rows,
    ) == 1

    roles = repo.list_player_role_snapshots(season=2026, week=4, player_id="10")
    availability = repo.list_player_week_availability(
        season=2026,
        week=4,
        team="KC",
    )

    assert len(roles) == 1
    assert roles[0]["projected_role_label"] == "committee_back"
    assert roles[0]["current_metadata_only"] == 0
    assert len(availability) == 1
    assert availability[0]["reserve_tags"] == ["IR"]
    assert (
        repo._connection.execute("SELECT COUNT(*) FROM player_role_snapshots").fetchone()[
            0
        ]
        == 1
    )
    repo.close()


def test_api_response_cache_remains_scoped_to_raw_cache_rows(tmp_path) -> None:
    db_path = tmp_path / "sleeper.db"
    repo = SleeperNormalizedRepository(db_path)
    repo.upsert_player_week_rows(
        season=2026,
        week=1,
        source="stats",
        rows=[{"player_id": "1", "stats": {"rec": 1}}],
    )
    repo.close()

    cache = ApiResponseCache(db_path)
    cache.set("fresh", url="https://example.com/fresh", response={"ok": True})

    assert cache.get("fresh") == {"ok": True}
    assert cache.stats()["total_entries"] == 1
    assert cache.clear() == 1
    assert cache.stats()["total_entries"] == 0
    cache.close()

    connection = sqlite3.connect(db_path)
    try:
        normalized_count = connection.execute(
            "SELECT COUNT(*) FROM player_week_rows"
        ).fetchone()[0]
    finally:
        connection.close()
    assert normalized_count == 1
