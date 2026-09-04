from __future__ import annotations

from typing import Any

from sleeper_tooling.reports import owner_display_name, player_name


def build_waiver_watch(
    *,
    trends: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    positions: list[str],
    trend_type: str,
) -> list[dict[str, Any]]:
    rostered = rostered_player_ids(rosters)
    projections_by_player = {str(row.get("player_id")): row for row in projection_rows}
    allowed_positions = {position.upper() for position in positions}
    rows: list[dict[str, Any]] = []

    for trend in trends:
        player_id = str(trend.get("player_id") or "")
        if not player_id or player_id in rostered:
            continue

        player = players.get(player_id, {})
        projection = projections_by_player.get(player_id, {})
        position = str(player.get("position") or projection.get("position") or "")
        if allowed_positions and position.upper() not in allowed_positions:
            continue

        rows.append(
            {
                "player_id": player_id,
                "name": player_name(player, player_id),
                "team": player.get("team") or projection.get("team") or "",
                "position": position,
                "trend_type": trend_type,
                "trend_count": trend.get("count", 0),
                "projected_points": projection.get("points", 0),
                "sleeper_projected_points": projection.get("sleeper_points", ""),
                "status": player.get("status") or "",
                "injury_status": player.get("injury_status") or "",
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            float(row.get("projected_points") or 0),
            int(row.get("trend_count") or 0),
        ),
        reverse=True,
    )


