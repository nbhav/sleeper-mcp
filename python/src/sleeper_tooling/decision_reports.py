from __future__ import annotations

import math
from collections import Counter
from typing import Any

from sleeper_tooling.reports import owner_display_name, player_name

DEFAULT_STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"]
NON_STARTER_SLOTS = {"BN", "BE", "IR", "TAXI"}
POSITION_SLOTS = {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP"}
SKILL_POSITIONS = {"RB", "WR", "TE"}
KNOWN_MARKET_TYPES = {"free_agent", "waiver", "unknown"}
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
                **player_context(
                    player_id,
                    players=players,
                    projection=projection,
                    market_hint=infer_market_type(player, row=trend, default="unknown"),
                ),
                "position": position,
                "trend_type": trend_type,
                "trend_count": trend.get("count", 0),
                "acquisition_action": "watch",
                "projected_points": projection.get("points", 0),
                "sleeper_projected_points": projection.get("sleeper_points", ""),
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
    reserve_ids = ordered_player_ids(roster.get("reserve") or [])
    roster_player_ids = ordered_player_ids(
        list(matchup.get("players") or roster.get("players") or []) + list(roster.get("players") or []) + reserve_ids
    )
    starter_id_set = set(starter_ids)
    reserve_id_set = set(reserve_ids)
    bench_ids = [
        player_id
        for player_id in roster_player_ids
        if player_id not in starter_id_set and player_id not in reserve_id_set
    ]

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
    reserve = [
        player_lineup_summary(
            player_id,
            players=players,
            projections_by_player=projections_by_player,
            player_points=player_points,
            slot="IR",
            lineup_status="reserve",
        )
        for player_id in reserve_ids
        if player_id not in starter_id_set
    ]
    lineup_table = starters + bench + reserve
    current_total = matchup.get("points", 0)
    projected_starter_total = round(
        sum(float(row.get("projected_points") or 0) for row in starters),
        2,
    )
    projected_total = round(
        sum(float(row.get("projected_points") or 0) for row in lineup_table),
        2,
    )
    bye_week_warnings = bye_pressure_warnings(lineup_table)

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
        "active_bench_count": len(bench),
        "reserve_count": len(reserve),
        "current_total": current_total,
        "points_so_far": current_total,
        "projected_total": projected_total,
        "projected_starter_total": projected_starter_total,
        "projected_starter_points": projected_starter_total,
        "bye_week_warnings": bye_week_warnings,
        "lineup_table": lineup_table,
        "starters": starters,
        "bench": bench,
        "reserve": reserve,
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
    bench = [
        row
        for row in lineup.get("bench", [])
        if row.get("player_id") != "0" and row.get("active_roster_spot", True)
    ]
    reserve = [row for row in lineup.get("reserve", []) if row.get("player_id") != "0"]
    roster_players = starters + bench + reserve
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
            row
            for candidate in available_candidates
            for row in [compare_available_player(candidate, roster_players)]
            if float(row.get("projected_gain_over_drop") or 0) > 0
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
            "reserve and last-playable backup protections are applied before choosing drop candidates",
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
                **player_context(
                    player_id,
                    players=players,
                    projection=projection,
                    market_hint=infer_market_type(player, row=projection, default="free_agent"),
                ),
                "position": position,
                "acquisition_action": "add_now",
                "projected_points": projection.get("points", 0),
                "sleeper_projected_points": projection.get("sleeper_points", ""),
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


def ordered_player_ids(player_ids: list[Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for player_id in player_ids:
        if player_id is None:
            continue
        normalized = str(player_id)
        if normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def starter_slots(league: dict[str, Any]) -> list[str]:
    configured = [
        str(slot).upper()
        for slot in league.get("roster_positions") or []
        if str(slot).upper() not in NON_STARTER_SLOTS
    ]
    return configured or DEFAULT_STARTER_SLOTS


def player_context(
    player_id: str,
    *,
    players: dict[str, dict[str, Any]],
    projection: dict[str, Any] | None = None,
    market_hint: str | None = None,
) -> dict[str, Any]:
    player = players.get(str(player_id), {})
    projection = projection or {}
    bye_week = first_present(player, "bye_week", "bye")
    context_sources = ["sleeper_players"]
    if projection:
        context_sources.append("sleeper_projections")
    return {
        "team": player.get("team") or projection.get("team") or "",
        "position": player.get("position") or projection.get("position") or "",
        "fantasy_positions": player.get("fantasy_positions") or [],
        "status": player.get("status") or "",
        "injury_status": player.get("injury_status") or "",
        "depth_chart_order": first_present(player, "depth_chart_order"),
        "depth_chart_position": first_present(player, "depth_chart_position"),
        "bye_week": bye_week,
        "market_type": normalize_market_type(market_hint),
        "context_sources": context_sources,
        "source_metadata": {
            "player_context": context_sources,
            "market": "sleeper_explicit" if market_hint in KNOWN_MARKET_TYPES - {"unknown"} else "unknown",
            "bye_week": "sleeper_players" if bye_week not in (None, "") else "missing",
        },
    }


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return ""


def infer_market_type(
    player: dict[str, Any],
    *,
    row: dict[str, Any],
    default: str,
) -> str:
    for source in (row, player):
        explicit = first_present(
            source,
            "market_type",
            "acquisition_market",
            "acquisition_type",
        )
        normalized = normalize_market_type(explicit)
        if normalized != "unknown":
            return normalized
        waiver_value = source.get("waiver") or source.get("is_waiver")
        if waiver_value is True:
            return "waiver"
        if waiver_value is False:
            return "free_agent"
    return normalize_market_type(default)


def normalize_market_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"fa", "free-agent", "free agent", "free_agent"}:
        return "free_agent"
    if normalized in {"waiver", "waivers", "claim"}:
        return "waiver"
    if normalized in KNOWN_MARKET_TYPES:
        return normalized
    return "unknown"


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
    actual_points = (player_points or {}).get(str(player_id), 0)
    active_roster_spot = lineup_status in {"starter", "bench"}
    stash_value = lineup_status == "reserve"
    return {
        "slot": slot,
        "lineup_status": lineup_status,
        "player_id": str(player_id),
        "name": player_name(player, str(player_id)),
        **player_context(
            player_id,
            players=players,
            projection=projection,
            market_hint=infer_market_type(player, row=projection, default="unknown"),
        ),
        "actual_points": actual_points,
        "points_so_far": actual_points,
        "projected_points": projection.get("points", 0),
        "sleeper_projected_points": projection.get("sleeper_points", ""),
        "rostered_percent": rostered_percent(player),
        "active_roster_spot": active_roster_spot,
        "stash_value": stash_value,
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
        market_type = infer_market_type(
            player,
            row=row,
            default="unknown" if add_count else "free_agent",
        )
        merged.append(
            {
                **row,
                "add_trend_count": add_count,
                "drop_trend_count": drop_count,
                "net_trend_count": add_count - drop_count,
                "rostered_percent": rostered_pct,
                "market_type": market_type,
                "acquisition_action": acquisition_action(market_type, projected_gain=0),
                "priority_score": round(priority_score, 2),
            }
        )
    return merged


def compare_available_player(
    candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    drop_candidate, drop_reason, rejected_drops = best_drop_candidate(candidate, roster_players)
    projected_points = float(candidate.get("projected_points") or 0)
    drop_points = float(drop_candidate.get("projected_points") or 0)
    projected_gain = round(projected_points - drop_points, 2)
    market_type = normalize_market_type(candidate.get("market_type"))
    action = acquisition_action(market_type, projected_gain=projected_gain)
    bye_warnings = move_bye_warnings(candidate, drop_candidate, roster_players)
    priority_score = round(
        float(candidate.get("priority_score") or 0)
        + max(projected_gain, 0) * 1.5
        - (2 * len(bye_warnings)),
        2,
    )
    row = {
        "action": action,
        "acquisition_action": action,
        "add_player_id": candidate.get("player_id"),
        "add_name": candidate.get("name"),
        "add_position": candidate.get("position"),
        "add_team": candidate.get("team"),
        "add_projected_points": projected_points,
        "add_status": candidate.get("status", ""),
        "add_injury_status": candidate.get("injury_status", ""),
        "depth_chart_order": candidate.get("depth_chart_order", ""),
        "depth_chart_position": candidate.get("depth_chart_position", ""),
        "bye_week": candidate.get("bye_week", ""),
        "drop_player_id": drop_candidate.get("player_id", ""),
        "drop_name": drop_candidate.get("name", ""),
        "drop_position": drop_candidate.get("position", ""),
        "drop_team": drop_candidate.get("team", ""),
        "drop_lineup_status": drop_candidate.get("lineup_status", ""),
        "drop_projected_points": drop_points,
        "drop_reason": drop_reason,
        "drop_reasoning": drop_reason,
        "selected_drop_reasoning": drop_reason,
        "rejected_drop_reasoning": rejected_drops,
        "projected_gain_over_drop": projected_gain,
        "market_type": market_type,
        "add_trend_count": candidate.get("add_trend_count", 0),
        "drop_trend_count": candidate.get("drop_trend_count", 0),
        "net_trend_count": candidate.get("net_trend_count", 0),
        "rostered_percent": candidate.get("rostered_percent"),
        "urgency": add_urgency(projected_gain, candidate),
        "add_reasoning": add_reasoning(candidate, projected_gain),
        "bye_week_warnings": bye_warnings,
        "priority_score": priority_score,
        "source_metadata": candidate.get("source_metadata", {}),
    }
    if market_type == "waiver":
        faab_hint = build_faab_hint(projected_gain, candidate)
        row.update(
            {
                "faab_bid_pct": faab_hint["bid_pct"],
                "faab_tier": faab_hint["tier"],
                "faab_reasoning": faab_hint["reasoning"],
            }
        )
    return row


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
        "acquisition_action": candidate.get(
            "acquisition_action",
            acquisition_action(candidate.get("market_type"), projected_gain=0),
        ),
        "urgency": add_urgency(0, candidate),
        "priority_score": candidate.get("priority_score", 0),
        "status": candidate.get("status", ""),
        "injury_status": candidate.get("injury_status", ""),
        "depth_chart_order": candidate.get("depth_chart_order", ""),
        "depth_chart_position": candidate.get("depth_chart_position", ""),
        "bye_week": candidate.get("bye_week", ""),
        "source_metadata": candidate.get("source_metadata", {}),
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


def best_drop_candidate(
    candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    selected_pool: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in roster_players:
        protected_reason = drop_protection_reason(row, roster_players)
        if protected_reason:
            rejected.append(
                {
                    "player_id": row.get("player_id", ""),
                    "name": row.get("name", ""),
                    "position": row.get("position", ""),
                    "reason": protected_reason,
                }
            )
            continue
        selected_pool.append(row)

    drop_candidate = min(
        selected_pool,
        key=lambda row: (
            drop_ease_score(candidate, row, roster_players),
            float(row.get("projected_points") or 0),
        ),
        default={},
    )
    if not drop_candidate:
        return {}, "no unprotected drop candidate", rejected
    if same_position_family(candidate, drop_candidate):
        reason = "lowest risk active roster cut with comparable position coverage"
    else:
        reason = "lowest risk active roster cut across positions"
    return drop_candidate, reason, rejected


def drop_protection_reason(
    row: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> str:
    if row.get("player_id") in (None, "", "0"):
        return "placeholder roster row"
    if row.get("active_roster_spot") is False or str(row.get("lineup_status") or "").lower() == "reserve":
        return "reserve/IR stash does not consume an active bench spot"
    if str(row.get("lineup_status") or "").lower() == "starter":
        return "current starter"
    position = str(row.get("position") or "").upper()
    if not position:
        return ""
    active_position_rows = [
        player
        for player in roster_players
        if str(player.get("position") or "").upper() == position
        and player.get("active_roster_spot", True)
    ]
    playable_count = sum(
        1
        for player in active_position_rows
        if float(player.get("projected_points") or 0) >= playable_threshold(position)
    )
    if (
        playable_count <= desired_depth(position)
        and float(row.get("projected_points") or 0) >= playable_threshold(position)
    ):
        return "last playable backup at position"
    risky_starters = [
        player
        for player in roster_players
        if str(player.get("lineup_status") or "").lower() == "starter"
        and same_position_family(row, player)
        and is_availability_risk(player)
    ]
    if risky_starters and float(row.get("projected_points") or 0) > 0:
        return "coverage for questionable starter"
    return ""


def drop_ease_score(
    candidate: dict[str, Any],
    row: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    score = float(row.get("projected_points") or 0)
    if not same_position_family(candidate, row):
        score += 1.5
    position = str(row.get("position") or "").upper()
    position_count = sum(
        1
        for player in roster_players
        if str(player.get("position") or "").upper() == position
        and player.get("active_roster_spot", True)
    )
    if position_count <= desired_depth(position):
        score += 4
    return score


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
    return build_faab_hint(projected_gain, candidate)["bid_pct"]


def build_faab_hint(projected_gain: float, candidate: dict[str, Any]) -> dict[str, Any]:
    net_trend_count = int(candidate.get("net_trend_count") or 0)
    rostered_pct = float(candidate.get("rostered_percent") or 0)
    projected_points = float(candidate.get("projected_points") or 0)

    if projected_gain >= 8 or (projected_gain >= 5 and net_trend_count >= 1000):
        bid_pct = 14
        tier = "aggressive"
    elif projected_gain >= 4:
        bid_pct = 9
        tier = "standard"
    elif projected_gain >= 1.5:
        bid_pct = 5
        tier = "speculative"
    elif projected_gain > 0 or net_trend_count >= 1000 or rostered_pct >= 40:
        bid_pct = 2
        tier = "watch"
    else:
        bid_pct = 0
        tier = "pass"

    reasons = []
    if projected_gain > 0:
        reasons.append(f"projects {projected_gain:.2f} points above the drop candidate")
    else:
        reasons.append("does not project above the drop candidate")
    if net_trend_count > 0:
        reasons.append(f"net add trend is +{net_trend_count}")
    elif net_trend_count < 0:
        reasons.append(f"net add trend is {net_trend_count}")
    if rostered_pct:
        reasons.append(f"rostered percentage signal is {rostered_pct:.1f}")
    if projected_points <= 0:
        reasons.append("projection is currently zero")

    return {
        "bid_pct": bid_pct,
        "tier": tier,
        "reasoning": "; ".join(reasons),
    }


def group_waiver_options_by_position(
    *,
    candidates: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
    positions: list[str],
    per_position_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    grouped = {position: [] for position in positions}
    for candidate in candidates:
        position = str(candidate.get("position") or "").upper()
        if position not in grouped:
            continue
        row = compare_available_player(candidate, roster_players)
        if float(row.get("projected_gain_over_drop") or 0) > 0:
            grouped[position].append(row)

    return {
        position: sorted(
            rows,
            key=lambda row: (
                float(row.get("priority_score") or 0),
                float(row.get("projected_gain_over_drop") or 0),
            ),
            reverse=True,
        )[:per_position_limit]
        for position, rows in grouped.items()
    }


def acquisition_action(market_type: Any, *, projected_gain: float) -> str:
    normalized = normalize_market_type(market_type)
    if projected_gain <= 0:
        return "watch"
    if normalized == "waiver":
        return "submit_waiver_claim"
    if normalized == "free_agent":
        return "add_now"
    return "watch"


def add_urgency(projected_gain: float, candidate: dict[str, Any]) -> str:
    net_trend_count = int(candidate.get("net_trend_count") or 0)
    if projected_gain >= 6 or net_trend_count >= 1500:
        return "high"
    if projected_gain >= 2 or net_trend_count >= 250:
        return "medium"
    return "low"


def add_reasoning(candidate: dict[str, Any], projected_gain: float) -> str:
    reasons = []
    if projected_gain > 0:
        reasons.append(f"projects {projected_gain:.2f} points above the selected drop")
    else:
        reasons.append("does not project above an unprotected drop")
    if candidate.get("depth_chart_order") not in (None, ""):
        reasons.append(
            f"depth chart {candidate.get('depth_chart_position') or candidate.get('position')} {candidate.get('depth_chart_order')}"
        )
    if candidate.get("injury_status"):
        reasons.append(f"injury status is {candidate.get('injury_status')}")
    return "; ".join(reasons)


def is_availability_risk(row: dict[str, Any]) -> bool:
    if row.get("injury_status"):
        return True
    status = str(row.get("status") or "").strip().lower()
    return bool(status and status != "active")


def bye_pressure_warnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        int(row.get("bye_week"))
        for row in rows
        if row.get("active_roster_spot", True)
        and str(row.get("bye_week") or "").isdigit()
    )
    return [
        {
            "week": week,
            "player_count": count,
            "severity": "high" if count >= 5 else "medium",
            "reason": f"{count} active roster players share a bye week",
        }
        for week, count in sorted(counts.items())
        if count >= 4
    ]


def move_bye_warnings(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not str(candidate.get("bye_week") or "").isdigit():
        return []
    candidate_bye = int(candidate["bye_week"])
    after = [
        row
        for row in roster_players
        if row.get("player_id") != drop_candidate.get("player_id")
    ] + [candidate]
    return [
        warning
        for warning in bye_pressure_warnings(after)
        if warning["week"] == candidate_bye
    ]


def build_trade_opportunities(
    *,
    league_id: str,
    roster_id: int,
    season: int,
    week: int,
    lineup: dict[str, Any],
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    positions: list[str],
    targets_per_team: int,
    offers_per_team: int,
) -> dict[str, Any]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    projections_by_player = {str(row.get("player_id")): row for row in projection_rows}
    my_roster_players = [
        row
        for row in lineup.get("lineup_table", lineup.get("starters", []) + lineup.get("bench", []))
        if str(row.get("player_id") or "") != "0"
    ]
    my_starters = [
        row
        for row in my_roster_players
        if str(row.get("lineup_status") or "").lower() == "starter"
    ]
    my_bench = [
        row
        for row in my_roster_players
        if str(row.get("lineup_status") or "").lower() == "bench"
        and row.get("active_roster_spot", True)
    ]
    my_offer_chips = trade_offer_chips(my_roster_players)
    my_upgrade_slots = sorted(
        [
            row
            for row in my_starters
            if str(row.get("position") or "").upper() in set(positions)
        ],
        key=lambda row: float(row.get("projected_points") or 0),
    )

    teams = []
    for roster in rosters:
        other_roster_id = int(roster.get("roster_id", 0))
        if other_roster_id == roster_id:
            continue
        owner = users_by_id.get(str(roster.get("owner_id")))
        roster_rows = roster_projection_rows(
            roster.get("players") or [],
            players=players,
            projections_by_player=projections_by_player,
            positions=positions,
        )
        needs = roster_needs(roster_rows, positions)
        surplus = roster_surplus(roster_rows, positions)
        targets = trade_targets(
            roster_rows,
            my_upgrade_slots=my_upgrade_slots,
            opponent_surplus=surplus,
            targets_per_team=targets_per_team,
        )
        offer_angles = []
        for target in targets:
            offer_angles.extend(
                build_offer_angles(
                    target=target,
                    my_offer_chips=my_offer_chips,
                    my_bench=my_bench,
                    my_roster_players=my_roster_players,
                    opponent_needs=needs,
                    offers_per_team=offers_per_team,
                )
            )
            if len(offer_angles) >= offers_per_team:
                break
        offer_angles = sorted(
            offer_angles,
            key=lambda row: (
                float(row.get("trade_score") or 0),
                float(row.get("opponent_fit_score") or 0),
                float(row.get("my_gain") or 0),
            ),
            reverse=True,
        )
        teams.append(
            {
                "roster_id": other_roster_id,
                "team_name": owner_display_name(owner),
                "needs": needs,
                "surplus": surplus,
                "targets": targets,
                "offer_angles": offer_angles[:offers_per_team],
                "reasoning": trade_reasoning(needs, surplus, offer_angles),
            }
        )

    return {
        "league_id": league_id,
        "roster_id": roster_id,
        "team_name": lineup.get("team_name"),
        "season": season,
        "week": week,
        "teams": teams,
        "evidence": [
            "trade opportunities are projection-based screens, not trade value rankings",
            "each opposing roster is included even when no attractive offer angle is found",
            "offer angles must match an opponent need and prefer bench or surplus players before core starters",
            "projected lineup gain compares the target to the lowest projected comparable starter",
        ],
    }


def roster_projection_rows(
    player_ids: list[Any],
    *,
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
    positions: list[str],
) -> list[dict[str, Any]]:
    allowed = {position.upper() for position in positions}
    rows = []
    for player_id in player_ids:
        row = _player_projection_summary(str(player_id), players, projections_by_player)
        if row.get("position") and str(row["position"]).upper() in allowed:
            rows.append(row)
    return sorted(rows, key=lambda row: float(row.get("projected_points") or 0), reverse=True)


def roster_needs(rows: list[dict[str, Any]], positions: list[str]) -> list[dict[str, Any]]:
    needs = []
    for position in positions:
        position_rows = [row for row in rows if str(row.get("position") or "").upper() == position]
        top_projection = max(
            [float(row.get("projected_points") or 0) for row in position_rows],
            default=0.0,
        )
        playable_count = sum(1 for row in position_rows if float(row.get("projected_points") or 0) >= playable_threshold(position))
        if playable_count < desired_depth(position) or top_projection < playable_threshold(position):
            needs.append(
                {
                    "position": position,
                    "playable_count": playable_count,
                    "top_projected_points": round(top_projection, 2),
                    "reason": "thin playable depth",
                }
            )
    return needs


def roster_surplus(rows: list[dict[str, Any]], positions: list[str]) -> list[dict[str, Any]]:
    surplus = []
    for position in positions:
        position_rows = [row for row in rows if str(row.get("position") or "").upper() == position]
        playable = [
            row
            for row in position_rows
            if float(row.get("projected_points") or 0) >= playable_threshold(position)
        ]
        if len(playable) > desired_depth(position):
            surplus.append(
                {
                    "position": position,
                    "playable_count": len(playable),
                    "top_names": [row.get("name") for row in playable[:3]],
                }
            )
    return surplus


def trade_targets(
    roster_rows: list[dict[str, Any]],
    *,
    my_upgrade_slots: list[dict[str, Any]],
    opponent_surplus: list[dict[str, Any]],
    targets_per_team: int,
) -> list[dict[str, Any]]:
    targets = []
    surplus_positions = {str(row.get("position") or "").upper() for row in opponent_surplus}
    for player in roster_rows:
        if surplus_positions and str(player.get("position") or "").upper() not in surplus_positions:
            continue
        replaced = comparable_upgrade_slot(player, my_upgrade_slots)
        if not replaced:
            continue
        gain = round(
            float(player.get("projected_points") or 0)
            - float(replaced.get("projected_points") or 0),
            2,
        )
        if gain <= 0:
            continue
        targets.append(
            {
                **player,
                "projected_lineup_gain": gain,
                "upgrade_over": {
                    "player_id": replaced.get("player_id"),
                    "name": replaced.get("name"),
                    "position": replaced.get("position"),
                    "team": replaced.get("team"),
                    "projected_points": replaced.get("projected_points"),
                    "status": replaced.get("status", ""),
                    "injury_status": replaced.get("injury_status", ""),
                },
                "opponent_surplus_position": str(player.get("position") or "").upper() in surplus_positions,
            }
        )
    return sorted(
        targets,
        key=lambda row: (
            float(row.get("projected_lineup_gain") or 0),
            float(row.get("projected_points") or 0),
        ),
        reverse=True,
    )[:targets_per_team]


def comparable_upgrade_slot(
    target: dict[str, Any],
    my_upgrade_slots: list[dict[str, Any]],
) -> dict[str, Any] | None:
    comparable = [row for row in my_upgrade_slots if same_position_family(target, row)]
    if not comparable:
        return None
    return min(comparable, key=lambda row: float(row.get("projected_points") or 0))


def build_offer_angles(
    *,
    target: dict[str, Any],
    my_offer_chips: list[dict[str, Any]],
    my_bench: list[dict[str, Any]],
    my_roster_players: list[dict[str, Any]],
    opponent_needs: list[dict[str, Any]],
    offers_per_team: int,
) -> list[dict[str, Any]]:
    need_positions = {str(row.get("position") or "").upper() for row in opponent_needs}
    angles = []
    direct = [
        chip
        for chip in my_offer_chips
        if str(chip.get("position") or "").upper() in need_positions
        and float(chip.get("projected_points") or 0) > 0
    ]
    package_pool = sorted(
        [
            chip
            for chip in my_offer_chips
            if str(chip.get("position") or "").upper() in need_positions
            if float(chip.get("projected_points") or 0) > 0
        ],
        key=lambda row: float(row.get("projected_points") or 0),
        reverse=True,
    )
    for chip in direct[:offers_per_team]:
        angles.append(
            trade_angle(
                target=target,
                offer=[chip],
                angle_type="need_fit",
                reasoning=f"{chip.get('name')} addresses their {chip.get('position')} need.",
                opponent_needs=opponent_needs,
                my_roster_players=my_roster_players,
            )
        )
    if len(package_pool) >= 2 and len({chip.get("player_id") for chip in package_pool[:2]}) == 2:
        package = package_pool[:2]
        angles.append(
            trade_angle(
                target=target,
                offer=package,
                angle_type="need_fit_package",
                reasoning="Package addresses an opponent need while consolidating your depth into a starter upgrade.",
                opponent_needs=opponent_needs,
                my_roster_players=my_roster_players,
            )
        )
    return sorted(
        angles,
        key=lambda row: float(row.get("trade_score") or 0),
        reverse=True,
    )[:offers_per_team]


def trade_angle(
    *,
    target: dict[str, Any],
    offer: list[dict[str, Any]],
    angle_type: str,
    reasoning: str,
    opponent_needs: list[dict[str, Any]],
    my_roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    offer_total = round(sum(float(row.get("projected_points") or 0) for row in offer), 2)
    need_positions = {str(row.get("position") or "").upper() for row in opponent_needs}
    matched_needs = sorted(
        {
            str(row.get("position") or "").upper()
            for row in offer
            if str(row.get("position") or "").upper() in need_positions
        }
    )
    my_gain = float(target.get("projected_lineup_gain") or 0)
    opponent_fit_score = opponent_trade_fit_score(
        offer=offer,
        opponent_need_matched=matched_needs,
    )
    backup_risk = trade_backup_risk(offer, my_roster_players)
    bye_week_risk = trade_bye_week_risk(target, offer, my_roster_players)
    roster_balance_after = roster_balance_after_trade(target, offer, my_roster_players)
    trade_score = round(
        my_gain * 10
        + opponent_fit_score
        - float(backup_risk.get("penalty") or 0)
        - float(bye_week_risk.get("penalty") or 0)
        - float(roster_balance_after.get("penalty") or 0),
        2,
    )
    return {
        "angle_type": angle_type,
        "ask_for": target,
        "offer": [
            {
                "player_id": row.get("player_id"),
                "name": row.get("name"),
                "position": row.get("position"),
                "team": row.get("team"),
                "projected_points": row.get("projected_points"),
                "status": row.get("status", ""),
                "injury_status": row.get("injury_status", ""),
            }
            for row in offer
        ],
        "offer_projected_points": offer_total,
        "projected_lineup_gain": target.get("projected_lineup_gain", 0),
        "my_gain": my_gain,
        "opponent_fit_score": opponent_fit_score,
        "opponent_need_matched": matched_needs,
        "backup_risk": backup_risk,
        "bye_week_risk": bye_week_risk,
        "roster_balance_after": roster_balance_after,
        "trade_score": trade_score,
        "reasoning": reasoning,
    }


def trade_offer_chips(roster_players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_position: dict[str, list[dict[str, Any]]] = {}
    for row in roster_players:
        position = str(row.get("position") or "").upper()
        by_position.setdefault(position, []).append(row)

    chips = []
    for position, rows in by_position.items():
        sorted_rows = sorted(
            rows,
            key=lambda row: float(row.get("projected_points") or 0),
            reverse=True,
        )
        keep = desired_depth(position)
        for row in sorted_rows[keep:]:
            if float(row.get("projected_points") or 0) > 0 and not drop_protection_reason(row, roster_players):
                chips.append(row)
    chips.extend(
        row
        for row in roster_players
        if str(row.get("lineup_status") or "").lower() == "bench"
        and float(row.get("projected_points") or 0) > 0
        and not drop_protection_reason(row, roster_players)
        and row not in chips
    )
    return sorted(chips, key=lambda row: float(row.get("projected_points") or 0), reverse=True)


def opponent_trade_fit_score(
    *,
    offer: list[dict[str, Any]],
    opponent_need_matched: list[str],
) -> float:
    offer_points = sum(float(row.get("projected_points") or 0) for row in offer)
    return round(len(opponent_need_matched) * 35 + min(offer_points, 30), 2)


def trade_backup_risk(
    offer: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons = []
    penalty = 0
    for row in offer:
        reason = drop_protection_reason(row, roster_players)
        if reason:
            reasons.append(f"{row.get('name')} is protected: {reason}")
            penalty += 25
        position = str(row.get("position") or "").upper()
        remaining_playable = [
            player
            for player in roster_players
            if player.get("player_id") != row.get("player_id")
            and str(player.get("position") or "").upper() == position
            and player.get("active_roster_spot", True)
            and float(player.get("projected_points") or 0) >= playable_threshold(position)
        ]
        if len(remaining_playable) < desired_depth(position):
            reasons.append(f"{position} depth would fall below desired playable coverage")
            penalty += 10
    level = "high" if penalty >= 25 else "medium" if penalty else "low"
    return {"level": level, "penalty": penalty, "reasons": reasons}


def trade_bye_week_risk(
    target: dict[str, Any],
    offer: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    after = [
        player
        for player in roster_players
        if player.get("player_id") not in {row.get("player_id") for row in offer}
    ] + [target]
    counts = Counter(
        int(player.get("bye_week"))
        for player in after
        if str(player.get("bye_week") or "").isdigit()
    )
    clustered = {
        week: count
        for week, count in counts.items()
        if count >= 4
    }
    penalty = sum((count - 3) * 4 for count in clustered.values())
    return {
        "level": "medium" if penalty else "low",
        "penalty": penalty,
        "clustered_byes": clustered,
        "incoming_bye_week": target.get("bye_week", ""),
        "outgoing_bye_weeks": [row.get("bye_week", "") for row in offer],
    }


def roster_balance_after_trade(
    target: dict[str, Any],
    offer: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
) -> dict[str, Any]:
    outgoing_ids = {row.get("player_id") for row in offer}
    after = [
        player
        for player in roster_players
        if player.get("player_id") not in outgoing_ids
    ] + [target]
    counts = Counter(str(row.get("position") or "").upper() for row in after if row.get("position"))
    warnings = []
    penalty = 0
    for position, desired in {position: desired_depth(position) for position in counts}.items():
        if counts[position] < desired:
            warnings.append(f"{position} depth below desired roster balance")
            penalty += 8
    return {
        "position_counts": dict(sorted(counts.items())),
        "warnings": warnings,
        "penalty": penalty,
    }


def trade_reasoning(
    needs: list[dict[str, Any]],
    surplus: list[dict[str, Any]],
    offer_angles: list[dict[str, Any]],
) -> list[str]:
    reasons = []
    if needs:
        reasons.append(
            "Needs: "
            + ", ".join(f"{row['position']} depth" for row in needs[:3])
        )
    if surplus:
        reasons.append(
            "Surplus: "
            + ", ".join(f"{row['position']} depth" for row in surplus[:3])
        )
    if offer_angles:
        reasons.append("At least one offer angle matches an opponent need and creates a projected lineup upgrade for you.")
    else:
        reasons.append("No clear mutual-fit offer angle from current roster depth.")
    return reasons


def playable_threshold(position: str) -> float:
    return {
        "QB": 14.0,
        "RB": 8.0,
        "WR": 8.0,
        "TE": 6.0,
        "K": 5.0,
        "DEF": 5.0,
    }.get(position.upper(), 6.0)


def desired_depth(position: str) -> int:
    return {
        "QB": 1,
        "RB": 3,
        "WR": 4,
        "TE": 1,
        "K": 1,
        "DEF": 1,
    }.get(position.upper(), 1)


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
        **player_context(
            player_id,
            players=players,
            projection=projection,
            market_hint=infer_market_type(player, row=projection, default="unknown"),
        ),
        "projected_points": projection.get("points", 0),
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
