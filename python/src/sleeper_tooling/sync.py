from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sleeper_tooling.client import SleeperClient


class SyncError(RuntimeError):
    def __init__(self, result: "SyncResult", original: Exception) -> None:
        super().__init__(str(original))
        self.result = result
        self.original = original


class NormalizedRepositoryUnavailable(RuntimeError):
    """Raised when normalized DB repository primitives have not landed yet."""


class NormalizedSleeperRepository(Protocol):
    def start_sync_run(
        self,
        *,
        league_id: str,
        seasons: list[int],
        weeks: list[int],
        started_at: float,
    ) -> object:
        ...

    def finish_sync_run(
        self,
        run_id: object,
        *,
        status: str,
        row_counts: dict[str, int],
        finished_at: float,
        error_text: str | None = None,
    ) -> None:
        ...

    def upsert_nfl_state(self, state: dict[str, Any]) -> int | None:
        ...

    def upsert_players(self, players: dict[str, dict[str, Any]]) -> int | None:
        ...

    def upsert_league(self, league: dict[str, Any]) -> int | None:
        ...

    def upsert_league_users(
        self,
        *,
        league_id: str,
        users: list[dict[str, Any]],
    ) -> int | None:
        ...

    def upsert_rosters(
        self,
        *,
        league_id: str,
        rosters: list[dict[str, Any]],
    ) -> int | Mapping[str, int] | None:
        ...

    def upsert_matchups(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        matchups: list[dict[str, Any]],
    ) -> int | Mapping[str, int] | None:
        ...

    def upsert_transactions(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        transactions: list[dict[str, Any]],
    ) -> int | None:
        ...

    def upsert_player_week_rows(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        source: str,
        rows: list[dict[str, Any]],
        scoring_settings: dict[str, Any],
    ) -> int | Mapping[str, int] | None:
        ...


class SyncStatusRepository(Protocol):
    def sync_status(self) -> dict[str, Any]:
        ...

    def clear_normalized(self) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class SyncTarget:
    league_id: str
    seasons: list[int]
    weeks: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "league_id": self.league_id,
            "seasons": self.seasons,
            "weeks": self.weeks,
        }


@dataclass(frozen=True)
class SyncResult:
    run_id: object
    status: str
    target: SyncTarget
    row_counts: dict[str, int]
    started_at: float
    finished_at: float
    error_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            **self.target.to_dict(),
            "row_counts": dict(sorted(self.row_counts.items())),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_text": self.error_text,
        }


EXPECTED_REPOSITORY_METHODS = [
    "start_sync_run",
    "finish_sync_run",
    "upsert_nfl_state",
    "upsert_players",
    "upsert_league",
    "upsert_league_users",
    "upsert_rosters",
    "upsert_matchups",
    "upsert_transactions",
    "upsert_player_week_rows",
    "sync_status",
    "clear_normalized",
]


class UnavailableNormalizedRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def start_sync_run(self, **_: Any) -> object:
        raise NormalizedRepositoryUnavailable(unavailable_message())

    def finish_sync_run(self, *args: Any, **kwargs: Any) -> None:
        return None

    def sync_status(self) -> dict[str, Any]:
        return {
            "repository_available": False,
            "db_path": str(self.db_path),
            "message": unavailable_message(),
            "expected_methods": EXPECTED_REPOSITORY_METHODS,
        }

    def clear_normalized(self) -> dict[str, Any]:
        return {
            "repository_available": False,
            "db_path": str(self.db_path),
            "deleted": {},
            "message": unavailable_message(),
            "expected_methods": EXPECTED_REPOSITORY_METHODS,
        }

    def __getattr__(self, name: str) -> Any:
        if name.startswith("upsert_"):
            raise NormalizedRepositoryUnavailable(unavailable_message())
        raise AttributeError(name)


