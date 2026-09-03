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
        default_league_id: str | None = None,
        default_roster_id: int | None = None,
        cache_enabled: bool = True,
        refresh_cache: bool = False,
    ) -> None:
        self._client_factory = client_factory
        self.cache_db = cache_db or resolve_cache_db_path()
        self.players_cache = players_cache or Path(
            os.environ.get("SLEEPER_PLAYERS_CACHE", "/data/players.json")
        )
        self.default_league_id = default_league_id or os.environ.get(
            "SLEEPER_DEFAULT_LEAGUE_ID"
        )
        self.default_roster_id = default_roster_id or resolve_default_roster_id()
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
        validate_stat_source(source)
        resolved_league_id = self._resolve_optional_league_id(league_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
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
                "league_id": resolved_league_id,
                "leader_source": source,
                "scoring_source": resolved_league_id or "sleeper_default_points",
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

    def weekly_performance_backtest(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        start_week: int | None = None,
        weeks: int = 2,
        positions: str = DEFAULT_POSITIONS,
        source: StatSource = "stats",
        limit: int = 5,
        movement_limit: int = 5,
    ) -> dict[str, Any]:
        if weeks < 1:
            raise ValueError("weeks must be at least 1")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if movement_limit < 1:
            raise ValueError("movement_limit must be at least 1")
        validate_stat_source(source)

        resolved_league_id = self._resolve_optional_league_id(league_id)
        with self._client() as client:
            resolved_season, end_week = resolve_season_week(client, season, None)
            resolved_start_week = start_week or max(1, end_week - weeks + 1)
            target_weeks = list(range(resolved_start_week, resolved_start_week + weeks))
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
            position_list = parse_positions(positions)
            ranked_by_week: dict[int, list[dict[str, Any]]] = {}
            weekly_leaders: list[dict[str, Any]] = []

            for target_week in target_weeks:
                rows = fetch_rows_for_positions(
                    client,
                    season=resolved_season,
                    week=target_week,
                    positions=position_list,
                    source=source,
                    scoring_settings=scoring_settings,
                )
                ranked_rows = rank_rows_by_position(rows)
                ranked_by_week[target_week] = ranked_rows
                weekly_leaders.append(
                    {
                        "week": target_week,
                        "leaders": top_players_by_position(rows, limit=limit),
                    }
                )

            return {
                "season": resolved_season,
                "start_week": resolved_start_week,
                "end_week": target_weeks[-1],
                "weeks": target_weeks,
                "positions": position_list,
                "source": source,
                "league_id": resolved_league_id,
                "scoring_source": resolved_league_id or "sleeper_default_points",
                "weekly_leaders": weekly_leaders,
                "week_over_week": [
                    compare_ranked_weeks(
                        previous_week=previous_week,
                        current_week=current_week,
                        previous_rows=ranked_by_week[previous_week],
                        current_rows=ranked_by_week[current_week],
                        limit=movement_limit,
                    )
                    for previous_week, current_week in zip(
                        target_weeks,
                        target_weeks[1:],
                    )
                ],
            }

    def waiver_watch(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        trend_type: str = "add",
        lookback_hours: int = 24,
        trend_limit: int = 100,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        validate_trend_type(trend_type)
        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
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
                rosters=client.get_rosters(resolved_league_id),
                positions=position_list,
                trend_type=trend_type,
            )
            return with_context(
                rows[:limit],
                league_id=resolved_league_id,
                season=resolved_season,
                week=resolved_week,
            )

    def waiver_wire_watch(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = "RB,WR,TE",
        lookback_hours: int = 24,
        trend_limit: int = 100,
        limit: int = 25,
        recent_weeks: int = 3,
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if recent_weeks < 0:
            raise ValueError("recent_weeks must be at least 0")

        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
            position_list = parse_positions(positions)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            players = load_or_fetch_players(client, cache_path=self.players_cache)
            rosters = client.get_rosters(resolved_league_id)
            add_trends = client.get_trending_players(
                "add",
                lookback_hours=lookback_hours,
                limit=trend_limit,
            )
            drop_trends = client.get_trending_players(
                "drop",
                lookback_hours=lookback_hours,
                limit=trend_limit,
            )
            candidates = build_waiver_watch(
                trends=add_trends,
                players=players,
                projection_rows=projection_rows,
                rosters=rosters,
                positions=position_list,
                trend_type="add",
            )
            enriched = enrich_waiver_candidates(
                candidates,
                drop_trends=drop_trends,
                recent_rows=fetch_recent_actuals(
                    client,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                    scoring_settings=scoring_settings,
                    weeks_back=recent_weeks,
                ),
            )
            return {
                "season": resolved_season,
                "week": resolved_week,
                "league_id": resolved_league_id,
                "positions": position_list,
                "lookback_hours": lookback_hours,
                "scoring_source": resolved_league_id,
                "candidates": with_context(
                    enriched[:limit],
                    league_id=resolved_league_id,
                    season=resolved_season,
                    week=resolved_week,
                ),
                "evidence": [
                    "candidates are unrostered in the league",
                    "projected_points use league scoring",
                    "recent_actual_points uses completed stats for prior weeks",
                    "drop_trend_count is included to down-rank noisy add trends",
                ],
            }

    def free_agent_watch(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = "RB,WR,TE",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
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
                rosters=client.get_rosters(resolved_league_id),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                positions=position_list,
            )
            return with_context(
                rows[:limit],
                league_id=resolved_league_id,
                season=resolved_season,
                week=resolved_week,
            )

    def injury_watch(self, *, league_id: str | None = None) -> list[dict[str, Any]]:
        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            rows = build_injury_watch(
                users=client.get_league_users(resolved_league_id),
                rosters=client.get_rosters(resolved_league_id),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
            )
            return with_context(rows, league_id=resolved_league_id)

    def opponent_watch(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
    ) -> dict[str, Any]:
        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=parse_positions(DEFAULT_POSITIONS),
                source="projections",
                scoring_settings=get_league_scoring_settings(client, resolved_league_id),
            )
            report = build_opponent_watch(
                roster_id=resolved_roster_id,
                week=resolved_week,
                users=client.get_league_users(resolved_league_id),
                rosters=client.get_rosters(resolved_league_id),
                matchups=client.get_matchups(resolved_league_id, resolved_week),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                projection_rows=projection_rows,
            )
            return {
                "league_id": resolved_league_id,
                "roster_id": resolved_roster_id,
                "season": resolved_season,
                **report,
            }

    def league_team_watch(
        self,
        *,
        league_id: str | None = None,
        week: int | None = None,
    ) -> list[dict[str, Any]]:
        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            _, resolved_week = resolve_season_week(client, None, week)
            rows = build_league_team_watch(
                week=resolved_week,
                users=client.get_league_users(resolved_league_id),
                rosters=client.get_rosters(resolved_league_id),
                transactions=client.get_transactions(resolved_league_id, resolved_week),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
            )
            return with_context(rows, league_id=resolved_league_id, week=resolved_week)

    def player_card(
        self,
        *,
        player_id: str,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        weeks_back: int = 6,
    ) -> dict[str, Any]:
        resolved_league_id = self._resolve_optional_league_id(league_id)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            players = load_or_fetch_players(client, cache_path=self.players_cache)
            player = players.get(str(player_id), {})
            position = str(player.get("position") or "RB")
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
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
                "league_id": resolved_league_id,
                "name": player.get("full_name") or player_id,
                "team": player.get("team") or "",
                "position": position,
                "status": player.get("status") or "",
                "injury_status": player.get("injury_status") or "",
                "season": resolved_season,
                "week": resolved_week,
                "scoring_source": resolved_league_id or "sleeper_default_points",
                "chart_data": {"weekly_points": weekly_points},
                "evidence": [
                    "actual_points and projected_points are calculated with league scoring when league_id is provided",
                    "missing stat keys are treated as zero",
                ],
            }

    def _resolve_optional_league_id(self, league_id: str | None) -> str | None:
        return league_id or self.default_league_id

    def _require_league_id(self, league_id: str | None) -> str:
        resolved = self._resolve_optional_league_id(league_id)
        if not resolved:
            raise ValueError(
                "league_id is required; pass league_id or set SLEEPER_DEFAULT_LEAGUE_ID"
            )
        return resolved

    def _require_roster_id(self, roster_id: int | None) -> int:
        resolved = roster_id if roster_id is not None else self.default_roster_id
        if resolved is None:
            raise ValueError(
                "roster_id is required; pass roster_id or set SLEEPER_DEFAULT_ROSTER_ID"
            )
        return resolved

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


