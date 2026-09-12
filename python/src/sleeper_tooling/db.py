from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sleeper_tooling.scoring import calculate_fantasy_points


JsonObject = Mapping[str, Any]

PLAYER_EXTERNAL_ID_SOURCE = "sleeper_players"

PLAYER_EXTERNAL_ID_FIELDS = {
    "espn_id": "espn",
    "fantasy_data_id": "fantasydata",
    "gsis_id": "gsis",
    "nfl_id": "nfl",
    "pff_id": "pff",
    "pfr_id": "pro-football-reference",
    "rotowire_id": "rotowire",
    "rotoworld_id": "rotoworld",
    "sportradar_id": "sportradar",
    "stats_id": "stats",
    "swish_id": "swish",
    "yahoo_id": "yahoo",
}


class ApiResponseCache:
    def __init__(self, db_path: Path, *, default_ttl_seconds: int = 900) -> None:
        self.db_path = db_path
        self.default_ttl_seconds = default_ttl_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._configure_connection()
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def get(self, cache_key: str) -> Any | None:
        row = self._connection.execute(
            """
            SELECT response_json, fetched_at, ttl_seconds
            FROM api_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if time.time() - float(row["fetched_at"]) > int(row["ttl_seconds"]):
            return None
        return json.loads(row["response_json"])

    def set(
        self,
        cache_key: str,
        *,
        url: str,
        response: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO api_cache (
                cache_key,
                url,
                response_json,
                fetched_at,
                ttl_seconds
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                url = excluded.url,
                response_json = excluded.response_json,
                fetched_at = excluded.fetched_at,
                ttl_seconds = excluded.ttl_seconds
            """,
            (
                cache_key,
                url,
                json.dumps(response, sort_keys=True),
                time.time(),
                ttl_seconds or self.default_ttl_seconds,
            ),
        )
        self._connection.commit()

    def clear(self) -> int:
        cursor = self._connection.execute("DELETE FROM api_cache")
        self._connection.commit()
        return cursor.rowcount

    def clear_expired(self) -> int:
        cursor = self._connection.execute(
            """
            DELETE FROM api_cache
            WHERE ? - fetched_at > ttl_seconds
            """,
            (time.time(),),
        )
        self._connection.commit()
        return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        row = self._connection.execute(
            """
            SELECT
                COUNT(*) AS total_entries,
                SUM(CASE WHEN ? - fetched_at <= ttl_seconds THEN 1 ELSE 0 END)
                    AS fresh_entries,
                SUM(CASE WHEN ? - fetched_at > ttl_seconds THEN 1 ELSE 0 END)
                    AS expired_entries,
                MIN(fetched_at) AS oldest_fetched_at,
                MAX(fetched_at) AS newest_fetched_at
            FROM api_cache
            """,
            (time.time(), time.time()),
        ).fetchone()
        return {
            "db_path": str(self.db_path),
            "total_entries": int(row["total_entries"] or 0),
            "fresh_entries": int(row["fresh_entries"] or 0),
            "expired_entries": int(row["expired_entries"] or 0),
            "oldest_fetched_at": row["oldest_fetched_at"],
            "newest_fetched_at": row["newest_fetched_at"],
        }

    def _migrate(self) -> None:
        migrate_sqlite_schema(self._connection)

    def _configure_connection(self) -> None:
        configure_sqlite_connection(self._connection)


class SleeperNormalizedRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, timeout=30)
        self._connection.row_factory = sqlite3.Row
        configure_sqlite_connection(self._connection)
        migrate_sqlite_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    def upsert_players(
        self,
        players: Mapping[str, JsonObject] | Iterable[JsonObject],
    ) -> int:
        updated_at = time.time()
        count = 0
        for player_id, player in _iter_player_map(players):
            self._connection.execute(
                """
                INSERT INTO players (
                    player_id,
                    full_name,
                    first_name,
                    last_name,
                    team,
                    position,
                    fantasy_positions_json,
                    status,
                    injury_status,
                    depth_chart_order,
                    depth_chart_position,
                    raw_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    team = excluded.team,
                    position = excluded.position,
                    fantasy_positions_json = excluded.fantasy_positions_json,
                    status = excluded.status,
                    injury_status = excluded.injury_status,
                    depth_chart_order = excluded.depth_chart_order,
                    depth_chart_position = excluded.depth_chart_position,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    player_id,
                    _text(player.get("full_name")),
                    _text(player.get("first_name")),
                    _text(player.get("last_name")),
                    _text(player.get("team")),
                    _text(player.get("position")),
                    _json_dumps(player.get("fantasy_positions") or []),
                    _text(player.get("status")),
                    _text(player.get("injury_status")),
                    _int_or_none(player.get("depth_chart_order")),
                    _text(player.get("depth_chart_position")),
                    _json_dumps(player),
                    updated_at,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM player_external_ids
                WHERE player_id = ? AND source = ?
                """,
                (player_id, PLAYER_EXTERNAL_ID_SOURCE),
            )
            for provider, external_id in _player_external_ids(player):
                self._connection.execute(
                    """
                    INSERT INTO player_external_ids (
                        player_id,
                        provider,
                        external_id,
                        source,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(player_id, provider, source) DO UPDATE SET
                        external_id = excluded.external_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        player_id,
                        provider,
                        external_id,
                        PLAYER_EXTERNAL_ID_SOURCE,
                        updated_at,
                    ),
                )
            count += 1
        self._connection.commit()
        return count

    def get_player(self, player_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM players WHERE player_id = ?",
            (str(player_id),),
        ).fetchone()
        return _decode_row(row)

    def list_player_external_ids(
        self,
        player_id: str,
        *,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(player_id)]
        where = "player_id = ?"
        if provider is not None:
            where += " AND provider = ?"
            params.append(_normalize_provider(provider))
        rows = self._connection.execute(
            f"""
            SELECT * FROM player_external_ids
            WHERE {where}
            ORDER BY provider, source
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def get_player_external_id(
        self,
        player_id: str,
        provider: str,
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT * FROM player_external_ids
            WHERE player_id = ? AND provider = ?
            ORDER BY source
            LIMIT 1
            """,
            (str(player_id), _normalize_provider(provider)),
        ).fetchone()
        return _decode_row(row)

    def upsert_league_settings(self, league: JsonObject) -> int:
        league_id = _required_text(league, "league_id")
        self._connection.execute(
            """
            INSERT INTO league_settings (
                league_id,
                name,
                season,
                status,
                sport,
                scoring_settings_json,
                roster_positions_json,
                settings_json,
                raw_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(league_id) DO UPDATE SET
                name = excluded.name,
                season = excluded.season,
                status = excluded.status,
                sport = excluded.sport,
                scoring_settings_json = excluded.scoring_settings_json,
                roster_positions_json = excluded.roster_positions_json,
                settings_json = excluded.settings_json,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                league_id,
                _text(league.get("name")),
                _int_or_none(league.get("season")),
                _text(league.get("status")),
                _text(league.get("sport")),
                _json_dumps(league.get("scoring_settings") or {}),
                _json_dumps(league.get("roster_positions") or []),
                _json_dumps(league.get("settings") or {}),
                _json_dumps(league),
                time.time(),
            ),
        )
        self._connection.commit()
        return 1

    def get_league_settings(self, league_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM league_settings WHERE league_id = ?",
            (str(league_id),),
        ).fetchone()
        return _decode_row(row)

    def upsert_league_users(
        self,
        league_id: str,
        users: Iterable[JsonObject],
    ) -> int:
        updated_at = time.time()
        count = 0
        for user in users:
            user_id = _required_text(user, "user_id")
            self._connection.execute(
                """
                INSERT INTO league_users (
                    league_id,
                    user_id,
                    username,
                    display_name,
                    avatar,
                    metadata_json,
                    raw_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(league_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    avatar = excluded.avatar,
                    metadata_json = excluded.metadata_json,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(league_id),
                    user_id,
                    _text(user.get("username")),
                    _text(user.get("display_name")),
                    _text(user.get("avatar")),
                    _json_dumps(user.get("metadata") or {}),
                    _json_dumps(user),
                    updated_at,
                ),
            )
            count += 1
        self._connection.commit()
        return count

    def list_league_users(self, league_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT * FROM league_users
            WHERE league_id = ?
            ORDER BY display_name, username, user_id
            """,
            (str(league_id),),
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def upsert_rosters(
        self,
        league_id: str,
        rosters: Iterable[JsonObject],
    ) -> dict[str, int]:
        updated_at = time.time()
        roster_count = 0
        roster_player_count = 0
        for roster in rosters:
            roster_id = _required_text(roster, "roster_id")
            self._connection.execute(
                """
                INSERT INTO rosters (
                    league_id,
                    roster_id,
                    owner_id,
                    co_owners_json,
                    starters_json,
                    players_json,
                    reserve_json,
                    taxi_json,
                    settings_json,
                    metadata_json,
                    raw_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(league_id, roster_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    co_owners_json = excluded.co_owners_json,
                    starters_json = excluded.starters_json,
                    players_json = excluded.players_json,
                    reserve_json = excluded.reserve_json,
                    taxi_json = excluded.taxi_json,
                    settings_json = excluded.settings_json,
                    metadata_json = excluded.metadata_json,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(league_id),
                    roster_id,
                    _text(roster.get("owner_id")),
                    _json_dumps(roster.get("co_owners") or []),
                    _json_dumps(roster.get("starters") or []),
                    _json_dumps(roster.get("players") or []),
                    _json_dumps(roster.get("reserve") or []),
                    _json_dumps(roster.get("taxi") or []),
                    _json_dumps(roster.get("settings") or {}),
                    _json_dumps(roster.get("metadata") or {}),
                    _json_dumps(roster),
                    updated_at,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM roster_players
                WHERE league_id = ? AND roster_id = ?
                """,
                (str(league_id), roster_id),
            )
            starters = [str(player_id) for player_id in roster.get("starters") or []]
            reserve = {str(player_id) for player_id in roster.get("reserve") or []}
            taxi = {str(player_id) for player_id in roster.get("taxi") or []}
            for player_id in _ordered_unique(roster.get("players") or [], starters):
                player_id = str(player_id)
                slot_type = "bench"
                slot_index: int | None = None
                if player_id in starters:
                    slot_type = "starter"
                    slot_index = starters.index(player_id)
                elif player_id in reserve:
                    slot_type = "reserve"
                elif player_id in taxi:
                    slot_type = "taxi"
                self._connection.execute(
                    """
                    INSERT INTO roster_players (
                        league_id,
                        roster_id,
                        player_id,
                        slot_type,
                        slot_index,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(league_id, roster_id, player_id) DO UPDATE SET
                        slot_type = excluded.slot_type,
                        slot_index = excluded.slot_index,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(league_id),
                        roster_id,
                        player_id,
                        slot_type,
                        slot_index,
                        updated_at,
                    ),
                )
                roster_player_count += 1
            roster_count += 1
        self._connection.commit()
        return {"rosters": roster_count, "roster_players": roster_player_count}

    def list_rosters(self, league_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT * FROM rosters
            WHERE league_id = ?
            ORDER BY CAST(roster_id AS INTEGER), roster_id
            """,
            (str(league_id),),
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def list_roster_players(
        self,
        league_id: str,
        roster_id: str | int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(league_id)]
        where = "league_id = ?"
        if roster_id is not None:
            where += " AND roster_id = ?"
            params.append(str(roster_id))
        rows = self._connection.execute(
            f"""
            SELECT * FROM roster_players
            WHERE {where}
            ORDER BY CAST(roster_id AS INTEGER), roster_id, slot_index, player_id
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def upsert_matchups(
        self,
        league_id: str,
        season: int,
        week: int,
        matchups: Iterable[JsonObject],
    ) -> dict[str, int]:
        updated_at = time.time()
        matchup_count = 0
        matchup_player_count = 0
        for matchup in matchups:
            roster_id = _required_text(matchup, "roster_id")
            self._connection.execute(
                """
                INSERT INTO matchups (
                    league_id,
                    season,
                    week,
                    roster_id,
                    matchup_id,
                    points,
                    starters_json,
                    players_json,
                    players_points_json,
                    custom_points_json,
                    raw_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(league_id, season, week, roster_id) DO UPDATE SET
                    matchup_id = excluded.matchup_id,
                    points = excluded.points,
                    starters_json = excluded.starters_json,
                    players_json = excluded.players_json,
                    players_points_json = excluded.players_points_json,
                    custom_points_json = excluded.custom_points_json,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(league_id),
                    int(season),
                    int(week),
                    roster_id,
                    _int_or_none(matchup.get("matchup_id")),
                    _number_or_none(matchup.get("points")),
                    _json_dumps(matchup.get("starters") or []),
                    _json_dumps(matchup.get("players") or []),
                    _json_dumps(matchup.get("players_points") or {}),
                    _json_dumps(matchup.get("custom_points") or {}),
                    _json_dumps(matchup),
                    updated_at,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM matchup_players
                WHERE league_id = ? AND season = ? AND week = ? AND roster_id = ?
                """,
                (str(league_id), int(season), int(week), roster_id),
            )
            starters = [str(player_id) for player_id in matchup.get("starters") or []]
            players_points = matchup.get("players_points") or {}
            for player_id in _ordered_unique(
                matchup.get("players") or [],
                starters,
                players_points.keys(),
            ):
                player_id = str(player_id)
                slot_index = starters.index(player_id) if player_id in starters else None
                self._connection.execute(
                    """
                    INSERT INTO matchup_players (
                        league_id,
                        season,
                        week,
                        roster_id,
                        player_id,
                        is_starter,
                        slot_index,
                        points,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        league_id,
                        season,
                        week,
                        roster_id,
                        player_id
                    ) DO UPDATE SET
                        is_starter = excluded.is_starter,
                        slot_index = excluded.slot_index,
                        points = excluded.points,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(league_id),
                        int(season),
                        int(week),
                        roster_id,
                        player_id,
                        int(player_id in starters),
                        slot_index,
                        _number_or_none(players_points.get(player_id)),
                        updated_at,
                    ),
                )
                matchup_player_count += 1
            matchup_count += 1
        self._connection.commit()
        return {"matchups": matchup_count, "matchup_players": matchup_player_count}

    def list_matchups(
        self,
        league_id: str,
        season: int,
        week: int,
    ) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT * FROM matchups
            WHERE league_id = ? AND season = ? AND week = ?
            ORDER BY matchup_id, CAST(roster_id AS INTEGER), roster_id
            """,
            (str(league_id), int(season), int(week)),
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def upsert_transactions(
        self,
        league_id: str,
        transactions: Iterable[JsonObject],
        *,
        week: int | None = None,
    ) -> int:
        updated_at = time.time()
        count = 0
        for transaction in transactions:
            transaction_id = _required_text(transaction, "transaction_id")
            self._connection.execute(
                """
                INSERT INTO transactions (
                    league_id,
                    transaction_id,
                    week,
                    type,
                    status,
                    status_updated,
                    created,
                    roster_ids_json,
                    adds_json,
                    drops_json,
                    draft_picks_json,
                    waiver_budget_json,
                    raw_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(league_id, transaction_id) DO UPDATE SET
                    week = excluded.week,
                    type = excluded.type,
                    status = excluded.status,
                    status_updated = excluded.status_updated,
                    created = excluded.created,
                    roster_ids_json = excluded.roster_ids_json,
                    adds_json = excluded.adds_json,
                    drops_json = excluded.drops_json,
                    draft_picks_json = excluded.draft_picks_json,
                    waiver_budget_json = excluded.waiver_budget_json,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(league_id),
                    transaction_id,
                    _int_or_none(transaction.get("leg") or transaction.get("week") or week),
                    _text(transaction.get("type")),
                    _text(transaction.get("status")),
                    _int_or_none(transaction.get("status_updated")),
                    _int_or_none(transaction.get("created")),
                    _json_dumps(transaction.get("roster_ids") or []),
                    _json_dumps(transaction.get("adds") or {}),
                    _json_dumps(transaction.get("drops") or {}),
                    _json_dumps(transaction.get("draft_picks") or []),
                    _json_dumps(transaction.get("waiver_budget") or []),
                    _json_dumps(transaction),
                    updated_at,
                ),
            )
            count += 1
        self._connection.commit()
        return count

    def list_transactions(
        self,
        league_id: str,
        *,
        week: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(league_id)]
        where = "league_id = ?"
        if week is not None:
            where += " AND week = ?"
            params.append(int(week))
        rows = self._connection.execute(
            f"""
            SELECT * FROM transactions
            WHERE {where}
            ORDER BY created DESC, transaction_id
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def upsert_player_week_rows(
        self,
        *,
        season: int,
        week: int,
        source: str,
        rows: Mapping[str, JsonObject] | Iterable[JsonObject],
        scoring_settings: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        updated_at = time.time()
        row_count = 0
        stat_value_count = 0
        scoring_value_count = 0
        for player_id, row in _iter_player_week_payloads(rows):
            stats = row.get("stats") if isinstance(row.get("stats"), Mapping) else {}
            player = row.get("player") if isinstance(row.get("player"), Mapping) else {}
            sleeper_points = _sleeper_points(row, stats)
            fantasy_points: float | None = None
            scoring_values: Mapping[str, Any] = {}
            if scoring_settings is not None:
                fantasy_points, scoring_values = calculate_fantasy_points(
                    dict(stats),
                    dict(scoring_settings),
                )
            elif isinstance(row.get("scoring_breakdown"), Mapping):
                scoring_values = row["scoring_breakdown"]
                fantasy_points = _number_or_none(
                    row.get("fantasy_points", row.get("points"))
                )
            self._connection.execute(
                """
                INSERT INTO player_week_rows (
                    source,
                    season,
                    week,
                    player_id,
                    player_name,
                    team,
                    position,
                    player_json,
                    raw_json,
                    sleeper_points,
                    fantasy_points,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, season, week, player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    team = excluded.team,
                    position = excluded.position,
                    player_json = excluded.player_json,
                    raw_json = excluded.raw_json,
                    sleeper_points = excluded.sleeper_points,
                    fantasy_points = excluded.fantasy_points,
                    updated_at = excluded.updated_at
                """,
                (
                    str(source),
                    int(season),
                    int(week),
                    player_id,
                    _player_name(row, player),
                    _text(row.get("team") or player.get("team")),
                    _text(row.get("position") or player.get("position")),
                    _json_dumps(player),
                    _json_dumps(row),
                    sleeper_points,
                    fantasy_points,
                    updated_at,
                    updated_at,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM player_week_stat_values
                WHERE source = ? AND season = ? AND week = ? AND player_id = ?
                """,
                (str(source), int(season), int(week), player_id),
            )
            for stat_key, stat_value in _numeric_items(stats):
                self._connection.execute(
                    """
                    INSERT INTO player_week_stat_values (
                        source,
                        season,
                        week,
                        player_id,
                        stat_key,
                        stat_value,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        source,
                        season,
                        week,
                        player_id,
                        stat_key
                    ) DO UPDATE SET
                        stat_value = excluded.stat_value,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(source),
                        int(season),
                        int(week),
                        player_id,
                        stat_key,
                        stat_value,
                        updated_at,
                    ),
                )
                stat_value_count += 1
            self._connection.execute(
                """
                DELETE FROM player_week_scoring_values
                WHERE source = ? AND season = ? AND week = ? AND player_id = ?
                """,
                (str(source), int(season), int(week), player_id),
            )
            for stat_key, points in _numeric_items(scoring_values):
                self._connection.execute(
                    """
                    INSERT INTO player_week_scoring_values (
                        source,
                        season,
                        week,
                        player_id,
                        stat_key,
                        points,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        source,
                        season,
                        week,
                        player_id,
                        stat_key
                    ) DO UPDATE SET
                        points = excluded.points,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(source),
                        int(season),
                        int(week),
                        player_id,
                        stat_key,
                        points,
                        updated_at,
                    ),
                )
                scoring_value_count += 1
            row_count += 1
        self._connection.commit()
        return {
            "player_week_rows": row_count,
            "player_week_stat_values": stat_value_count,
            "player_week_scoring_values": scoring_value_count,
        }

    def list_player_week_rows(
        self,
        *,
        season: int,
        week: int,
        source: str,
        player_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(source), int(season), int(week)]
        where = "source = ? AND season = ? AND week = ?"
        if player_id is not None:
            where += " AND player_id = ?"
            params.append(str(player_id))
        rows = self._connection.execute(
            f"""
            SELECT * FROM player_week_rows
            WHERE {where}
            ORDER BY fantasy_points DESC, sleeper_points DESC, player_id
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def list_player_week_stat_values(
        self,
        *,
        season: int,
        week: int,
        source: str,
        player_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(source), int(season), int(week)]
        where = "source = ? AND season = ? AND week = ?"
        if player_id is not None:
            where += " AND player_id = ?"
            params.append(str(player_id))
        rows = self._connection.execute(
            f"""
            SELECT * FROM player_week_stat_values
            WHERE {where}
            ORDER BY player_id, stat_key
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def list_player_week_scoring_values(
        self,
        *,
        season: int,
        week: int,
        source: str,
        player_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(source), int(season), int(week)]
        where = "source = ? AND season = ? AND week = ?"
        if player_id is not None:
            where += " AND player_id = ?"
            params.append(str(player_id))
        rows = self._connection.execute(
            f"""
            SELECT * FROM player_week_scoring_values
            WHERE {where}
            ORDER BY player_id, stat_key
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows if row is not None]

    def create_sync_run(
        self,
        *,
        target: str,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        source: str | None = None,
        status: str = "running",
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO sync_runs (
                target,
                league_id,
                season,
                week,
                source,
                status,
                started_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target,
                league_id,
                season,
                week,
                source,
                status,
                time.time(),
                _json_dumps(metadata or {}),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        sync_run_id: int,
        *,
        status: str,
        row_counts: Mapping[str, int] | None = None,
        error_text: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            UPDATE sync_runs
            SET status = ?,
                finished_at = ?,
                row_counts_json = ?,
                error_text = ?
            WHERE sync_run_id = ?
            """,
            (
                status,
                time.time(),
                _json_dumps(row_counts or {}),
                error_text,
                int(sync_run_id),
            ),
        )
        self._connection.commit()

    def latest_sync_run(
        self,
        *,
        target: str | None = None,
        league_id: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = []
        where_clauses: list[str] = []
        if target is not None:
            where_clauses.append("target = ?")
            params.append(target)
        if league_id is not None:
            where_clauses.append("league_id = ?")
            params.append(league_id)
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        row = self._connection.execute(
            f"""
            SELECT * FROM sync_runs
            {where}
            ORDER BY started_at DESC, sync_run_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return _decode_row(row)


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")


def migrate_sqlite_schema(connection: sqlite3.Connection) -> None:
    _migrate_api_cache_schema(connection)
    _migrate_normalized_schema(connection)
    connection.commit()


def _migrate_api_cache_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS api_cache (
            cache_key TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            response_json TEXT NOT NULL,
            fetched_at REAL NOT NULL,
            ttl_seconds INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_api_cache_fetched_at
        ON api_cache(fetched_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_context_overrides (
            player_id TEXT PRIMARY KEY,
            context_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'local_db',
            updated_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS team_schedule_context (
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
        """
        CREATE TABLE IF NOT EXISTS context_source_timestamps (
            source TEXT PRIMARY KEY,
            fetched_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def _migrate_normalized_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            full_name TEXT,
            first_name TEXT,
            last_name TEXT,
            team TEXT,
            position TEXT,
            fantasy_positions_json TEXT NOT NULL DEFAULT '[]',
            status TEXT,
            injury_status TEXT,
            depth_chart_order INTEGER,
            depth_chart_position TEXT,
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    _ensure_columns(
        connection,
        "players",
        {
            "depth_chart_order": "INTEGER",
            "depth_chart_position": "TEXT",
        },
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_external_ids (
            player_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (player_id, provider, source)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS league_settings (
            league_id TEXT PRIMARY KEY,
            name TEXT,
            season INTEGER,
            status TEXT,
            sport TEXT,
            scoring_settings_json TEXT NOT NULL DEFAULT '{}',
            roster_positions_json TEXT NOT NULL DEFAULT '[]',
            settings_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS league_users (
            league_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT,
            display_name TEXT,
            avatar TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS rosters (
            league_id TEXT NOT NULL,
            roster_id TEXT NOT NULL,
            owner_id TEXT,
            co_owners_json TEXT NOT NULL DEFAULT '[]',
            starters_json TEXT NOT NULL DEFAULT '[]',
            players_json TEXT NOT NULL DEFAULT '[]',
            reserve_json TEXT NOT NULL DEFAULT '[]',
            taxi_json TEXT NOT NULL DEFAULT '[]',
            settings_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, roster_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS roster_players (
            league_id TEXT NOT NULL,
            roster_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            slot_type TEXT NOT NULL,
            slot_index INTEGER,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, roster_id, player_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS matchups (
            league_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            roster_id TEXT NOT NULL,
            matchup_id INTEGER,
            points REAL,
            starters_json TEXT NOT NULL DEFAULT '[]',
            players_json TEXT NOT NULL DEFAULT '[]',
            players_points_json TEXT NOT NULL DEFAULT '{}',
            custom_points_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, season, week, roster_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS matchup_players (
            league_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            roster_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            is_starter INTEGER NOT NULL DEFAULT 0,
            slot_index INTEGER,
            points REAL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, season, week, roster_id, player_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            league_id TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            week INTEGER,
            type TEXT,
            status TEXT,
            status_updated INTEGER,
            created INTEGER,
            roster_ids_json TEXT NOT NULL DEFAULT '[]',
            adds_json TEXT NOT NULL DEFAULT '{}',
            drops_json TEXT NOT NULL DEFAULT '{}',
            draft_picks_json TEXT NOT NULL DEFAULT '[]',
            waiver_budget_json TEXT NOT NULL DEFAULT '[]',
            raw_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (league_id, transaction_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_week_rows (
            source TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            player_name TEXT,
            team TEXT,
            position TEXT,
            player_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL,
            sleeper_points REAL,
            fantasy_points REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (source, season, week, player_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_week_stat_values (
            source TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (source, season, week, player_id, stat_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_week_scoring_values (
            source TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            stat_key TEXT NOT NULL,
            points REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (source, season, week, player_id, stat_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            sync_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            league_id TEXT,
            season INTEGER,
            week INTEGER,
            source TEXT,
            status TEXT NOT NULL,
            started_at REAL NOT NULL,
            finished_at REAL,
            row_counts_json TEXT NOT NULL DEFAULT '{}',
            error_text TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    _create_normalized_indexes(connection)


def _create_normalized_indexes(connection: sqlite3.Connection) -> None:
    statements = [
        """
        CREATE INDEX IF NOT EXISTS idx_players_team_position
        ON players(team, position)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_external_ids_provider
        ON player_external_ids(provider, external_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_league_users_display_name
        ON league_users(league_id, display_name)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_rosters_owner
        ON rosters(league_id, owner_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_roster_players_player
        ON roster_players(league_id, player_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_matchups_league_week
        ON matchups(league_id, season, week, matchup_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_matchup_players_player_week
        ON matchup_players(player_id, season, week)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_league_week
        ON transactions(league_id, week, created)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_week_rows_week
        ON player_week_rows(source, season, week, position)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_week_rows_player
        ON player_week_rows(player_id, source, season, week)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_week_stat_trends
        ON player_week_stat_values(player_id, stat_key, source, season, week)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_week_stat_leaders
        ON player_week_stat_values(source, season, week, stat_key, stat_value)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_player_week_scoring_trends
        ON player_week_scoring_values(player_id, stat_key, source, season, week)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sync_runs_freshness
        ON sync_runs(target, league_id, source, status, started_at)
        """,
    ]
    for statement in statements:
        connection.execute(statement)


def _iter_player_map(
    players: Mapping[str, JsonObject] | Iterable[JsonObject],
) -> Iterable[tuple[str, JsonObject]]:
    if isinstance(players, Mapping):
        for player_id, player in players.items():
            if not isinstance(player, Mapping):
                continue
            yield str(player.get("player_id") or player_id), player
        return
    for player in players:
        yield _required_text(player, "player_id"), player


def _iter_player_week_payloads(
    rows: Mapping[str, JsonObject] | Iterable[JsonObject],
) -> Iterable[tuple[str, JsonObject]]:
    if isinstance(rows, Mapping):
        for player_id, row in rows.items():
            if not isinstance(row, Mapping):
                continue
            payload = dict(row)
            payload.setdefault("player_id", player_id)
            yield str(payload["player_id"]), payload
        return
    for row in rows:
        yield _required_text(row, "player_id"), row


def _player_external_ids(player: JsonObject) -> Iterable[tuple[str, str]]:
    for field_name, provider in PLAYER_EXTERNAL_ID_FIELDS.items():
        external_id = _external_id_text(player.get(field_name))
        if external_id is None:
            continue
        yield provider, external_id


def _external_id_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (Mapping, list, tuple, set)):
        return None
    external_id = str(value).strip()
    return external_id or None


def _normalize_provider(provider: str) -> str:
    return str(provider).strip().lower()


def _ordered_unique(*groups: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in groups:
        for value in group:
            normalized = str(value)
            if normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
    return values


def _required_text(row: JsonObject, key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required {key}")
    return str(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return int(number)


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _numeric_items(values: Mapping[str, Any]) -> Iterable[tuple[str, float]]:
    for key, value in values.items():
        number = _number_or_none(value)
        if number is not None:
            yield str(key), number


def _sleeper_points(row: JsonObject, stats: JsonObject) -> float | None:
    for key in ("pts_ppr", "pts_half_ppr", "pts_std", "points"):
        if key in stats:
            return _number_or_none(stats.get(key))
    for key in ("pts_ppr", "pts_half_ppr", "pts_std", "points"):
        if key in row:
            return _number_or_none(row.get(key))
    return None


def _player_name(row: JsonObject, player: JsonObject) -> str | None:
    for key in ("full_name", "name"):
        value = player.get(key) or row.get(key)
        if value:
            return str(value)
    first_name = player.get("first_name") or row.get("first_name")
    last_name = player.get("last_name") or row.get("last_name")
    return " ".join(str(part) for part in (first_name, last_name) if part) or None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: Mapping[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_definition in columns.items():
        if column_name in existing:
            continue
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    decoded = dict(row)
    for key, value in list(decoded.items()):
        if key.endswith("_json") and isinstance(value, str):
            decoded[key.removesuffix("_json")] = json.loads(value)
            del decoded[key]
    return decoded
