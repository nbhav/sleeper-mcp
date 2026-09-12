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