def build_injury_watch(
    *,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    rows: list[dict[str, Any]] = []

    for roster in rosters:
        owner = users_by_id.get(str(roster.get("owner_id")))
        for player_id in roster.get("players") or []:
            player = players.get(str(player_id), {})
            if not is_injury_relevant(player):
                continue
            rows.append(
                {
                    "roster_id": roster.get("roster_id"),
                    "owner_id": roster.get("owner_id"),
                    "team_name": owner_display_name(owner),
                    "player_id": str(player_id),
                    "name": player_name(player, str(player_id)),
                    "team": player.get("team") or "",
                    "position": player.get("position") or "",
                    "status": player.get("status") or "",
                    "injury_status": player.get("injury_status") or "",
                }
            )

    return sorted(
        rows,
        key=lambda row: (
            str(row["team_name"]),
            str(row["position"]),
            str(row["name"]),
        ),
    )


def build_free_agent_watch(
    *,
    projection_rows: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    positions: list[str],
) -> list[dict[str, Any]]:
    rostered = rostered_player_ids(rosters)
    allowed_positions = {position.upper() for position in positions}
    rows: list[dict[str, Any]] = []

    for projection in projection_rows:
        player_id = str(projection.get("player_id") or "")
        if not player_id or player_id in rostered:
            continue
        player = players.get(player_id, {})
        position = str(player.get("position") or projection.get("position") or "")
        if allowed_positions and position.upper() not in allowed_positions:
            continue
        rows.append(
            {
                "player_id": player_id,
                "name": player_name(player, player_id),
                "team": player.get("team") or projection.get("team") or "",
                "position": position,
                "projected_points": projection.get("points", 0),
                "sleeper_projected_points": projection.get("sleeper_points", ""),
                "status": player.get("status") or "",
                "injury_status": player.get("injury_status") or "",
            }
        )

    return sorted(rows, key=lambda row: float(row.get("projected_points") or 0), reverse=True)


def build_opponent_watch(
    *,
    roster_id: int,
    week: int,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    matchups: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    rosters_by_id = {int(roster["roster_id"]): roster for roster in rosters}
    projections_by_player = {str(row.get("player_id")): row for row in projection_rows}
    my_matchup = next((matchup for matchup in matchups if int(matchup.get("roster_id", 0)) == roster_id), None)
    if not my_matchup:
        return {"roster_id": roster_id, "week": week, "opponent_found": False}

    matchup_id = my_matchup.get("matchup_id")
    opponent_matchup = next(
        (
            matchup
            for matchup in matchups
            if matchup.get("matchup_id") == matchup_id
            and int(matchup.get("roster_id", 0)) != roster_id
        ),
        None,
    )
    if not opponent_matchup:
        return {"roster_id": roster_id, "week": week, "matchup_id": matchup_id, "opponent_found": False}

    opponent_roster_id = int(opponent_matchup["roster_id"])
    opponent_roster = rosters_by_id.get(opponent_roster_id, {})
    opponent_owner = users_by_id.get(str(opponent_roster.get("owner_id")))
    starters = [
        _player_projection_summary(player_id, players, projections_by_player)
        for player_id in opponent_matchup.get("starters") or []
    ]
    injuries = [
        row
        for row in starters
        if row.get("injury_status") or str(row.get("status") or "").lower() != "active"
    ]

    return {
        "roster_id": roster_id,
        "week": week,
        "matchup_id": matchup_id,
        "opponent_found": True,
        "opponent_roster_id": opponent_roster_id,
        "opponent_team_name": owner_display_name(opponent_owner),
        "opponent_points_so_far": opponent_matchup.get("points", 0),
        "opponent_projected_starter_points": round(
            sum(float(row.get("projected_points") or 0) for row in starters),
            2,
        ),
        "opponent_starters": starters,
        "opponent_injuries": injuries,
    }


def build_league_team_watch(
    *,
    week: int,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    roster_owner_by_id = {
        int(roster["roster_id"]): users_by_id.get(str(roster.get("owner_id")))
        for roster in rosters
    }
    rows: list[dict[str, Any]] = []

    for transaction in transactions:
        if transaction.get("status") != "complete":
            continue
        adds = _transaction_players(transaction.get("adds") or {}, players, roster_owner_by_id)
        drops = _transaction_players(transaction.get("drops") or {}, players, roster_owner_by_id)
        rows.append(
            {
                "week": week,
                "transaction_id": transaction.get("transaction_id"),
                "type": transaction.get("type"),
                "status": transaction.get("status"),
                "created": transaction.get("created"),
                "roster_ids": transaction.get("roster_ids") or [],
                "adds": adds,
                "drops": drops,
                "adds_summary": ", ".join(player["name"] for player in adds),
                "drops_summary": ", ".join(player["name"] for player in drops),
            }
        )

    return sorted(rows, key=lambda row: row.get("created") or 0, reverse=True)


def is_injury_relevant(player: dict[str, Any]) -> bool:
    injury_status = player.get("injury_status")
    if injury_status:
        return True
    status = str(player.get("status") or "")
    return bool(status and status.lower() != "active")


def rostered_player_ids(rosters: list[dict[str, Any]]) -> set[str]:
    player_ids: set[str] = set()
    for roster in rosters:
        player_ids.update(
            str(player_id)
            for player_id in roster.get("players") or []
            if player_id is not None
        )
    return player_ids


def _player_projection_summary(
    player_id: str,
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    player = players.get(str(player_id), {})
    projection = projections_by_player.get(str(player_id), {})
    return {
        "player_id": str(player_id),
        "name": player_name(player, str(player_id)),
        "team": player.get("team") or projection.get("team") or "",
        "position": player.get("position") or projection.get("position") or "",
        "projected_points": projection.get("points", 0),
        "status": player.get("status") or "",
        "injury_status": player.get("injury_status") or "",
    }


def _transaction_players(
    player_to_roster: dict[str, Any],
    players: dict[str, dict[str, Any]],
    roster_owner_by_id: dict[int, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player_id, roster_id in player_to_roster.items():
        player = players.get(str(player_id), {})
        owner = roster_owner_by_id.get(int(roster_id)) if roster_id is not None else None
        rows.append(
            {
                "player_id": str(player_id),
                "name": player_name(player, str(player_id)),
                "team": player.get("team") or "",
                "position": player.get("position") or "",
                "roster_id": roster_id,
                "team_name": owner_display_name(owner),
            }
        )
    return rows