class SleeperSyncService:
    def __init__(
        self,
        *,
        client: SleeperClient,
        repository: NormalizedSleeperRepository,
        clock: Any = time.time,
    ) -> None:
        self.client = client
        self.repository = repository
        self.clock = clock

    def close(self) -> None:
        close = getattr(self.repository, "close", None)
        if callable(close):
            close()

    def sync(
        self,
        *,
        league_id: str | None = None,
        seasons: list[int] | None = None,
        weeks: list[int] | None = None,
    ) -> SyncResult:
        state = self.client.get_nfl_state()
        target = resolve_sync_target(
            state=state,
            league_id=league_id,
            seasons=seasons,
            weeks=weeks,
        )
        started_at = float(self.clock())
        run_id = self.repository.start_sync_run(
            league_id=target.league_id,
            seasons=target.seasons,
            weeks=target.weeks,
            started_at=started_at,
        )
        row_counts: dict[str, int] = {}

        try:
            _add_count(
                row_counts,
                "nfl_state",
                self.repository.upsert_nfl_state(state),
                fallback=1,
            )
            players = self.client.get_players()
            _add_count(
                row_counts,
                "players",
                self.repository.upsert_players(players),
                fallback=len(players),
            )
            leagues_by_season = self._sync_leagues(target=target, row_counts=row_counts)

            for season in target.seasons:
                league = leagues_by_season.get(season) or leagues_by_season[
                    max(leagues_by_season)
                ]
                season_league_id = str(league.get("league_id") or target.league_id)
                scoring_settings = league.get("scoring_settings") or {}
                self._sync_league_members(
                    league_id=season_league_id,
                    row_counts=row_counts,
                )
                for week in target.weeks:
                    self._sync_league_week(
                        league_id=season_league_id,
                        season=season,
                        week=week,
                        scoring_settings=scoring_settings,
                        row_counts=row_counts,
                    )
        except Exception as exc:
            finished_at = float(self.clock())
            error_text = str(exc)
            self.repository.finish_sync_run(
                run_id,
                status="failed",
                row_counts=row_counts,
                finished_at=finished_at,
                error_text=error_text,
            )
            result = SyncResult(
                run_id=run_id,
                status="failed",
                target=target,
                row_counts=row_counts,
                started_at=started_at,
                finished_at=finished_at,
                error_text=error_text,
            )
            raise SyncError(result, exc) from exc

        finished_at = float(self.clock())
        self.repository.finish_sync_run(
            run_id,
            status="success",
            row_counts=row_counts,
            finished_at=finished_at,
        )
        return SyncResult(
            run_id=run_id,
            status="success",
            target=target,
            row_counts=row_counts,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _sync_leagues(
        self,
        *,
        target: SyncTarget,
        row_counts: dict[str, int],
    ) -> dict[int, dict[str, Any]]:
        leagues_by_season: dict[int, dict[str, Any]] = {}
        league = self.client.get_league(target.league_id)
        self._record_league(
            league,
            default_season=max(target.seasons),
            row_counts=row_counts,
            leagues_by_season=leagues_by_season,
        )

        next_league_id = league.get("previous_league_id")
        while next_league_id and not set(target.seasons).issubset(leagues_by_season):
            previous_league = self.client.get_league(str(next_league_id))
            missing_seasons = [
                season for season in target.seasons if season not in leagues_by_season
            ]
            self._record_league(
                previous_league,
                default_season=max(missing_seasons),
                row_counts=row_counts,
                leagues_by_season=leagues_by_season,
            )
            next_league_id = previous_league.get("previous_league_id")

        return leagues_by_season

    def _record_league(
        self,
        league: dict[str, Any],
        *,
        default_season: int,
        row_counts: dict[str, int],
        leagues_by_season: dict[int, dict[str, Any]],
    ) -> None:
        season = _int_or_default(league.get("season"), default_season)
        leagues_by_season[season] = league
        _add_count(
            row_counts,
            "leagues",
            self.repository.upsert_league(league),
            fallback=1,
        )

    def _sync_league_members(
        self,
        *,
        league_id: str,
        row_counts: dict[str, int],
    ) -> None:
        users = self.client.get_league_users(league_id)
        _add_count(
            row_counts,
            "league_users",
            self.repository.upsert_league_users(
                league_id=league_id,
                users=users,
            ),
            fallback=len(users),
        )
        rosters = self.client.get_rosters(league_id)
        _add_count(
            row_counts,
            "rosters",
            self.repository.upsert_rosters(
                league_id=league_id,
                rosters=rosters,
            ),
            fallback=len(rosters),
        )

    def _sync_league_week(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        scoring_settings: dict[str, Any],
        row_counts: dict[str, int],
    ) -> None:
        matchups = self.client.get_matchups(league_id, week)
        _add_count(
            row_counts,
            "matchups",
            self.repository.upsert_matchups(
                league_id=league_id,
                season=season,
                week=week,
                matchups=matchups,
            ),
            fallback=len(matchups),
        )
        transactions = self.client.get_transactions(league_id, week)
        _add_count(
            row_counts,
            "transactions",
            self.repository.upsert_transactions(
                league_id=league_id,
                season=season,
                week=week,
                transactions=transactions,
            ),
            fallback=len(transactions),
        )
        stats = self.client.get_stats(season, week=week)
        _add_count(
            row_counts,
            "stats",
            self.repository.upsert_player_week_rows(
                league_id=league_id,
                season=season,
                week=week,
                source="stats",
                rows=stats,
                scoring_settings=scoring_settings,
            ),
            fallback=len(stats),
        )
        projections = self.client.get_projections(season, week=week)
        _add_count(
            row_counts,
            "projections",
            self.repository.upsert_player_week_rows(
                league_id=league_id,
                season=season,
                week=week,
                source="projections",
                rows=projections,
                scoring_settings=scoring_settings,
            ),
            fallback=len(projections),
        )


def resolve_sync_target(
    *,
    state: dict[str, Any],
    league_id: str | None = None,
    seasons: list[int] | None = None,
    weeks: list[int] | None = None,
) -> SyncTarget:
    resolved_league_id = league_id or os.environ.get("SLEEPER_DEFAULT_LEAGUE_ID")
    if not resolved_league_id:
        raise ValueError(
            "league_id is required. Pass --league-id or set SLEEPER_DEFAULT_LEAGUE_ID."
        )

    current_season = _positive_int(state.get("season"), "season")
    current_week = _positive_int(state.get("week"), "week")
    resolved_seasons = _dedupe_ints(seasons or [current_season - 1, current_season])
    resolved_weeks = _dedupe_ints(weeks or list(range(1, current_week + 1)))
    return SyncTarget(
        league_id=str(resolved_league_id),
        seasons=resolved_seasons,
        weeks=resolved_weeks,
    )


class SQLiteNormalizedRepositoryAdapter:
    target_name = "normalized_sleeper_data"

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    @property
    def db_path(self) -> Path:
        return self.repository.db_path

    def close(self) -> None:
        self.repository.close()

    def start_sync_run(
        self,
        *,
        league_id: str,
        seasons: list[int],
        weeks: list[int],
        started_at: float,
    ) -> object:
        return self.repository.create_sync_run(
            target=self.target_name,
            league_id=league_id,
            status="running",
            metadata={
                "seasons": seasons,
                "weeks": weeks,
                "started_at": started_at,
            },
        )

    def finish_sync_run(
        self,
        run_id: object,
        *,
        status: str,
        row_counts: dict[str, int],
        finished_at: float,
        error_text: str | None = None,
    ) -> None:
        self.repository.finish_sync_run(
            int(run_id),
            status=status,
            row_counts=row_counts,
            error_text=error_text,
        )

    def upsert_nfl_state(self, state: dict[str, Any]) -> int:
        self.repository._connection.execute(
            """
            INSERT INTO context_source_timestamps (
                source,
                fetched_at,
                metadata_json
            )
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                metadata_json = excluded.metadata_json
            """,
            ("sleeper_nfl_state", time.time(), json.dumps(state, sort_keys=True)),
        )
        self.repository._connection.commit()
        return 1

    def upsert_players(self, players: dict[str, dict[str, Any]]) -> int | None:
        return self.repository.upsert_players(players)

    def upsert_league(self, league: dict[str, Any]) -> int | None:
        return self.repository.upsert_league_settings(league)

    def upsert_league_users(
        self,
        *,
        league_id: str,
        users: list[dict[str, Any]],
    ) -> int | None:
        return self.repository.upsert_league_users(league_id, users)

    def upsert_rosters(
        self,
        *,
        league_id: str,
        rosters: list[dict[str, Any]],
    ) -> int | Mapping[str, int] | None:
        return self.repository.upsert_rosters(league_id, rosters)

    def upsert_matchups(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        matchups: list[dict[str, Any]],
    ) -> int | Mapping[str, int] | None:
        return self.repository.upsert_matchups(league_id, season, week, matchups)

    def upsert_transactions(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        transactions: list[dict[str, Any]],
    ) -> int | None:
        return self.repository.upsert_transactions(
            league_id,
            transactions,
            week=week,
        )

    def upsert_player_week_rows(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        source: str,
        rows: list[dict[str, Any]],
        scoring_settings: dict[str, Any],
    ) -> int | Mapping[str, int] | None:
        return self.repository.upsert_player_week_rows(
            season=season,
            week=week,
            source=source,
            rows=rows,
            scoring_settings=scoring_settings,
        )

    def sync_status(self) -> dict[str, Any]:
        return {
            "repository_available": True,
            "db_path": str(self.db_path),
            "latest_sync": self.repository.latest_sync_run(target=self.target_name),
            "row_counts": self._row_counts(),
        }

    def decision_data_status(self, *, season: int | None = None) -> dict[str, Any]:
        where = ""
        params: list[Any] = []
        if season is not None:
            where = "WHERE season = ?"
            params.append(int(season))
        row = self.repository._connection.execute(
            f"""
            SELECT COUNT(*) AS row_count
            FROM player_week_stat_values
            {where}
            """,
            params,
        ).fetchone()
        latest_sync = self.repository.latest_sync_run(target=self.target_name)
        return {
            "repository_available": True,
            "db_path": str(self.db_path),
            "season": season,
            "numeric_stat_rows": int(row["row_count"] or 0),
            "latest_stats_week": self._latest_week(source="stats", season=season),
            "latest_projections_week": self._latest_week(
                source="projections",
                season=season,
            ),
            "last_synced_at": (latest_sync or {}).get("finished_at")
            or (latest_sync or {}).get("started_at"),
            "sources": self._sources(season=season),
            "metadata": {"latest_sync": latest_sync},
        }

    def query_numeric_stat_rows(
        self,
        *,
        source: str,
        season: int,
        start_week: int,
        end_week: int,
        stat_keys: Sequence[str] | None = None,
        player_ids: Sequence[str] | None = None,
        positions: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = [
            "v.source = ?",
            "v.season = ?",
            "v.week BETWEEN ? AND ?",
        ]
        params: list[Any] = [str(source), int(season), int(start_week), int(end_week)]
        _add_in_clause(where_clauses, params, "v.stat_key", stat_keys)
        _add_in_clause(where_clauses, params, "v.player_id", player_ids)
        _add_in_clause(
            where_clauses,
            params,
            "UPPER(COALESCE(r.position, p.position, ''))",
            [str(position).upper() for position in positions or []],
        )
        rows = self.repository._connection.execute(
            f"""
            SELECT
                v.season,
                v.week,
                v.player_id,
                COALESCE(r.player_name, p.full_name, v.player_id) AS name,
                COALESCE(r.team, p.team, '') AS team,
                COALESCE(r.position, p.position, '') AS position,
                v.stat_key,
                v.stat_value
            FROM player_week_stat_values v
            LEFT JOIN player_week_rows r
                ON r.source = v.source
                AND r.season = v.season
                AND r.week = v.week
                AND r.player_id = v.player_id
            LEFT JOIN players p
                ON p.player_id = v.player_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY v.week, v.player_id, v.stat_key
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def clear_normalized(self) -> dict[str, Any]:
        deleted: dict[str, int] = {}
        for table_name in NORMALIZED_TABLE_DELETE_ORDER:
            cursor = self.repository._connection.execute(f"DELETE FROM {table_name}")
            deleted[table_name] = int(cursor.rowcount)
        self.repository._connection.commit()
        return {
            "repository_available": True,
            "db_path": str(self.db_path),
            "deleted": deleted,
        }

    def _row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table_name in NORMALIZED_TABLES:
            row = self.repository._connection.execute(
                f"SELECT COUNT(*) AS row_count FROM {table_name}"
            ).fetchone()
            counts[table_name] = int(row["row_count"] or 0)
        return counts

    def _latest_week(self, *, source: str, season: int | None) -> int | None:
        where = "source = ?"
        params: list[Any] = [source]
        if season is not None:
            where += " AND season = ?"
            params.append(int(season))
        row = self.repository._connection.execute(
            f"""
            SELECT MAX(week) AS latest_week
            FROM player_week_stat_values
            WHERE {where}
            """,
            params,
        ).fetchone()
        return int(row["latest_week"]) if row and row["latest_week"] is not None else None

    def _sources(self, *, season: int | None) -> list[str]:
        where = ""
        params: list[Any] = []
        if season is not None:
            where = "WHERE season = ?"
            params.append(int(season))
        rows = self.repository._connection.execute(
            f"""
            SELECT DISTINCT source
            FROM player_week_stat_values
            {where}
            ORDER BY source
            """,
            params,
        ).fetchall()
        return [str(row["source"]) for row in rows]


NORMALIZED_TABLES = [
    "players",
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
]

NORMALIZED_TABLE_DELETE_ORDER = [
    "player_week_scoring_values",
    "player_week_stat_values",
    "player_week_rows",
    "matchup_players",
    "matchups",
    "transactions",
    "roster_players",
    "rosters",
    "league_users",
    "league_settings",
    "players",
    "sync_runs",
]


def build_normalized_repository(db_path: Path) -> Any:
    try:
        from sleeper_tooling.db import SleeperNormalizedRepository
    except ImportError:
        return UnavailableNormalizedRepository(db_path)
    return SQLiteNormalizedRepositoryAdapter(SleeperNormalizedRepository(db_path))


def unavailable_message() -> str:
    return "Normalized repository primitives are not available in this checkout."


def _add_in_clause(
    where_clauses: list[str],
    params: list[Any],
    column_expression: str,
    values: Sequence[str] | None,
) -> None:
    normalized_values = [str(value) for value in values or [] if str(value)]
    if not normalized_values:
        return
    placeholders = ", ".join("?" for _ in normalized_values)
    where_clauses.append(f"{column_expression} IN ({placeholders})")
    params.extend(normalized_values)


def _add_count(
    row_counts: dict[str, int],
    key: str,
    count: int | Mapping[str, int] | None,
    *,
    fallback: int,
) -> None:
    if isinstance(count, Mapping):
        if key not in count:
            row_counts[key] = row_counts.get(key, 0) + sum(
                int(value) for value in count.values()
            )
        for subkey, value in count.items():
            row_counts[str(subkey)] = row_counts.get(str(subkey), 0) + int(value)
        return
    row_counts[key] = row_counts.get(key, 0) + (
        fallback if count is None else int(count)
    )


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Sleeper state did not include a valid {name}.") from exc
    if parsed < 1:
        raise ValueError(f"Sleeper state {name} must be at least 1.")
    return parsed


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe_ints(values: list[int]) -> list[int]:
    return sorted({int(value) for value in values})