def resolve_default_roster_id() -> int | None:
    value = os.environ.get("SLEEPER_DEFAULT_ROSTER_ID")
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("SLEEPER_DEFAULT_ROSTER_ID must be an integer") from exc


def with_context(
    rows: list[dict[str, Any]],
    *,
    league_id: str,
    roster_id: int | None = None,
    season: int | None = None,
    week: int | None = None,
) -> list[dict[str, Any]]:
    context = {"league_id": league_id}
    if roster_id is not None:
        context["roster_id"] = roster_id
    if season is not None:
        context["season"] = season
    if week is not None:
        context["week"] = week
    return [{**context, **row} for row in rows]


def rank_rows_by_position(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        position = str(row.get("position") or "")
        if position:
            grouped.setdefault(position, []).append(row)

    ranked_rows: list[dict[str, Any]] = []
    for position, position_rows in grouped.items():
        sorted_rows = sorted(
            position_rows,
            key=lambda row: float(row.get("points") or 0),
            reverse=True,
        )
        for rank, row in enumerate(sorted_rows, start=1):
            ranked_rows.append({"position_rank": rank, **row})
    return ranked_rows


def compare_ranked_weeks(
    *,
    previous_week: int,
    current_week: int,
    previous_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    previous_by_key = keyed_player_rows(previous_rows)
    current_by_key = keyed_player_rows(current_rows)
    shared_keys = set(previous_by_key) & set(current_by_key)
    appeared_keys = set(current_by_key) - set(previous_by_key)
    disappeared_keys = set(previous_by_key) - set(current_by_key)

    movers = [
        movement_row(previous_by_key[key], current_by_key[key])
        for key in shared_keys
    ]
    risers = sorted(
        movers,
        key=lambda row: (
            float(row.get("points_delta") or 0),
            float(row.get("current_points") or 0),
        ),
        reverse=True,
    )
    fallers = sorted(
        movers,
        key=lambda row: (
            float(row.get("points_delta") or 0),
            float(row.get("current_points") or 0),
        ),
    )

    return {
        "previous_week": previous_week,
        "current_week": current_week,
        "top_risers": risers[:limit],
        "top_fallers": fallers[:limit],
        "appeared": sorted(
            [appearance_row(current_by_key[key], current=True) for key in appeared_keys],
            key=lambda row: (
                str(row.get("position") or ""),
                int(row.get("current_rank") or 9999),
            ),
        )[:limit],
        "disappeared": sorted(
            [
                appearance_row(previous_by_key[key], current=False)
                for key in disappeared_keys
            ],
            key=lambda row: (
                str(row.get("position") or ""),
                int(row.get("previous_rank") or 9999),
            ),
        )[:limit],
    }


def keyed_player_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row.get("position") or ""), str(row.get("player_id") or "")): row
        for row in rows
        if row.get("position") and row.get("player_id")
    }


