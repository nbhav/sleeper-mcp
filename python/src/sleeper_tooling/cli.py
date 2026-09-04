from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal, Optional

import typer

from sleeper_tooling.client import SleeperApiError, SleeperClient, load_or_fetch_players
from sleeper_tooling.decision_reports import build_injury_watch, build_waiver_watch
from sleeper_tooling.db import ApiResponseCache
from sleeper_tooling.output import OutputFormat, emit
from sleeper_tooling.reports import (
    build_league_week_report,
    enrich_trending_players,
    flatten_player_map,
    flatten_player_rows,
    summarize_league_week,
    top_players_by_position,
    top_players_by_team,
)
from sleeper_tooling.scoring import flatten_scored_player_rows

app = typer.Typer(no_args_is_help=True, help="Pull fantasy football data from Sleeper.")
StatSource = Literal["stats", "projections"]
_CACHE_DB_PATH: Path | None = None
_CACHE_ENABLED = True
_REFRESH_CACHE = False


def output_option() -> OutputFormat:
    return "table"


@app.callback()
def main(
    cache_db: Annotated[Optional[Path], typer.Option("--cache-db", help="SQLite API cache path.")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the SQLite API cache.")] = False,
    refresh_cache: Annotated[bool, typer.Option("--refresh-cache", help="Fetch live data and update cached responses.")] = False,
) -> None:
    """Configure shared CLI options."""
    global _CACHE_DB_PATH, _CACHE_ENABLED, _REFRESH_CACHE
    _CACHE_DB_PATH = cache_db
    _CACHE_ENABLED = not no_cache
    _REFRESH_CACHE = refresh_cache


@app.command()
def user(
    username_or_id: Annotated[str, typer.Argument(help="Sleeper username or user_id.")],
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Fetch a Sleeper user."""
    with client_or_exit() as client:
        emit(client.get_user(username_or_id), output_format=output)


@app.command()
def leagues(
    user_id: Annotated[str, typer.Argument(help="Sleeper user_id.")],
    season: Annotated[int, typer.Option("--season", "-s")],
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """List a user's NFL leagues for a season."""
    with client_or_exit() as client:
        emit(client.get_user_leagues(user_id, season), output_format=output)


@app.command()
def league(
    league_id: Annotated[str, typer.Argument(help="Sleeper league_id.")],
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Fetch league settings and metadata."""
    with client_or_exit() as client:
        emit(client.get_league(league_id), output_format=output)


@app.command()
def rosters(
    league_id: Annotated[str, typer.Argument(help="Sleeper league_id.")],
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Fetch rosters for a league."""
    with client_or_exit() as client:
        emit(client.get_rosters(league_id), output_format=output)


@app.command()
def matchups(
    league_id: Annotated[str, typer.Argument(help="Sleeper league_id.")],
    week: Annotated[int, typer.Argument(help="NFL week.")],
    enrich: Annotated[bool, typer.Option("--enrich", help="Join users, rosters, and cached player names.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Include starter and bench player detail in reports.")] = False,
    players_cache: Annotated[Optional[Path], typer.Option("--players-cache")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Fetch weekly matchups, optionally enriched into team reports."""
    with client_or_exit() as client:
        raw_matchups = client.get_matchups(league_id, week)
        if not enrich:
            emit(raw_matchups, output_format=output)
            return
        players = load_or_fetch_players(client, cache_path=players_cache)
        report = build_league_week_report(
            users=client.get_league_users(league_id),
            rosters=client.get_rosters(league_id),
            matchups=raw_matchups,
            players=players,
        )
        emit(report if full else summarize_league_week(report), output_format=output)


@app.command()
def players(
    position: Annotated[Optional[str], typer.Option("--position", "-p")] = None,
    active: Annotated[bool, typer.Option("--active")] = False,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    cache_path: Annotated[Optional[Path], typer.Option("--cache-path")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Fetch or cache Sleeper's player map. Use sparingly."""
    with client_or_exit() as client:
        player_map = load_or_fetch_players(
            client,
            cache_path=cache_path,
            position=position,
            active=active or None,
        )
        data = player_map if output == "json" else flatten_player_map(player_map)
        emit(data[:limit] if limit and isinstance(data, list) else data, output_format=output)


@app.command()
def trending(
    trend_type: Annotated[str, typer.Argument(help="add or drop.")],
    lookback_hours: Annotated[int, typer.Option("--lookback-hours")] = 24,
    limit: Annotated[int, typer.Option("--limit")] = 25,
    enrich: Annotated[bool, typer.Option("--enrich", help="Join player names, teams, positions, and injuries.")] = False,
    players_cache: Annotated[Optional[Path], typer.Option("--players-cache")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Fetch players trending by adds or drops."""
    if trend_type not in {"add", "drop"}:
        typer.echo("trend_type must be 'add' or 'drop'", err=True)
        raise typer.Exit(2)
    with client_or_exit() as client:
        trends = client.get_trending_players(
            trend_type, lookback_hours=lookback_hours, limit=limit
        )
        if enrich:
            players = load_or_fetch_players(client, cache_path=players_cache)
            emit(
                enrich_trending_players(trends, players, trend_type=trend_type),
                output_format=output,
            )
            return
        emit(trends, output_format=output)


@app.command()
def stats(
    season: Annotated[int, typer.Option("--season", "-s")],
    week: Annotated[Optional[int], typer.Option("--week", "-w")] = None,
    position: Annotated[Optional[str], typer.Option("--position", "-p")] = None,
    order_by: Annotated[Optional[str], typer.Option("--order-by")] = None,
    league_id: Annotated[Optional[str], typer.Option("--league-id", help="Use this league's scoring settings.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Fetch season or weekly player stats."""
    with client_or_exit() as client:
        raw_rows = client.get_stats(season, week=week, position=position, order_by=order_by)
        rows = flatten_rows(raw_rows, get_league_scoring_settings(client, league_id))
        rows = filter_exact_position(rows, position)
        emit(rows[:limit] if limit else rows, output_format=output)


@app.command()
def projections(
    season: Annotated[int, typer.Option("--season", "-s")],
    week: Annotated[Optional[int], typer.Option("--week", "-w")] = None,
    position: Annotated[Optional[str], typer.Option("--position", "-p")] = None,
    order_by: Annotated[Optional[str], typer.Option("--order-by")] = None,
    league_id: Annotated[Optional[str], typer.Option("--league-id", help="Use this league's scoring settings.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit")] = None,
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Fetch season or weekly player projections."""
    with client_or_exit() as client:
        raw_rows = client.get_projections(
            season, week=week, position=position, order_by=order_by
        )
        rows = flatten_rows(raw_rows, get_league_scoring_settings(client, league_id))
        rows = filter_exact_position(rows, position)
        emit(rows[:limit] if limit else rows, output_format=output)


@app.command("cache-info")
def cache_info(
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Show API response cache stats."""
    cache = build_cache()
    try:
        emit(cache.stats(), output_format=output)
    finally:
        cache.close()


@app.command("cache-clear")
def cache_clear(
    expired_only: Annotated[bool, typer.Option("--expired-only", help="Only delete expired cache rows.")] = False,
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Clear API response cache rows."""
    cache = build_cache()
    try:
        deleted = cache.clear_expired() if expired_only else cache.clear()
        emit({"deleted": deleted, **cache.stats()}, output_format=output)
    finally:
        cache.close()


@app.command("best-week")
def best_week(
    season: Annotated[Optional[int], typer.Option("--season", "-s", help="Season. Defaults to Sleeper's current season.")] = None,
    week: Annotated[Optional[int], typer.Option("--week", "-w", help="Week. Defaults to Sleeper's current week.")] = None,
    league_id: Annotated[Optional[str], typer.Option("--league-id", help="Use this league's scoring settings.")] = None,
    positions: Annotated[str, typer.Option("--positions", "-p", help="Comma-separated positions.")] = "QB,RB,WR,TE,K,DEF",
    source: Annotated[StatSource, typer.Option("--source", help="Use actual stats or projections.")] = "stats",
    order_by: Annotated[str, typer.Option("--order-by")] = "pts_ppr",
    limit: Annotated[int, typer.Option("--limit")] = 10,
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Chain state plus position stat/projection calls into one leaders report."""
    with client_or_exit() as client:
        resolved_season, resolved_week = resolve_season_week(client, season, week)
        scoring_settings = get_league_scoring_settings(client, league_id)
        rows = fetch_rows_for_positions(
            client,
            season=resolved_season,
            week=resolved_week,
            positions=parse_positions(positions),
            source=source,
            order_by=order_by,
            scoring_settings=scoring_settings,
        )
        emit(top_players_by_position(rows, limit=limit), output_format=output)


@app.command("best-by-team")
def best_by_team(
    position: Annotated[str, typer.Option("--position", "-p")] = "RB",
    season: Annotated[Optional[int], typer.Option("--season", "-s", help="Season. Defaults to Sleeper's current season.")] = None,
    week: Annotated[Optional[int], typer.Option("--week", "-w", help="Week. Defaults to Sleeper's current week.")] = None,
    league_id: Annotated[Optional[str], typer.Option("--league-id", help="Use this league's scoring settings.")] = None,
    source: Annotated[StatSource, typer.Option("--source", help="Use actual stats or projections.")] = "projections",
    order_by: Annotated[str, typer.Option("--order-by")] = "pts_ppr",
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Chain state plus one stat/projection call into top player per NFL team."""
    with client_or_exit() as client:
        resolved_season, resolved_week = resolve_season_week(client, season, week)
        scoring_settings = get_league_scoring_settings(client, league_id)
        rows = fetch_rows_for_positions(
            client,
            season=resolved_season,
            week=resolved_week,
            positions=[position],
            source=source,
            order_by=order_by,
            scoring_settings=scoring_settings,
        )
        emit(top_players_by_team(rows), output_format=output)


@app.command("weekly-briefing")
def weekly_briefing(
    season: Annotated[Optional[int], typer.Option("--season", "-s", help="Season. Defaults to Sleeper's current season.")] = None,
    week: Annotated[Optional[int], typer.Option("--week", "-w", help="Week. Defaults to Sleeper's current week.")] = None,
    league_id: Annotated[Optional[str], typer.Option("--league-id", help="Use this league's scoring settings.")] = None,
    source: Annotated[StatSource, typer.Option("--source", help="Use actual stats or projections for leaders.")] = "projections",
    positions: Annotated[str, typer.Option("--positions", "-p", help="Comma-separated positions.")] = "QB,RB,WR,TE,K,DEF",
    leader_limit: Annotated[int, typer.Option("--leader-limit")] = 5,
    trend_limit: Annotated[int, typer.Option("--trend-limit")] = 10,
    lookback_hours: Annotated[int, typer.Option("--lookback-hours")] = 24,
    players_cache: Annotated[Optional[Path], typer.Option("--players-cache")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json",
) -> None:
    """Chain state, leaders, and enriched trending adds into one report."""
    with client_or_exit() as client:
        resolved_season, resolved_week = resolve_season_week(client, season, week)
        scoring_settings = get_league_scoring_settings(client, league_id)
        player_map = load_or_fetch_players(client, cache_path=players_cache)
        leader_rows = fetch_rows_for_positions(
            client,
            season=resolved_season,
            week=resolved_week,
            positions=parse_positions(positions),
            source=source,
            order_by="pts_ppr",
            scoring_settings=scoring_settings,
        )
        trends = client.get_trending_players(
            "add", lookback_hours=lookback_hours, limit=trend_limit
        )
        emit(
            {
                "season": resolved_season,
                "week": resolved_week,
                "leader_source": source,
                "scoring_source": league_id or "sleeper_default_points",
                "leaders": top_players_by_position(leader_rows, limit=leader_limit),
                "trending_adds": enrich_trending_players(
                    trends, player_map, trend_type="add"
                ),
            },
            output_format=output,
        )


@app.command("waiver-watch")
def waiver_watch(
    league_id: Annotated[str, typer.Argument(help="Sleeper league_id.")],
    season: Annotated[Optional[int], typer.Option("--season", "-s", help="Season. Defaults to Sleeper's current season.")] = None,
    week: Annotated[Optional[int], typer.Option("--week", "-w", help="Week. Defaults to Sleeper's current week.")] = None,
    positions: Annotated[str, typer.Option("--positions", "-p", help="Comma-separated positions.")] = "QB,RB,WR,TE,K,DEF",
    trend_type: Annotated[str, typer.Option("--trend-type", help="add or drop.")] = "add",
    lookback_hours: Annotated[int, typer.Option("--lookback-hours")] = 24,
    trend_limit: Annotated[int, typer.Option("--trend-limit", help="How many trending players to inspect before filtering rostered players.")] = 100,
    limit: Annotated[int, typer.Option("--limit")] = 25,
    players_cache: Annotated[Optional[Path], typer.Option("--players-cache")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Find trending available players with projected value."""
    if trend_type not in {"add", "drop"}:
        typer.echo("trend_type must be 'add' or 'drop'", err=True)
        raise typer.Exit(2)
    with client_or_exit() as client:
        resolved_season, resolved_week = resolve_season_week(client, season, week)
        scoring_settings = get_league_scoring_settings(client, league_id)
        position_list = parse_positions(positions)
        projection_rows = fetch_rows_for_positions(
            client,
            season=resolved_season,
            week=resolved_week,
            positions=position_list,
            source="projections",
            order_by="pts_ppr",
            scoring_settings=scoring_settings,
        )
        rows = build_waiver_watch(
            trends=client.get_trending_players(
                trend_type,
                lookback_hours=lookback_hours,
                limit=trend_limit,
            ),
            players=load_or_fetch_players(client, cache_path=players_cache),
            projection_rows=projection_rows,
            rosters=client.get_rosters(league_id),
            positions=position_list,
            trend_type=trend_type,
        )
        emit(rows[:limit], output_format=output)


@app.command("injury-watch")
def injury_watch(
    league_id: Annotated[str, typer.Argument(help="Sleeper league_id.")],
    players_cache: Annotated[Optional[Path], typer.Option("--players-cache")] = Path("/data/players.json"),
    output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "table",
) -> None:
    """Show injury-relevant players currently rostered in a league."""
    with client_or_exit() as client:
        emit(
            build_injury_watch(
                users=client.get_league_users(league_id),
                rosters=client.get_rosters(league_id),
                players=load_or_fetch_players(client, cache_path=players_cache),
            ),
            output_format=output,
        )


@app.command()
def state(output: Annotated[OutputFormat, typer.Option("--output", "-o")] = "json") -> None:
    """Fetch current NFL state from Sleeper."""
    with client_or_exit() as client:
        emit(client.get_nfl_state(), output_format=output)


def parse_positions(positions: str) -> list[str]:
    return [position.strip().upper() for position in positions.split(",") if position.strip()]


def resolve_season_week(
    client: SleeperClient,
    season: int | None,
    week: int | None,
) -> tuple[int, int]:
    if season is not None and week is not None:
        return season, week
    state = client.get_nfl_state()
    return season or int(state["season"]), week or int(state["week"])


def fetch_rows_for_positions(
    client: SleeperClient,
    *,
    season: int,
    week: int,
    positions: list[str],
    source: StatSource,
    order_by: str,
    scoring_settings: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for position in positions:
        if source == "projections":
            raw_rows = client.get_projections(
                season, week=week, position=position, order_by=order_by
            )
        else:
            raw_rows = client.get_stats(
                season, week=week, position=position, order_by=order_by
            )
        rows.extend(filter_exact_position(flatten_rows(raw_rows, scoring_settings), position))
    return rows


def get_league_scoring_settings(
    client: SleeperClient,
    league_id: str | None,
) -> dict[str, object] | None:
    if not league_id:
        return None
    league = client.get_league(league_id)
    return league.get("scoring_settings") or {}


def flatten_rows(
    raw_rows: list[dict[str, object]],
    scoring_settings: dict[str, object] | None,
) -> list[dict[str, object]]:
    if scoring_settings is None:
        return flatten_player_rows(raw_rows)
    return flatten_scored_player_rows(raw_rows, scoring_settings)


def build_cache() -> ApiResponseCache:
    return ApiResponseCache(resolve_cache_db_path())


def resolve_cache_db_path() -> Path:
    if _CACHE_DB_PATH is not None:
        return _CACHE_DB_PATH
    if os.environ.get("SLEEPER_CACHE_DB"):
        return Path(os.environ["SLEEPER_CACHE_DB"])
    cache_dir = Path(os.environ.get("SLEEPER_CACHE_DIR", "/data"))
    return cache_dir / "sleeper.db"


def filter_exact_position(
    rows: list[dict[str, object]],
    position: str | None,
) -> list[dict[str, object]]:
    if not position:
        return rows
    expected = position.upper()
    return [row for row in rows if str(row.get("position") or "").upper() == expected]


def client_or_exit() -> SleeperClient:
    try:
        return SleeperClient(
            cache=build_cache() if _CACHE_ENABLED else None,
            refresh_cache=_REFRESH_CACHE,
        )
    except SleeperApiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
