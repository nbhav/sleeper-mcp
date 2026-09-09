from __future__ import annotations

import math
from typing import Any

from sleeper_tooling.reports import owner_display_name, player_name

DEFAULT_STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"]
NON_STARTER_SLOTS = {"BN", "BE", "IR", "TAXI"}
POSITION_SLOTS = {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP"}
FLEX_SLOT_POSITIONS = {
    "FLEX": {"RB", "WR", "TE"},
    "W/R/T": {"RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WR/TE": {"WR", "TE"},
    "WR/RB": {"WR", "RB"},
    "WRRB_FLEX": {"WR", "RB"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
}


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


def build_my_lineup(
    *,
    league_id: str,
    roster_id: int,
    season: int,
    week: int,
    league: dict[str, Any],
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    matchups: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    roster = next(
        (row for row in rosters if int(row.get("roster_id", 0)) == roster_id),
        {},
    )
    owner = users_by_id.get(str(roster.get("owner_id")))
    matchup = next(
        (row for row in matchups if int(row.get("roster_id", 0)) == roster_id),
        {},
    )
    slots = starter_slots(league)
    projections_by_player = {str(row.get("player_id")): row for row in projection_rows}
    player_points = matchup.get("players_points") or {}
    starter_ids = [str(player_id) for player_id in matchup.get("starters") or []]
    roster_player_ids = [
        str(player_id)
        for player_id in matchup.get("players") or roster.get("players") or []
    ]
    starter_id_set = set(starter_ids)
    bench_ids = [player_id for player_id in roster_player_ids if player_id not in starter_id_set]

    starters = [
        player_lineup_summary(
            player_id,
            players=players,
            projections_by_player=projections_by_player,
            player_points=player_points,
            slot=slots[index] if index < len(slots) else f"STARTER_{index + 1}",
            lineup_status="starter",
        )
        for index, player_id in enumerate(starter_ids)
    ]
    bench = [
        player_lineup_summary(
            player_id,
            players=players,
            projections_by_player=projections_by_player,
            player_points=player_points,
            slot="BN",
            lineup_status="bench",
        )
        for player_id in bench_ids
    ]

    return {
        "league_id": league_id,
        "roster_id": roster_id,
        "owner_id": roster.get("owner_id"),
        "team_name": owner_display_name(owner),
        "season": season,
        "week": week,
        "lineup_found": bool(matchup),
        "roster_slots": slots,
        "starter_count": len(starters),
        "bench_count": len(bench),
        "points_so_far": matchup.get("points", 0),
        "projected_starter_points": round(
            sum(float(row.get("projected_points") or 0) for row in starters),
            2,
        ),
        "starters": starters,
        "bench": bench,
    }


def build_lineup_recommendations(
    *,
    lineup: dict[str, Any],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    add_trends: list[dict[str, Any]],
    drop_trends: list[dict[str, Any]],
    positions: list[str],
    min_delta: float,
    limit: int,
) -> dict[str, Any]:
    starters = [row for row in lineup.get("starters", []) if row.get("player_id") != "0"]
    bench = [row for row in lineup.get("bench", []) if row.get("player_id") != "0"]
    roster_players = starters + bench
    projection_candidates = build_free_agent_watch(
        projection_rows=projection_rows,
        rosters=rosters,
        players=players,
        positions=positions,
    )
    trend_candidates = build_waiver_watch(
        trends=add_trends,
        players=players,
        projection_rows=projection_rows,
        rosters=rosters,
        positions=positions,
        trend_type="add",
    )
    available_candidates = merge_available_candidates(
        projection_candidates=projection_candidates,
        trend_candidates=trend_candidates,
        players=players,
        add_trends=add_trends,
        drop_trends=drop_trends,
    )

    start_sit = sorted(
        [
            recommendation
            for starter in starters
            for recommendation in eligible_roster_recommendations(
                starter=starter,
                bench=bench,
                min_delta=min_delta,
            )
        ],
        key=lambda row: float(row.get("projected_gain") or 0),
        reverse=True,
    )[:limit]

    waiver_comparisons = sorted(
        [
            compare_available_player(candidate, roster_players)
            for candidate in available_candidates
        ],
        key=lambda row: (
            float(row.get("priority_score") or 0),
            float(row.get("projected_gain_over_drop") or 0),
        ),
        reverse=True,
    )[:limit]

    watchlist = sorted(
        [
            watchlist_row(candidate)
            for candidate in available_candidates
            if int(candidate.get("add_trend_count") or 0) > 0
            or float(candidate.get("projected_points") or 0) > 0
        ],
        key=lambda row: float(row.get("priority_score") or 0),
        reverse=True,
    )[:limit]

    return {
        "league_id": lineup.get("league_id"),
        "roster_id": lineup.get("roster_id"),
        "season": lineup.get("season"),
        "week": lineup.get("week"),
        "team_name": lineup.get("team_name"),
        "current_lineup": lineup,
        "start_sit": start_sit,
        "waiver_comparisons": waiver_comparisons,
        "watchlist": watchlist,
        "evidence": [
            "starter and bench comparisons use projected_points under league scoring",
            "waiver comparisons exclude players already rostered in the league",
            "priority_score combines projection, projected roster gain, normalized add/drop momentum, and rostered percentage when present",
            "Sleeper player metadata does not always expose global rostered percentage",
        ],
    }


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


def starter_slots(league: dict[str, Any]) -> list[str]:
    configured = [
        str(slot).upper()
        for slot in league.get("roster_positions") or []
        if str(slot).upper() not in NON_STARTER_SLOTS
    ]
    return configured or DEFAULT_STARTER_SLOTS


def player_lineup_summary(
    player_id: str,
    *,
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
    player_points: dict[str, Any],
    slot: str,
    lineup_status: str,
) -> dict[str, Any]:
    player = players.get(str(player_id), {})
    projection = projections_by_player.get(str(player_id), {})
    return {
        "slot": slot,
        "lineup_status": lineup_status,
        "player_id": str(player_id),
        "name": player_name(player, str(player_id)),
        "team": player.get("team") or projection.get("team") or "",
        "position": player.get("position") or projection.get("position") or "",
        "fantasy_positions": player.get("fantasy_positions") or [],
        "points_so_far": (player_points or {}).get(str(player_id), 0),
        "projected_points": projection.get("points", 0),
        "sleeper_projected_points": projection.get("sleeper_points", ""),
        "status": player.get("status") or "",
        "injury_status": player.get("injury_status") or "",
        "rostered_percent": rostered_percent(player),
    }


def eligible_roster_recommendations(
    *,
    starter: dict[str, Any],
    bench: list[dict[str, Any]],
    min_delta: float,
) -> list[dict[str, Any]]:
    slot = str(starter.get("slot") or "")
    starter_points = float(starter.get("projected_points") or 0)
    rows = []
    for candidate in bench:
        if not is_player_eligible_for_slot(candidate, slot):
            continue
        candidate_points = float(candidate.get("projected_points") or 0)
        projected_gain = round(candidate_points - starter_points, 2)
        if projected_gain < min_delta:
            continue
        rows.append(
            {
                "action": "start",
                "slot": slot,
                "start_player_id": candidate.get("player_id"),
                "start_name": candidate.get("name"),
                "start_position": candidate.get("position"),
                "start_team": candidate.get("team"),
                "start_projected_points": candidate_points,
                "sit_player_id": starter.get("player_id"),
                "sit_name": starter.get("name"),
                "sit_position": starter.get("position"),
                "sit_team": starter.get("team"),
                "sit_projected_points": starter_points,
                "projected_gain": projected_gain,
                "evidence": [
                    f"{candidate.get('name')} is eligible for {slot}",
                    "recommendation is based on projected point delta",
                ],
            }
        )
    return rows


def merge_available_candidates(
    *,
    projection_candidates: list[dict[str, Any]],
    trend_candidates: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    add_trends: list[dict[str, Any]],
    drop_trends: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    add_counts = trend_counts(add_trends)
    drop_counts = trend_counts(drop_trends)
    by_player = {str(row.get("player_id")): dict(row) for row in projection_candidates}
    for row in trend_candidates:
        player_id = str(row.get("player_id") or "")
        if player_id:
            by_player[player_id] = {**by_player.get(player_id, {}), **row}
    merged = []
    for player_id, row in by_player.items():
        player = players.get(player_id, {})
        add_count = add_counts.get(player_id, 0)
        drop_count = drop_counts.get(player_id, 0)
        rostered_pct = rostered_percent(player)
        projected_points = float(row.get("projected_points") or 0)
        priority_score = projected_points + trend_priority_boost(add_count - drop_count)
        if rostered_pct is not None:
            priority_score += rostered_pct / 20
        merged.append(
            {
                **row,
                "add_trend_count": add_count,
                "drop_trend_count": drop_count,
                "net_trend_count": add_count - drop_count,
                "rostered_percent": rostered_pct,
                "market_type": "waiver_trending" if add_count else "free_agent_projection",
                "priority_score": round(priority_score, 2),
            }
        )
    return merged


def compare_available_player(
    candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    comparable_roster_players = [
        row
        for row in roster_players
        if same_position_family(candidate, row)
    ]
    drop_candidate = min(
        comparable_roster_players or roster_players,
        key=lambda row: float(row.get("projected_points") or 0),
        default={},
    )
    projected_points = float(candidate.get("projected_points") or 0)
    drop_points = float(drop_candidate.get("projected_points") or 0)
    projected_gain = round(projected_points - drop_points, 2)
    priority_score = round(
        float(candidate.get("priority_score") or 0) + max(projected_gain, 0) * 1.5,
        2,
    )
    return {
        "action": "add" if projected_gain > 0 else "watch",
        "add_player_id": candidate.get("player_id"),
        "add_name": candidate.get("name"),
        "add_position": candidate.get("position"),
        "add_team": candidate.get("team"),
        "add_projected_points": projected_points,
        "drop_player_id": drop_candidate.get("player_id", ""),
        "drop_name": drop_candidate.get("name", ""),
        "drop_position": drop_candidate.get("position", ""),
        "drop_team": drop_candidate.get("team", ""),
        "drop_projected_points": drop_points,
        "projected_gain_over_drop": projected_gain,
        "market_type": candidate.get("market_type"),
        "add_trend_count": candidate.get("add_trend_count", 0),
        "drop_trend_count": candidate.get("drop_trend_count", 0),
        "net_trend_count": candidate.get("net_trend_count", 0),
        "rostered_percent": candidate.get("rostered_percent"),
        "faab_bid_pct": faab_bid_pct(projected_gain, candidate),
        "priority_score": priority_score,
    }


def watchlist_row(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": candidate.get("player_id"),
        "name": candidate.get("name"),
        "team": candidate.get("team"),
        "position": candidate.get("position"),
        "projected_points": candidate.get("projected_points", 0),
        "add_trend_count": candidate.get("add_trend_count", 0),
        "drop_trend_count": candidate.get("drop_trend_count", 0),
        "net_trend_count": candidate.get("net_trend_count", 0),
        "rostered_percent": candidate.get("rostered_percent"),
        "market_type": candidate.get("market_type"),
        "priority_score": candidate.get("priority_score", 0),
        "status": candidate.get("status", ""),
        "injury_status": candidate.get("injury_status", ""),
    }


def trend_counts(trends: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(row.get("player_id")): int(row.get("count") or 0)
        for row in trends
        if row.get("player_id")
    }


def same_position_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_position = str(left.get("position") or "").upper()
    right_position = str(right.get("position") or "").upper()
    if left_position == right_position:
        return True
    return left_position in {"RB", "WR", "TE"} and right_position in {"RB", "WR", "TE"}


def is_player_eligible_for_slot(player: dict[str, Any], slot: str) -> bool:
    normalized_slot = slot.upper()
    positions = {
        str(position).upper()
        for position in player.get("fantasy_positions") or []
        if position
    }
    if player.get("position"):
        positions.add(str(player["position"]).upper())
    if normalized_slot in FLEX_SLOT_POSITIONS:
        return bool(positions & FLEX_SLOT_POSITIONS[normalized_slot])
    if normalized_slot in POSITION_SLOTS:
        return normalized_slot in positions
    return normalized_slot in positions


def rostered_percent(player: dict[str, Any]) -> float | None:
    for key in (
        "rostered_percent",
        "rostered_pct",
        "percent_rostered",
        "percent_owned",
        "owned_percent",
    ):
        value = player.get(key)
        if isinstance(value, int | float):
            return round(float(value), 2)
        if isinstance(value, str) and value.strip():
            try:
                return round(float(value), 2)
            except ValueError:
                continue
    return None


def faab_bid_pct(projected_gain: float, candidate: dict[str, Any]) -> int:
    net_trend_count = int(candidate.get("net_trend_count") or 0)
    rostered_pct = float(candidate.get("rostered_percent") or 0)
    if projected_gain >= 6:
        return 12
    if projected_gain >= 3:
        return 7
    if projected_gain >= 1:
        return 3
    if net_trend_count >= 1000 or rostered_pct >= 40:
        return 3
    if net_trend_count > 0:
        return 1
    return 0


def trend_priority_boost(net_trend_count: int) -> float:
    if net_trend_count == 0:
        return 0
    direction = 1 if net_trend_count > 0 else -1
    return round(direction * math.log10(abs(net_trend_count) + 1), 2)


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