def movement_row(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    previous_points = float(previous.get("points") or 0)
    current_points = float(current.get("points") or 0)
    previous_rank = int(previous.get("position_rank") or 0)
    current_rank = int(current.get("position_rank") or 0)
    return {
        "player_id": current.get("player_id", ""),
        "name": current.get("name", ""),
        "team": current.get("team", ""),
        "position": current.get("position", ""),
        "previous_points": previous_points,
        "current_points": current_points,
        "points_delta": round(current_points - previous_points, 2),
        "previous_rank": previous_rank,
        "current_rank": current_rank,
        "rank_delta": previous_rank - current_rank,
    }


def appearance_row(row: dict[str, Any], *, current: bool) -> dict[str, Any]:
    output = {
        "player_id": row.get("player_id", ""),
        "name": row.get("name", ""),
        "team": row.get("team", ""),
        "position": row.get("position", ""),
    }
    if current:
        output["current_points"] = row.get("points", 0)
        output["current_rank"] = row.get("position_rank", "")
    else:
        output["previous_points"] = row.get("points", 0)
        output["previous_rank"] = row.get("position_rank", "")
    return output


def fetch_recent_actuals(
    client: Any,
    *,
    season: int,
    week: int,
    positions: list[str],
    scoring_settings: dict[str, Any] | None,
    weeks_back: int,
) -> dict[str, list[dict[str, Any]]]:
    if weeks_back == 0:
        return {}
    start_week = max(1, week - weeks_back)
    recent_rows: dict[str, list[dict[str, Any]]] = {}
    for target_week in range(start_week, week):
        rows = fetch_rows_for_positions(
            client,
            season=season,
            week=target_week,
            positions=positions,
            source="stats",
            scoring_settings=scoring_settings,
        )
        for row in rows:
            player_id = str(row.get("player_id") or "")
            if player_id:
                recent_rows.setdefault(player_id, []).append(
                    {"week": target_week, "points": row.get("points", 0)}
                )
    return recent_rows


def enrich_waiver_candidates(
    candidates: list[dict[str, Any]],
    *,
    drop_trends: list[dict[str, Any]],
    recent_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    drops_by_player = {
        str(trend.get("player_id")): int(trend.get("count") or 0)
        for trend in drop_trends
    }
    enriched = []
    for row in candidates:
        player_id = str(row.get("player_id") or "")
        recent_points = recent_rows.get(player_id, [])
        average = (
            round(
                sum(float(item.get("points") or 0) for item in recent_points)
                / len(recent_points),
                2,
            )
            if recent_points
            else 0
        )
        drop_count = drops_by_player.get(player_id, 0)
        add_count = int(row.get("trend_count") or 0)
        projected_points = float(row.get("projected_points") or 0)
        enriched.append(
            {
                **row,
                "drop_trend_count": drop_count,
                "net_trend_count": add_count - drop_count,
                "recent_actual_points": recent_points,
                "recent_average_points": average,
                "watch_score": round(
                    projected_points + average + ((add_count - drop_count) / 100),
                    2,
                ),
            }
        )
    return sorted(
        enriched,
        key=lambda row: (
            float(row.get("watch_score") or 0),
            float(row.get("projected_points") or 0),
            int(row.get("net_trend_count") or 0),
        ),
        reverse=True,
    )


def parse_positions(positions: str) -> list[str]:
    return [position.strip().upper() for position in positions.split(",") if position.strip()]


def validate_stat_source(source: str) -> None:
    if source not in {"stats", "projections"}:
        raise ValueError("source must be 'stats' or 'projections'")


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
    validate_stat_source(source)
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
