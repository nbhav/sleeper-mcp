from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def owner_display_name(user: dict[str, Any] | None) -> str:
    if not user:
        return "Unknown"
    metadata = user.get("metadata") or {}
    return metadata.get("team_name") or user.get("display_name") or user.get("username") or "Unknown"


def player_name(player: dict[str, Any] | None, player_id: str) -> str:
    if not player:
        return player_id
    full_name = player.get("full_name")
    if full_name:
        return full_name
    first = player.get("first_name") or ""
    last = player.get("last_name") or ""
    name = f"{first} {last}".strip()
    return name or player_id


def flatten_player_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        player = row.get("player") or {}
        stats = row.get("stats") or {}
        flattened.append(
            {
                "player_id": str(row.get("player_id", "")),
                "name": player.get("full_name") or player_name(player, str(row.get("player_id", ""))),
                "team": player.get("team") or "",
                "position": player.get("position") or "",
                "points": stats.get("pts_ppr")
                or stats.get("pts_half_ppr")
                or stats.get("pts_std")
                or row.get("pts_ppr")
                or row.get("points")
                or 0,
                **{f"stat_{key}": value for key, value in stats.items()},
            }
        )
    return flattened


def top_players_by_position(
    rows: Iterable[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        position = str(row.get("position") or "")
        if not position:
            continue
        grouped.setdefault(position, []).append(row)

    leaders: list[dict[str, Any]] = []
    for position, position_rows in grouped.items():
        for rank, row in enumerate(_sort_by_points(position_rows)[:limit], start=1):
            leaders.append({"position_rank": rank, **_leader_row(row)})
    return sorted(leaders, key=lambda row: (row["position"], row["position_rank"]))


def top_players_by_team(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        team = str(row.get("team") or "")
        if not team:
            continue
        grouped.setdefault(team, []).append(row)

    leaders = []
    for team, team_rows in grouped.items():
        top = _sort_by_points(team_rows)[0]
        leaders.append({"team_rank": 1, **_leader_row(top)})
    return sorted(leaders, key=lambda row: row["team"])


def flatten_player_map(players: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player_id, player in players.items():
        rows.append(
            {
                "player_id": player_id,
                "name": player_name(player, player_id),
                "team": player.get("team") or "",
                "position": player.get("position") or "",
                "fantasy_positions": ",".join(player.get("fantasy_positions") or []),
                "status": player.get("status") or "",
                "injury_status": player.get("injury_status") or "",
            }
        )
    return sorted(rows, key=lambda row: (row["position"], row["name"]))


def enrich_trending_players(
    trends: Iterable[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    *,
    trend_type: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trend in trends:
        player_id = str(trend.get("player_id") or "")
        player = players.get(player_id)
        rows.append(
            {
                "player_id": player_id,
                "name": player_name(player, player_id),
                "team": player.get("team") if player else "",
                "position": player.get("position") if player else "",
                "trend_type": trend_type,
                "count": trend.get("count", 0),
                "status": (player.get("status") or "") if player else "",
                "injury_status": (player.get("injury_status") or "") if player else "",
            }
        )
    return sorted(rows, key=lambda row: row["count"], reverse=True)


def build_league_week_report(
    *,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    matchups: list[dict[str, Any]],
    players: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    rosters_by_id = {int(roster["roster_id"]): roster for roster in rosters}
    reports: list[dict[str, Any]] = []

    for matchup in sorted(matchups, key=lambda item: (item.get("matchup_id") or 0, item.get("roster_id") or 0)):
        roster_id = int(matchup["roster_id"])
        roster = rosters_by_id.get(roster_id, {})
        owner = users_by_id.get(str(roster.get("owner_id")))
        starters = [str(player_id) for player_id in matchup.get("starters") or []]
        roster_players = [str(player_id) for player_id in matchup.get("players") or []]
        bench = [player_id for player_id in roster_players if player_id not in set(starters)]

        reports.append(
            {
                "matchup_id": matchup.get("matchup_id"),
                "roster_id": roster_id,
                "owner_id": roster.get("owner_id"),
                "team_name": owner_display_name(owner),
                "points": matchup.get("points", 0),
                "starters": [
                    _player_summary(player_id, players, matchup.get("players_points"))
                    for player_id in starters
                ],
                "bench": [
                    _player_summary(player_id, players, matchup.get("players_points"))
                    for player_id in bench
                ],
            }
        )

    return reports


def summarize_league_week(report: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "matchup_id": row["matchup_id"],
            "roster_id": row["roster_id"],
            "team_name": row["team_name"],
            "points": row["points"],
        }
        for row in report
    ]


def _player_summary(
    player_id: str,
    players: dict[str, dict[str, Any]] | None,
    player_points: dict[str, Any] | None,
) -> dict[str, Any]:
    player = players.get(player_id) if players else None
    return {
        "player_id": player_id,
        "name": player_name(player, player_id),
        "team": player.get("team") if player else "",
        "position": player.get("position") if player else "",
        "points": (player_points or {}).get(player_id, 0),
    }


def _sort_by_points(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: float(row.get("points") or 0), reverse=True)


def _leader_row(row: dict[str, Any]) -> dict[str, Any]:
    leader = {
        "player_id": row.get("player_id", ""),
        "name": row.get("name", ""),
        "team": row.get("team", ""),
        "position": row.get("position", ""),
        "points": row.get("points", 0),
    }
    if "sleeper_points" in row:
        leader["sleeper_points"] = row["sleeper_points"]
    if "scoring_rules_matched" in row:
        leader["scoring_rules_matched"] = row["scoring_rules_matched"]
    return leader
