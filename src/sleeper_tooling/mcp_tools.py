from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from sleeper_tooling.client import SleeperClient, load_or_fetch_players
from sleeper_tooling.db import ApiResponseCache
from sleeper_tooling.decision_reports import (
    build_free_agent_watch,
    build_injury_watch,
    build_league_team_watch,
    build_opponent_watch,
    build_waiver_watch,
)
from sleeper_tooling.reports import flatten_player_rows, top_players_by_position
from sleeper_tooling.scoring import flatten_scored_player_rows

StatSource = Literal["stats", "projections"]
DEFAULT_POSITIONS = "QB,RB,WR,TE,K,DEF"


class FantasyToolRunner:
    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        cache_db: Path | None = None,
        players_cache: Path | None = None,
        cache_enabled: bool = True,
        refresh_cache: bool = False,
    ) -> None:
        self._client_factory = client_factory
        self.cache_db = cache_db or resolve_cache_db_path()
        self.players_cache = players_cache or Path(
            os.environ.get("SLEEPER_PLAYERS_CACHE", "/data/players.json")
        )
        self.cache_enabled = cache_enabled
        self.refresh_cache = refresh_cache

    def weekly_briefing(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        source: StatSource = "projections",
        positions: str = DEFAULT_POSITIONS,
        leader_limit: int = 5,
        trend_limit: int = 10,
        lookback_hours: int = 24,
    ) -> dict[str, Any]:
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, league_id)
            player_map = load_or_fetch_players(client, cache_path=self.players_cache)
            position_list = parse_positions(positions)
            leader_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source=source,
                scoring_settings=scoring_settings,
            )
            trends = client.get_trending_players(
                "add",
                lookback_hours=lookback_hours,
                limit=trend_limit,
            )
            return {
                "season": resolved_season,
                "week": resolved_week,
                "leader_source": source,
                "scoring_source": league_id or "sleeper_default_points",
                "leaders": top_players_by_position(leader_rows, limit=leader_limit),
                "waiver_signal": build_waiver_watch(
                    trends=trends,
                    players=player_map,
                    projection_rows=leader_rows,
                    rosters=[],
                    positions=position_list,
                    trend_type="add",
                ),
            }

    def waiver_watch(
        self,
        *,
        league_id: str,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        trend_type: str = "add",
        lookback_hours: int = 24,
        trend_limit: int = 100,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        validate_trend_type(trend_type)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, league_id)
            position_list = parse_positions(positions)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            rows = build_waiver_watch(
                trends=client.get_trending_players(
                    trend_type,
                    lookback_hours=lookback_hours,
                    limit=trend_limit,
                ),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                projection_rows=projection_rows,
                rosters=client.get_rosters(league_id),
                positions=position_list,
                trend_type=trend_type,
            )
            return rows[:limit]

    def free_agent_watch(
        self,
        *,
        league_id: str,
        season: int | None = None,
        week: int | None = None,
        positions: str = "RB,WR,TE",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, league_id)
            position_list = parse_positions(positions)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            rows = build_free_agent_watch(
                projection_rows=projection_rows,
                rosters=client.get_rosters(league_id),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                positions=position_list,
            )
            return rows[:limit]

    def injury_watch(self, *, league_id: str) -> list[dict[str, Any]]:
        with self._client() as client:
            return build_injury_watch(
                users=client.get_league_users(league_id),
                rosters=client.get_rosters(league_id),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
            )

    def opponent_watch(
        self,
        *,
        league_id: str,
        roster_id: int,
        season: int | None = None,
        week: int | None = None,
    ) -> dict[str, Any]:
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=parse_positions(DEFAULT_POSITIONS),
                source="projections",
                scoring_settings=get_league_scoring_settings(client, league_id),
            )
            return build_opponent_watch(
                roster_id=roster_id,
                week=resolved_week,
                users=client.get_league_users(league_id),
                rosters=client.get_rosters(league_id),
                matchups=client.get_matchups(league_id, resolved_week),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                projection_rows=projection_rows,
            )

    def league_team_watch(
        self,
        *,
        league_id: str,
        week: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._client() as client:
            _, resolved_week = resolve_season_week(client, None, week)
            return build_league_team_watch(
                week=resolved_week,
                users=client.get_league_users(league_id),
                rosters=client.get_rosters(league_id),
                transactions=client.get_transactions(league_id, resolved_week),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
            )

    def player_card(
        self,
        *,
        player_id: str,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        weeks_back: int = 6,
    ) -> dict[str, Any]:
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            players = load_or_fetch_players(client, cache_path=self.players_cache)
            player = players.get(str(player_id), {})
            position = str(player.get("position") or "RB")
            scoring_settings = get_league_scoring_settings(client, league_id)
            weekly_points = []
            start_week = max(1, resolved_week - weeks_back + 1)

            for target_week in range(start_week, resolved_week + 1):
                stats_rows = fetch_rows_for_positions(
                    client,
                    season=resolved_season,
                    week=target_week,
                    positions=[position],
                    source="stats",
                    scoring_settings=scoring_settings,
                )
                projection_rows = fetch_rows_for_positions(
                    client,
                    season=resolved_season,
                    week=target_week,
                    positions=[position],
                    source="projections",
                    scoring_settings=scoring_settings,
                )
                stat_row = find_player_row(stats_rows, player_id)
                projection_row = find_player_row(projection_rows, player_id)
                weekly_points.append(
                    {
                        "week": target_week,
                        "actual_points": (stat_row or {}).get("points", 0),
                        "projected_points": (projection_row or {}).get("points", 0),
                    }
                )

            return {
                "player_id": str(player_id),
                "name": player.get("full_name") or player_id,
                "team": player.get("team") or "",
                "position": position,
                "status": player.get("status") or "",
                "injury_status": player.get("injury_status") or "",
                "season": resolved_season,
                "week": resolved_week,
                "scoring_source": league_id or "sleeper_default_points",
                "chart_data": {"weekly_points": weekly_points},
                "evidence": [
                    "actual_points and projected_points are calculated with league scoring when league_id is provided",
                    "missing stat keys are treated as zero",
                ],
            }

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        cache = ApiResponseCache(self.cache_db) if self.cache_enabled else None
        return SleeperClient(cache=cache, refresh_cache=self.refresh_cache)


def resolve_cache_db_path() -> Path:
    if os.environ.get("SLEEPER_CACHE_DB"):
        return Path(os.environ["SLEEPER_CACHE_DB"])
    cache_dir = Path(os.environ.get("SLEEPER_CACHE_DIR", "/data"))
    return cache_dir / "sleeper.db"


def parse_positions(positions: str) -> list[str]:
    return [position.strip().upper() for position in positions.split(",") if position.strip()]


def resolve_season_week(client: Any, season: int | None, week: int | None) -> tuple[int, int]:
    if season is not None and week is not None:
        return season, week
    state = client.get_nfl_state()
    return season or int(state["season"]), week or int(state["week"])


def get_league_scoring_settings(client: Any, league_id: str | None) -> dict[str, Any] | None:
    if not league_id:
        return None
    league = client.get_league(league_id)
    return league.get("scoring_settings") or {}


def fetch_rows_for_positions(
    client: Any,
    *,
    season: int,
    week: int,
    positions: list[str],
    source: StatSource,
    scoring_settings: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        if source == "projections":
            raw_rows = client.get_projections(
                season,
                week=week,
                position=position,
                order_by="pts_ppr",
            )
        else:
            raw_rows = client.get_stats(
                season,
                week=week,
                position=position,
                order_by="pts_ppr",
            )
        rows.extend(filter_exact_position(flatten_rows(raw_rows, scoring_settings), position))
    return rows


def flatten_rows(
    raw_rows: list[dict[str, Any]],
    scoring_settings: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if scoring_settings is None:
        return flatten_player_rows(raw_rows)
    return flatten_scored_player_rows(raw_rows, scoring_settings)


def filter_exact_position(
    rows: list[dict[str, Any]],
    position: str | None,
) -> list[dict[str, Any]]:
    if not position:
        return rows
    expected = position.upper()
    return [row for row in rows if str(row.get("position") or "").upper() == expected]


def find_player_row(rows: list[dict[str, Any]], player_id: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("player_id")) == str(player_id):
            return row
    return None


def validate_trend_type(trend_type: str) -> None:
    if trend_type not in {"add", "drop"}:
        raise ValueError("trend_type must be 'add' or 'drop'")
