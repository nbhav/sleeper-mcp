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
    build_lineup_recommendations,
    build_my_lineup,
    build_opponent_watch,
    build_trade_opportunities,
    build_waiver_watch,
    group_waiver_options_by_position,
    merge_available_candidates,
)
from sleeper_tooling.league_context import (
    render_context_env,
    resolve_league_context as build_league_context,
)
from sleeper_tooling.player_values import build_player_values
from sleeper_tooling.roster_analysis import (
    build_league_roster_analysis,
    build_roster_analysis,
)
from sleeper_tooling.normalized_decision_reads import (
    DEFAULT_MAX_AGE_SECONDS as NORMALIZED_DECISION_MAX_AGE_SECONDS,
    NormalizedDecisionInputs,
    NormalizedDecisionRead,
    NormalizedDecisionReader,
)
from sleeper_tooling.reports import flatten_player_rows, top_players_by_position
from sleeper_tooling.scoring import flatten_scored_player_rows
from sleeper_tooling.season import current_season_year
from sleeper_tooling.sync import SleeperSyncService, build_normalized_repository
from sleeper_tooling.trend_queries import (
    GRAPH_ROW_FIELDS,
    decision_data_status as build_decision_data_status,
    player_stat_trends as build_player_stat_trends,
    position_stat_leaders as build_position_stat_leaders,
)

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
        trend_repository: Any | None = None,
        decision_repository: Any | None = None,
        sync_service: Any | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._trend_repository = trend_repository
        self._decision_repository = decision_repository
        self._sync_service = sync_service
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

    def resolve_league_context(
        self,
        *,
        league_ref: str,
        user_ref: str | None = None,
        team_name: str | None = None,
    ) -> dict[str, Any]:
        lookup = user_ref or team_name
        if not lookup:
            raise ValueError("user_ref is required")
        with self._client() as client:
            context = build_league_context(
                client,
                league_ref=league_ref,
                team_name=lookup,
            )
            return {
                **context,
                "local_env_file": "/data/sleeper-mcp.env",
                "env_text": render_context_env(context, league_ref=league_ref),
                "cloudflare_vars": context["env"],
                "evidence": [
                    "league_id was parsed from league_ref",
                    "roster_id was matched from league users and rosters",
                    "Cloudflare Workers cannot mutate runtime vars; set cloudflare_vars before deploy or through the Cloudflare dashboard",
                ],
            }

    def decision_data_status(
        self,
        *,
        season: int | None = None,
        max_age_hours: float = 24,
    ) -> dict[str, Any]:
        if max_age_hours <= 0:
            raise ValueError("max_age_hours must be greater than zero")
        return build_decision_data_status(
            self._require_trend_repository(),
            season=season,
            max_age_seconds=int(max_age_hours * 3600),
        )

    def sync_decision_data(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        resolved_league_id = self._resolve_optional_league_id(league_id)
        if self._sync_service is not None:
            service = self._sync_service
            if hasattr(service, "sync_decision_data"):
                return service.sync_decision_data(
                    league_id=resolved_league_id,
                    season=season,
                    week=week,
                    force=force,
                )
            if callable(service):
                return service(
                    league_id=resolved_league_id,
                    season=season,
                    week=week,
                    force=force,
                )
            if hasattr(service, "sync"):
                return service.sync(
                    league_id=resolved_league_id,
                    seasons=[season] if season is not None else None,
                    weeks=[week] if week is not None else None,
                ).to_dict()
            raise ValueError("configured sync service is not callable")

        repository = build_normalized_repository(self.cache_db)
        try:
            with self._client(refresh_cache=force) as client:
                result = SleeperSyncService(
                    client=client,
                    repository=repository,
                ).sync(
                    league_id=resolved_league_id,
                    seasons=[season] if season is not None else None,
                    weeks=[week] if week is not None else None,
                )
                return result.to_dict()
        finally:
            close = getattr(repository, "close", None)
            if callable(close):
                close()

    def player_stat_trends(
        self,
        *,
        season: int,
        player_id: str,
        stat_key: str,
        start_week: int,
        end_week: int | None = None,
        source: StatSource = "stats",
    ) -> dict[str, Any]:
        resolved_end_week = end_week if end_week is not None else start_week
        rows = build_player_stat_trends(
            self._require_trend_repository(),
            season=season,
            player_id=player_id,
            stat_key=stat_key,
            start_week=start_week,
            end_week=resolved_end_week,
            source=source,
        )
        return {
            "season": season,
            "start_week": start_week,
            "end_week": resolved_end_week,
            "player_id": player_id,
            "stat_key": stat_key,
            "source": source,
            "shape": list(GRAPH_ROW_FIELDS),
            "rows": rows,
        }

    def position_stat_leaders(
        self,
        *,
        season: int,
        week: int,
        position: str,
        stat_key: str,
        source: StatSource = "stats",
        limit: int = 10,
    ) -> dict[str, Any]:
        rows = build_position_stat_leaders(
            self._require_trend_repository(),
            season=season,
            week=week,
            position=position,
            stat_key=stat_key,
            source=source,
            limit=limit,
        )
        return {
            "season": season,
            "week": week,
            "position": position.upper(),
            "stat_key": stat_key,
            "source": source,
            "limit": limit,
            "shape": list(GRAPH_ROW_FIELDS),
            "leaders": rows,
        }

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

    def my_lineup(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
    ) -> dict[str, Any]:
        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=season,
                week=week,
                positions=position_list,
            )
            if normalized.fresh and normalized.inputs is not None:
                return self._with_decision_metadata(
                    build_my_lineup(
                        league_id=resolved_league_id,
                        roster_id=resolved_roster_id,
                        season=season,
                        week=week,
                        league=normalized.inputs.league,
                        users=normalized.inputs.users,
                        rosters=normalized.inputs.rosters,
                        matchups=normalized.inputs.matchups,
                        players=normalized.inputs.players,
                        projection_rows=normalized.inputs.projection_rows,
                    ),
                    normalized,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                )
                if normalized.fresh and normalized.inputs is not None:
                    return self._with_decision_metadata(
                        build_my_lineup(
                            league_id=resolved_league_id,
                            roster_id=resolved_roster_id,
                            season=resolved_season,
                            week=resolved_week,
                            league=normalized.inputs.league,
                            users=normalized.inputs.users,
                            rosters=normalized.inputs.rosters,
                            matchups=normalized.inputs.matchups,
                            players=normalized.inputs.players,
                            projection_rows=normalized.inputs.projection_rows,
                        ),
                        normalized,
                        fallback_used=False,
                    )
            league = client.get_league(resolved_league_id)
            scoring_settings = league.get("scoring_settings") or {}
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            return self._with_decision_metadata(
                build_my_lineup(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    league=league,
                    users=client.get_league_users(resolved_league_id),
                    rosters=client.get_rosters(resolved_league_id),
                    matchups=client.get_matchups(resolved_league_id, resolved_week),
                    players=load_or_fetch_players(client, cache_path=self.players_cache),
                    projection_rows=projection_rows,
                ),
                normalized,
                fallback_used=True,
            )

    def lineup_recommendations(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        trend_limit: int = 100,
        lookback_hours: int = 24,
        min_delta: float = 1.0,
        limit: int = 10,
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if min_delta < 0:
            raise ValueError("min_delta must be zero or greater")

        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=season,
                week=week,
                positions=position_list,
            )
            if normalized.fresh and normalized.inputs is not None:
                return self._lineup_recommendations_from_inputs(
                    inputs=normalized.inputs,
                    normalized=normalized,
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=season,
                    week=week,
                    positions=position_list,
                    min_delta=min_delta,
                    limit=limit,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                )
                if normalized.fresh and normalized.inputs is not None:
                    return self._lineup_recommendations_from_inputs(
                        inputs=normalized.inputs,
                        normalized=normalized,
                        league_id=resolved_league_id,
                        roster_id=resolved_roster_id,
                        season=resolved_season,
                        week=resolved_week,
                        positions=position_list,
                        min_delta=min_delta,
                        limit=limit,
                        fallback_used=False,
                    )
            league = client.get_league(resolved_league_id)
            scoring_settings = league.get("scoring_settings") or {}
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            users = client.get_league_users(resolved_league_id)
            rosters = client.get_rosters(resolved_league_id)
            players = load_or_fetch_players(client, cache_path=self.players_cache)
            lineup = build_my_lineup(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=resolved_season,
                week=resolved_week,
                league=league,
                users=users,
                rosters=rosters,
                matchups=client.get_matchups(resolved_league_id, resolved_week),
                players=players,
                projection_rows=projection_rows,
            )
            return self._with_decision_metadata(
                build_lineup_recommendations(
                    lineup=lineup,
                    rosters=rosters,
                    players=players,
                    projection_rows=projection_rows,
                    add_trends=client.get_trending_players(
                        "add",
                        lookback_hours=lookback_hours,
                        limit=trend_limit,
                    ),
                    drop_trends=client.get_trending_players(
                        "drop",
                        lookback_hours=lookback_hours,
                        limit=trend_limit,
                    ),
                    positions=position_list,
                    min_delta=min_delta,
                    limit=limit,
                ),
                normalized,
                fallback_used=True,
            )

    def waiver_wire_watch(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
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
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=0,
                season=season,
                week=week,
                positions=position_list,
                recent_weeks=recent_weeks,
            )
            if normalized.fresh and normalized.inputs is not None:
                candidates = build_waiver_watch(
                    trends=normalized.inputs.add_trends,
                    players=normalized.inputs.players,
                    projection_rows=normalized.inputs.projection_rows,
                    rosters=normalized.inputs.rosters,
                    positions=position_list,
                    trend_type="add",
                )
                enriched = enrich_waiver_candidates(
                    candidates,
                    drop_trends=normalized.inputs.drop_trends,
                    recent_rows=normalized.inputs.recent_actuals,
                )
                return self._with_decision_metadata(
                    {
                        "season": season,
                        "week": week,
                        "league_id": resolved_league_id,
                        "positions": position_list,
                        "lookback_hours": lookback_hours,
                        "scoring_source": resolved_league_id,
                        "candidates": with_context(
                            enriched[:limit],
                            league_id=resolved_league_id,
                            season=season,
                            week=week,
                        ),
                        "evidence": [
                            "candidates are unrostered in the league",
                            "projected_points use league scoring",
                            "recent_actual_points uses completed stats for prior weeks",
                            "drop_trend_count is included to down-rank noisy add trends",
                        ],
                    },
                    normalized,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=0,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                    recent_weeks=recent_weeks,
                )
                if normalized.fresh and normalized.inputs is not None:
                    candidates = build_waiver_watch(
                        trends=normalized.inputs.add_trends,
                        players=normalized.inputs.players,
                        projection_rows=normalized.inputs.projection_rows,
                        rosters=normalized.inputs.rosters,
                        positions=position_list,
                        trend_type="add",
                    )
                    enriched = enrich_waiver_candidates(
                        candidates,
                        drop_trends=normalized.inputs.drop_trends,
                        recent_rows=normalized.inputs.recent_actuals,
                    )
                    return self._with_decision_metadata(
                        {
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
                        },
                        normalized,
                        fallback_used=False,
                    )
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
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
            return self._with_decision_metadata(
                {
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
                },
                normalized,
                fallback_used=True,
            )

    def waiver_wire_by_position(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        lookback_hours: int = 24,
        trend_limit: int = 100,
        per_position_limit: int = 10,
    ) -> dict[str, Any]:
        if per_position_limit < 1:
            raise ValueError("per_position_limit must be at least 1")

        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=season,
                week=week,
                positions=position_list,
            )
            if normalized.fresh and normalized.inputs is not None:
                return self._waiver_by_position_from_inputs(
                    inputs=normalized.inputs,
                    normalized=normalized,
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=season,
                    week=week,
                    positions=position_list,
                    per_position_limit=per_position_limit,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                )
                if normalized.fresh and normalized.inputs is not None:
                    return self._waiver_by_position_from_inputs(
                        inputs=normalized.inputs,
                        normalized=normalized,
                        league_id=resolved_league_id,
                        roster_id=resolved_roster_id,
                        season=resolved_season,
                        week=resolved_week,
                        positions=position_list,
                        per_position_limit=per_position_limit,
                        fallback_used=False,
                    )
            league = client.get_league(resolved_league_id)
            scoring_settings = league.get("scoring_settings") or {}
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            users = client.get_league_users(resolved_league_id)
            rosters = client.get_rosters(resolved_league_id)
            matchups = client.get_matchups(resolved_league_id, resolved_week)
            players = load_or_fetch_players(client, cache_path=self.players_cache)
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
            projection_candidates = build_free_agent_watch(
                projection_rows=projection_rows,
                rosters=rosters,
                players=players,
                positions=position_list,
            )
            trend_candidates = build_waiver_watch(
                trends=add_trends,
                players=players,
                projection_rows=projection_rows,
                rosters=rosters,
                positions=position_list,
                trend_type="add",
            )
            available_candidates = merge_available_candidates(
                projection_candidates=projection_candidates,
                trend_candidates=trend_candidates,
                players=players,
                add_trends=add_trends,
                drop_trends=drop_trends,
            )
            lineup = build_my_lineup(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=resolved_season,
                week=resolved_week,
                league=league,
                users=users,
                rosters=rosters,
                matchups=matchups,
                players=players,
                projection_rows=projection_rows,
            )
            roster_players = [
                row
                for row in lineup["lineup_table"]
                if row.get("player_id") != "0"
            ]
            return self._with_decision_metadata(
                {
                    "season": resolved_season,
                    "week": resolved_week,
                    "league_id": resolved_league_id,
                    "roster_id": resolved_roster_id,
                    "positions": position_list,
                    "per_position_limit": per_position_limit,
                    "by_position": group_waiver_options_by_position(
                        candidates=available_candidates,
                        roster_players=roster_players,
                        positions=position_list,
                        per_position_limit=per_position_limit,
                    ),
                    "evidence": [
                        "options are grouped by position and exclude rostered players",
                        "projected_gain_over_drop compares against an unprotected active roster drop candidate",
                        "FAAB hints are included only when the acquisition market is known to be waiver",
                    ],
                },
                normalized,
                fallback_used=True,
            )

    def free_agent_watch(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
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

    def player_values(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        limit: int = 50,
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        resolved_league_id = self._resolve_optional_league_id(league_id)
        position_list = parse_positions(positions)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            scoring_settings = get_league_scoring_settings(client, resolved_league_id)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            values = build_player_values(
                players=load_or_fetch_players(client, cache_path=self.players_cache),
                projection_rows=projection_rows,
                scoring_settings=scoring_settings,
            )
            filtered = [
                row
                for row in values
                if not position_list or str(row.get("position") or "").upper() in position_list
            ]
            return {
                "league_id": resolved_league_id,
                "season": resolved_season,
                "week": resolved_week,
                "positions": position_list,
                "scoring_source": resolved_league_id or "sleeper_default_points",
                "limit": limit,
                "values": filtered[:limit],
                "evidence": [
                    "values are deterministic from player metadata and weekly projection rows",
                    "week_value, three_week_value, season_value, and decision_value use the shared player value model",
                    "value_above_replacement uses the model replacement baselines",
                ],
            }

    def roster_analysis(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
    ) -> dict[str, Any]:
        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=season,
                week=week,
                positions=position_list,
            )
            if normalized.fresh and normalized.inputs is not None:
                return self._with_decision_metadata(
                    build_roster_analysis(
                        league_id=resolved_league_id,
                        roster_id=resolved_roster_id,
                        season=season,
                        week=week,
                        league=normalized.inputs.league,
                        users=normalized.inputs.users,
                        rosters=normalized.inputs.rosters,
                        matchups=normalized.inputs.matchups,
                        players=normalized.inputs.players,
                        projection_rows=normalized.inputs.projection_rows,
                    ),
                    normalized,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                )
                if normalized.fresh and normalized.inputs is not None:
                    return self._with_decision_metadata(
                        build_roster_analysis(
                            league_id=resolved_league_id,
                            roster_id=resolved_roster_id,
                            season=resolved_season,
                            week=resolved_week,
                            league=normalized.inputs.league,
                            users=normalized.inputs.users,
                            rosters=normalized.inputs.rosters,
                            matchups=normalized.inputs.matchups,
                            players=normalized.inputs.players,
                            projection_rows=normalized.inputs.projection_rows,
                        ),
                        normalized,
                        fallback_used=False,
                    )
            league = client.get_league(resolved_league_id)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=league.get("scoring_settings") or {},
            )
            return self._with_decision_metadata(
                build_roster_analysis(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    league=league,
                    users=client.get_league_users(resolved_league_id),
                    rosters=client.get_rosters(resolved_league_id),
                    matchups=client.get_matchups(resolved_league_id, resolved_week),
                    players=load_or_fetch_players(client, cache_path=self.players_cache),
                    projection_rows=projection_rows,
                ),
                normalized,
                fallback_used=True,
            )

    def league_roster_analysis(
        self,
        *,
        league_id: str | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
    ) -> dict[str, Any]:
        resolved_league_id = self._require_league_id(league_id)
        position_list = parse_positions(positions)
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            league = client.get_league(resolved_league_id)
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=league.get("scoring_settings") or {},
            )
            return {
                **build_league_roster_analysis(
                    league_id=resolved_league_id,
                    season=resolved_season,
                    week=resolved_week,
                    league=league,
                    users=client.get_league_users(resolved_league_id),
                    rosters=client.get_rosters(resolved_league_id),
                    matchups=client.get_matchups(resolved_league_id, resolved_week),
                    players=load_or_fetch_players(client, cache_path=self.players_cache),
                    projection_rows=projection_rows,
                ),
                "positions": position_list,
                "data_source": "sleeper_fallback",
                "freshness": {
                    "status": "not_checked",
                    "fresh": False,
                    "warnings": [
                        "league_roster_analysis currently uses live Sleeper inputs through the HTTP cache"
                    ],
                },
                "fallback_used": True,
                "sync_recommended": False,
            }

    def injury_watch(self, *, league_id: str | None = None) -> list[dict[str, Any]]:
        resolved_league_id = self._require_league_id(league_id)
        with self._client() as client:
            rows = build_injury_watch(
                users=client.get_league_users(resolved_league_id),
                rosters=client.get_rosters(resolved_league_id),
                players=load_or_fetch_players(client, cache_path=self.players_cache),
            )
            return with_context(rows, league_id=resolved_league_id)

    def trade_opportunities(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = "QB,RB,WR,TE",
        targets_per_team: int = 5,
        offers_per_team: int = 3,
    ) -> dict[str, Any]:
        if targets_per_team < 1:
            raise ValueError("targets_per_team must be at least 1")
        if offers_per_team < 1:
            raise ValueError("offers_per_team must be at least 1")

        resolved_league_id = self._require_league_id(league_id)
        resolved_roster_id = self._require_roster_id(roster_id)
        position_list = parse_positions(positions)
        if season is not None and week is not None:
            normalized = self._normalized_decision_read(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=season,
                week=week,
                positions=position_list,
            )
            if normalized.fresh and normalized.inputs is not None:
                return self._trade_opportunities_from_inputs(
                    inputs=normalized.inputs,
                    normalized=normalized,
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=season,
                    week=week,
                    positions=position_list,
                    targets_per_team=targets_per_team,
                    offers_per_team=offers_per_team,
                    fallback_used=False,
                )
        else:
            normalized = None
        with self._client() as client:
            resolved_season, resolved_week = resolve_season_week(client, season, week)
            if normalized is None:
                normalized = self._normalized_decision_read(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    positions=position_list,
                )
                if normalized.fresh and normalized.inputs is not None:
                    return self._trade_opportunities_from_inputs(
                        inputs=normalized.inputs,
                        normalized=normalized,
                        league_id=resolved_league_id,
                        roster_id=resolved_roster_id,
                        season=resolved_season,
                        week=resolved_week,
                        positions=position_list,
                        targets_per_team=targets_per_team,
                        offers_per_team=offers_per_team,
                        fallback_used=False,
                    )
            league = client.get_league(resolved_league_id)
            scoring_settings = league.get("scoring_settings") or {}
            projection_rows = fetch_rows_for_positions(
                client,
                season=resolved_season,
                week=resolved_week,
                positions=position_list,
                source="projections",
                scoring_settings=scoring_settings,
            )
            users = client.get_league_users(resolved_league_id)
            rosters = client.get_rosters(resolved_league_id)
            matchups = client.get_matchups(resolved_league_id, resolved_week)
            players = load_or_fetch_players(client, cache_path=self.players_cache)
            lineup = build_my_lineup(
                league_id=resolved_league_id,
                roster_id=resolved_roster_id,
                season=resolved_season,
                week=resolved_week,
                league=league,
                users=users,
                rosters=rosters,
                matchups=matchups,
                players=players,
                projection_rows=projection_rows,
            )
            return self._with_decision_metadata(
                build_trade_opportunities(
                    league_id=resolved_league_id,
                    roster_id=resolved_roster_id,
                    season=resolved_season,
                    week=resolved_week,
                    league=league,
                    lineup=lineup,
                    matchups=matchups,
                    users=users,
                    rosters=rosters,
                    players=players,
                    projection_rows=projection_rows,
                    positions=position_list,
                    targets_per_team=targets_per_team,
                    offers_per_team=offers_per_team,
                ),
                normalized,
                fallback_used=True,
            )

    def decision_smoke_report(
        self,
        *,
        league_id: str | None = None,
        roster_id: int | None = None,
        season: int | None = None,
        week: int | None = None,
        positions: str = DEFAULT_POSITIONS,
        per_position_limit: int = 3,
        targets_per_team: int = 2,
        offers_per_team: int = 2,
        format: str = "markdown",
    ) -> dict[str, Any] | str:
        if format not in {"json", "markdown"}:
            raise ValueError("format must be 'json' or 'markdown'")
        from sleeper_tooling.smoke import (
            build_decision_smoke_report,
            render_decision_smoke_tables,
        )

        report = build_decision_smoke_report(
            self,
            league_id=league_id,
            roster_id=roster_id,
            season=season,
            week=week,
            positions=positions,
            per_position_limit=per_position_limit,
            targets_per_team=targets_per_team,
            offers_per_team=offers_per_team,
        )
        if format == "markdown":
            return render_decision_smoke_tables(report)
        return report

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

    def _lineup_recommendations_from_inputs(
        self,
        *,
        inputs: NormalizedDecisionInputs,
        normalized: NormalizedDecisionRead,
        league_id: str,
        roster_id: int,
        season: int,
        week: int,
        positions: list[str],
        min_delta: float,
        limit: int,
        fallback_used: bool,
    ) -> dict[str, Any]:
        lineup = build_my_lineup(
            league_id=league_id,
            roster_id=roster_id,
            season=season,
            week=week,
            league=inputs.league,
            users=inputs.users,
            rosters=inputs.rosters,
            matchups=inputs.matchups,
            players=inputs.players,
            projection_rows=inputs.projection_rows,
        )
        return self._with_decision_metadata(
            build_lineup_recommendations(
                lineup=lineup,
                rosters=inputs.rosters,
                players=inputs.players,
                projection_rows=inputs.projection_rows,
                add_trends=inputs.add_trends,
                drop_trends=inputs.drop_trends,
                positions=positions,
                min_delta=min_delta,
                limit=limit,
            ),
            normalized,
            fallback_used=fallback_used,
        )

    def _waiver_by_position_from_inputs(
        self,
        *,
        inputs: NormalizedDecisionInputs,
        normalized: NormalizedDecisionRead,
        league_id: str,
        roster_id: int,
        season: int,
        week: int,
        positions: list[str],
        per_position_limit: int,
        fallback_used: bool,
    ) -> dict[str, Any]:
        projection_candidates = build_free_agent_watch(
            projection_rows=inputs.projection_rows,
            rosters=inputs.rosters,
            players=inputs.players,
            positions=positions,
        )
        trend_candidates = build_waiver_watch(
            trends=inputs.add_trends,
            players=inputs.players,
            projection_rows=inputs.projection_rows,
            rosters=inputs.rosters,
            positions=positions,
            trend_type="add",
        )
        available_candidates = merge_available_candidates(
            projection_candidates=projection_candidates,
            trend_candidates=trend_candidates,
            players=inputs.players,
            add_trends=inputs.add_trends,
            drop_trends=inputs.drop_trends,
        )
        lineup = build_my_lineup(
            league_id=league_id,
            roster_id=roster_id,
            season=season,
            week=week,
            league=inputs.league,
            users=inputs.users,
            rosters=inputs.rosters,
            matchups=inputs.matchups,
            players=inputs.players,
            projection_rows=inputs.projection_rows,
        )
        roster_players = [
            row
            for row in lineup["lineup_table"]
            if row.get("player_id") != "0"
        ]
        return self._with_decision_metadata(
            {
                "season": season,
                "week": week,
                "league_id": league_id,
                "roster_id": roster_id,
                "positions": positions,
                "per_position_limit": per_position_limit,
                "by_position": group_waiver_options_by_position(
                    candidates=available_candidates,
                    roster_players=roster_players,
                    positions=positions,
                    per_position_limit=per_position_limit,
                ),
                "evidence": [
                    "options are grouped by position and exclude rostered players",
                    "projected_gain_over_drop compares against an unprotected active roster drop candidate",
                    "FAAB hints are included only when the acquisition market is known to be waiver",
                ],
            },
            normalized,
            fallback_used=fallback_used,
        )

    def _trade_opportunities_from_inputs(
        self,
        *,
        inputs: NormalizedDecisionInputs,
        normalized: NormalizedDecisionRead,
        league_id: str,
        roster_id: int,
        season: int,
        week: int,
        positions: list[str],
        targets_per_team: int,
        offers_per_team: int,
        fallback_used: bool,
    ) -> dict[str, Any]:
        lineup = build_my_lineup(
            league_id=league_id,
            roster_id=roster_id,
            season=season,
            week=week,
            league=inputs.league,
            users=inputs.users,
            rosters=inputs.rosters,
            matchups=inputs.matchups,
            players=inputs.players,
            projection_rows=inputs.projection_rows,
        )
        return self._with_decision_metadata(
            build_trade_opportunities(
                league_id=league_id,
                roster_id=roster_id,
                season=season,
                week=week,
                league=inputs.league,
                lineup=lineup,
                matchups=inputs.matchups,
                users=inputs.users,
                rosters=inputs.rosters,
                players=inputs.players,
                projection_rows=inputs.projection_rows,
                positions=positions,
                targets_per_team=targets_per_team,
                offers_per_team=offers_per_team,
            ),
            normalized,
            fallback_used=fallback_used,
        )

    def _normalized_decision_read(
        self,
        *,
        league_id: str,
        roster_id: int,
        season: int,
        week: int,
        positions: list[str],
        recent_weeks: int = 0,
    ) -> NormalizedDecisionRead:
        return NormalizedDecisionReader(
            self._require_decision_repository(),
            max_age_seconds=NORMALIZED_DECISION_MAX_AGE_SECONDS,
        ).read_inputs(
            league_id=league_id,
            roster_id=roster_id,
            season=season,
            week=week,
            positions=positions,
            recent_weeks=recent_weeks,
        )

    def _with_decision_metadata(
        self,
        report: dict[str, Any],
        normalized: NormalizedDecisionRead | None,
        *,
        fallback_used: bool,
    ) -> dict[str, Any]:
        if normalized is None:
            freshness = {
                "status": "not_checked",
                "fresh": False,
                "last_synced_at": None,
                "max_age_seconds": NORMALIZED_DECISION_MAX_AGE_SECONDS,
                "missing_inputs": [],
                "warnings": [
                    "normalized decision data was not checked before fallback"
                ],
                "latest_sync": None,
            }
        else:
            freshness = normalized.freshness
        return {
            **report,
            "data_source": "sleeper_fallback" if fallback_used else "normalized_db",
            "freshness": freshness,
            "fallback_used": fallback_used,
            "sync_recommended": fallback_used or freshness.get("status") != "fresh",
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

    def _client(self, *, refresh_cache: bool | None = None) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        cache = ApiResponseCache(self.cache_db) if self.cache_enabled else None
        return SleeperClient(
            cache=cache,
            refresh_cache=self.refresh_cache if refresh_cache is None else refresh_cache,
        )

    def _require_trend_repository(self) -> Any:
        if self._trend_repository is None:
            self._trend_repository = build_normalized_repository(self.cache_db)
        return self._trend_repository

    def _require_decision_repository(self) -> Any:
        if self._decision_repository is None:
            self._decision_repository = build_normalized_repository(self.cache_db)
        return self._decision_repository

    def _require_sync_service(self) -> Any:
        if self._sync_service is None:
            raise ValueError(
                "sync service is required; provide an object with sync_decision_data"
            )
        return self._sync_service


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
    resolved_season = season or current_season_year()
    if week is not None:
        return resolved_season, week
    state = client.get_nfl_state()
    return resolved_season, int(state["week"])


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
