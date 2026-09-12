from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from typing import Any

from sleeper_tooling.player_values import build_player_value, build_player_values
from sleeper_tooling.reports import owner_display_name, player_name

DEFAULT_STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"]
NON_STARTER_SLOTS = {"BN", "BE", "IR", "TAXI"}
POSITION_SLOTS = {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP"}
SKILL_POSITIONS = {"RB", "WR", "TE"}
KNOWN_MARKET_TYPES = {"free_agent", "waiver", "unknown"}
TRADE_PACKAGE_TYPES = [(1, 1), (2, 1), (1, 2), (3, 2), (2, 3)]
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
    projected_active_roster_total = round(
        sum(float(row.get("projected_points") or 0) for row in starters + bench),
        2,
    )
    projected_roster_total = round(
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
        "projected_total": projected_starter_total,
        "projected_starter_total": projected_starter_total,
        "projected_starter_points": projected_starter_total,
        "projected_active_roster_total": projected_active_roster_total,
        "projected_roster_total": projected_roster_total,
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

    waiver_matrix = sorted(
        [
            row
            for candidate in available_candidates
            for row in [compare_available_player(candidate, roster_players)]
            if row.get("drop_player_id") and float(row.get("projected_gain_over_drop") or 0) > 0
        ],
        key=waiver_move_sort_key,
        reverse=True,
    )
    waiver_comparisons = [
        row for row in waiver_matrix if row.get("recommendation") == "recommend"
    ][:limit]
    waiver_watch_items = [
        row for row in waiver_matrix if row.get("recommendation") == "watch"
    ][:limit]

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
        "waiver_watch_items": waiver_watch_items,
        "watchlist": watchlist,
        "evidence": [
            "starter and bench comparisons use projected_points under league scoring",
            "waiver comparisons exclude players already rostered in the league",
            "waiver comparisons are ranked by deterministic move_score from the roster-impact move matrix",
            "reserve and last-playable backup protections are applied before choosing drop candidates",
            "unknown acquisition markets stay watch-level until verified in Sleeper",
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
    matrix = build_waiver_move_matrix(candidate, roster_players)
    selected = max(matrix, key=waiver_move_sort_key, default={})
    drop_candidate = selected.get("drop_candidate", {})
    rejected_drops = selected.get("rejected_drop_reasoning", [])
    drop_reason = selected.get("selected_drop_reasoning") or selected.get("drop_reasoning") or "no unprotected drop candidate"
    projected_points = float(candidate.get("projected_points") or 0)
    drop_points = float(drop_candidate.get("projected_points") or 0)
    projected_gain = round(projected_points - drop_points, 2)
    market_type = normalize_market_type(candidate.get("market_type"))
    recommendation = str(selected.get("recommendation") or "reject")
    action = acquisition_action(market_type, projected_gain=projected_gain) if recommendation == "recommend" else "watch"
    bye_warnings = move_bye_warnings(candidate, drop_candidate, roster_players)
    priority_score = selected.get("move_score", 0)
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
        "week_value_delta": selected.get("week_value_delta", projected_gain),
        "three_week_value_delta": selected.get("three_week_value_delta", projected_gain),
        "season_value_delta": selected.get("season_value_delta", projected_gain),
        "starter_impact": selected.get("starter_impact", 0),
        "depth_impact": selected.get("depth_impact", 0),
        "positional_need_score": selected.get("positional_need_score", 0),
        "positional_damage_score": selected.get("positional_damage_score", 0),
        "injury_coverage_impact": selected.get("injury_coverage_impact", 0),
        "bye_week_impact": selected.get("bye_week_impact", 0),
        "streamer_penalty": selected.get("streamer_penalty", 0),
        "stash_penalty": selected.get("stash_penalty", 0),
        "drop_protection_reason": selected.get("drop_protection_reason", ""),
        "move_score": selected.get("move_score", 0),
        "recommendation": recommendation,
        "reasoning_summary": selected.get("reasoning_summary", ""),
        "market_type": market_type,
        "roster_availability": candidate.get("roster_availability", "unrostered"),
        "market_confidence": market_confidence(candidate),
        "add_trend_count": candidate.get("add_trend_count", 0),
        "drop_trend_count": candidate.get("drop_trend_count", 0),
        "net_trend_count": candidate.get("net_trend_count", 0),
        "rostered_percent": candidate.get("rostered_percent"),
        "urgency": add_urgency(projected_gain, {**candidate, "recommendation": recommendation}),
        "add_reasoning": add_reasoning(candidate, projected_gain),
        "bye_week_warnings": bye_warnings,
        "priority_score": priority_score,
        "source_metadata": {
            **candidate.get("source_metadata", {}),
            "waiver_matrix": selected.get("source_metadata", {}),
        },
    }
    for key in ("week_value", "three_week_value", "season_value", "decision_value", "value_tier", "role_tag"):
        if key in selected.get("add_value", {}):
            row[f"add_{key}"] = selected["add_value"][key]
        if key in selected.get("drop_value", {}):
            row[f"drop_{key}"] = selected["drop_value"][key]
    if action == "submit_waiver_claim":
        faab_hint = build_faab_hint(projected_gain, candidate)
        row.update(
            {
                "faab_bid_pct": faab_hint["bid_pct"],
                "faab_tier": faab_hint["tier"],
                "faab_reasoning": faab_hint["reasoning"],
            }
        )
    return row


def build_waiver_move_matrix(
    candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    selected_pool: list[dict[str, Any]] = []
    for drop_candidate in roster_players:
        protection_reason = drop_protection_reason(drop_candidate, roster_players)
        if protection_reason:
            rejected.append(
                {
                    "player_id": drop_candidate.get("player_id", ""),
                    "name": drop_candidate.get("name", ""),
                    "position": drop_candidate.get("position", ""),
                    "reason": protection_reason,
                }
            )
            continue
        selected_pool.append(drop_candidate)

    rows = [
        score_waiver_move(
            candidate=candidate,
            drop_candidate=drop_candidate,
            roster_players=roster_players,
            rejected_drops=rejected,
        )
        for drop_candidate in selected_pool
    ]
    if rows:
        return rows
    return [
        score_waiver_move(
            candidate=candidate,
            drop_candidate={},
            roster_players=roster_players,
            rejected_drops=rejected,
        )
    ]


def score_waiver_move(
    *,
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
    rejected_drops: list[dict[str, Any]],
) -> dict[str, Any]:
    add_value = waiver_player_value(candidate)
    drop_value = waiver_player_value(drop_candidate) if drop_candidate else empty_player_value()
    week_delta = round(float(add_value["week_value"]) - float(drop_value["week_value"]), 2)
    three_week_delta = round(float(add_value["three_week_value"]) - float(drop_value["three_week_value"]), 2)
    season_delta = round(float(add_value["season_value"]) - float(drop_value["season_value"]), 2)
    starter_impact = waiver_starter_impact(candidate, roster_players)
    depth_impact = waiver_depth_impact(candidate, drop_candidate, roster_players)
    need_score = positional_need_score(candidate, drop_candidate, roster_players)
    damage_score = positional_damage_score(candidate, drop_candidate, roster_players)
    injury_impact = injury_coverage_impact(candidate, drop_candidate, roster_players)
    bye_impact = bye_week_impact(candidate, drop_candidate, roster_players)
    streamer_penalty = waiver_streamer_penalty(candidate, drop_candidate, roster_players)
    stash_penalty = waiver_stash_penalty(candidate, roster_players)
    unknown_market_penalty = 2 if normalize_market_type(candidate.get("market_type")) == "unknown" else 0
    no_drop_penalty = 100 if not drop_candidate else 0
    move_score = round(
        week_delta * 0.8
        + three_week_delta * 1.2
        + season_delta * 1.4
        + starter_impact * 1.5
        + depth_impact
        + need_score
        + injury_impact
        + bye_impact
        + trend_priority_boost(int(candidate.get("net_trend_count") or 0))
        - damage_score
        - streamer_penalty
        - stash_penalty
        - unknown_market_penalty
        - no_drop_penalty,
        2,
    )
    recommendation = waiver_recommendation(
        candidate=candidate,
        drop_candidate=drop_candidate,
        roster_players=roster_players,
        move_score=move_score,
        projected_gain=week_delta,
        damage_score=damage_score,
        streamer_penalty=streamer_penalty,
        stash_penalty=stash_penalty,
    )
    selected_reason = selected_drop_reason(candidate, drop_candidate)
    return {
        "candidate": candidate,
        "drop_candidate": drop_candidate,
        "add_player_id": candidate.get("player_id"),
        "add_name": candidate.get("name"),
        "add_position": candidate.get("position"),
        "drop_player_id": drop_candidate.get("player_id", ""),
        "drop_name": drop_candidate.get("name", ""),
        "drop_position": drop_candidate.get("position", ""),
        "roster_availability": candidate.get("roster_availability", "unrostered"),
        "market_type": normalize_market_type(candidate.get("market_type")),
        "market_confidence": market_confidence(candidate),
        "acquisition_action": acquisition_action(
            candidate.get("market_type"),
            projected_gain=week_delta,
        ) if recommendation == "recommend" else "watch",
        "week_value_delta": week_delta,
        "three_week_value_delta": three_week_delta,
        "season_value_delta": season_delta,
        "starter_impact": starter_impact,
        "depth_impact": depth_impact,
        "positional_need_score": need_score,
        "positional_damage_score": damage_score,
        "injury_coverage_impact": injury_impact,
        "bye_week_impact": bye_impact,
        "streamer_penalty": streamer_penalty,
        "stash_penalty": stash_penalty,
        "drop_protection_reason": "",
        "move_score": move_score,
        "recommendation": recommendation,
        "drop_reasoning": selected_reason,
        "selected_drop_reasoning": selected_reason,
        "reasoning_summary": waiver_reasoning_summary(
            candidate=candidate,
            drop_candidate=drop_candidate,
            recommendation=recommendation,
            move_score=move_score,
            need_score=need_score,
            damage_score=damage_score,
            streamer_penalty=streamer_penalty,
            stash_penalty=stash_penalty,
            week_delta=week_delta,
        ),
        "rejected_drop_reasoning": list(rejected_drops),
        "add_value": add_value,
        "drop_value": drop_value,
        "source_metadata": {
            "builder": "sleeper_tooling.decision_reports.build_waiver_move_matrix",
            "ranking": "deterministic roster-impact move_score",
            "roster_analysis_compatible": True,
        },
    }


def waiver_player_value(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return empty_player_value()
    existing = {
        "player_id": row.get("player_id", ""),
        "name": row.get("name", ""),
        "team": row.get("team", ""),
        "position": str(row.get("position") or "").upper(),
        "fantasy_positions": row.get("fantasy_positions") or [],
        "week_value": number_or(row.get("week_value"), row.get("projected_points"), 0.0),
        "three_week_value": number_or(row.get("three_week_value"), row.get("projected_points"), 0.0),
        "season_value": number_or(row.get("season_value"), row.get("decision_value"), row.get("projected_points"), 0.0),
        "decision_value": number_or(row.get("decision_value"), row.get("projected_points"), 0.0),
        "value_tier": row.get("value_tier", ""),
        "role_tag": row.get("role_tag", ""),
    }
    if any(key in row for key in ("week_value", "three_week_value", "season_value", "decision_value")):
        return existing

    return {
        **existing,
        **{
            key: value
            for key, value in build_player_value(
                player_id=str(row.get("player_id") or ""),
                player={
                    "full_name": row.get("name", ""),
                    "team": row.get("team", ""),
                    "position": row.get("position", ""),
                    "fantasy_positions": row.get("fantasy_positions") or [],
                    "status": row.get("status", ""),
                    "injury_status": row.get("injury_status", ""),
                    "bye_week": row.get("bye_week", ""),
                    "rostered_percent": row.get("rostered_percent"),
                    "depth_chart_order": row.get("depth_chart_order", ""),
                },
                projection_row={
                    "player_id": row.get("player_id", ""),
                    "points": row.get("projected_points", 0),
                    "team": row.get("team", ""),
                    "position": row.get("position", ""),
                },
            ).items()
            if key in {"week_value", "three_week_value", "season_value", "decision_value", "value_tier", "role_tag"}
        },
    }


def empty_player_value() -> dict[str, Any]:
    return {
        "player_id": "",
        "name": "",
        "team": "",
        "position": "",
        "fantasy_positions": [],
        "week_value": 0.0,
        "three_week_value": 0.0,
        "season_value": 0.0,
        "decision_value": 0.0,
        "value_tier": "",
        "role_tag": "",
    }


def waiver_starter_impact(candidate: dict[str, Any], roster_players: list[dict[str, Any]]) -> float:
    comparable_starters = [
        row
        for row in roster_players
        if str(row.get("lineup_status") or "").lower() == "starter"
        and same_position_family(candidate, row)
    ]
    if not comparable_starters:
        return 0.0
    weakest_starter = min(comparable_starters, key=lambda row: float(row.get("projected_points") or 0))
    return round(max(0.0, float(candidate.get("projected_points") or 0) - float(weakest_starter.get("projected_points") or 0)), 2)


def waiver_depth_impact(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    if not drop_candidate:
        return 0.0
    before = roster_position_summary(roster_players)
    after = roster_position_summary(roster_after_move(candidate, drop_candidate, roster_players))
    positions = {
        primary_position(candidate),
        primary_position(drop_candidate),
    } - {""}
    impact = 0.0
    for position in positions:
        before_group = before.get(position, {})
        after_group = after.get(position, {})
        desired = desired_depth(position)
        before_gap = max(0, desired - int(before_group.get("playable_count", 0)))
        after_gap = max(0, desired - int(after_group.get("playable_count", 0)))
        impact += (before_gap - after_gap) * 4
        if position in SKILL_POSITIONS:
            before_count = int(before_group.get("active_count", 0))
            after_count = int(after_group.get("active_count", 0))
            if after_count > before_count:
                impact += 1
            elif after_count < before_count and after_gap > before_gap:
                impact -= 3
    return round(impact, 2)


def positional_need_score(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    position = primary_position(candidate)
    if not position:
        return 0.0
    summary = roster_position_summary(roster_players)
    group = summary.get(position, {})
    if backup_qb_suppression_applies(candidate, roster_players):
        return -18.0
    if position in {"K", "DEF"}:
        if int(group.get("playable_count", 0)) == 0:
            return 10.0
        if is_same_position(candidate, drop_candidate):
            return 5.0
        return 0.0
    playable_gap = max(0, desired_depth(position) - int(group.get("playable_count", 0)))
    starter_risk = bool(group.get("risky_starter_count"))
    score = playable_gap * 5
    if starter_risk and int(group.get("playable_bench_count", 0)) == 0:
        score += 8
    return float(score)


def positional_damage_score(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    if not drop_candidate:
        return 100.0
    if same_position_family(candidate, drop_candidate):
        return 0.0
    position = primary_position(drop_candidate)
    if not position:
        return 0.0
    before = roster_position_summary(roster_players).get(position, {})
    after = roster_position_summary(roster_after_move(candidate, drop_candidate, roster_players)).get(position, {})
    before_gap = max(0, desired_depth(position) - int(before.get("playable_count", 0)))
    after_gap = max(0, desired_depth(position) - int(after.get("playable_count", 0)))
    damage = 5.0
    if after_gap > before_gap:
        damage += (after_gap - before_gap) * 10
    if int(after.get("playable_count", 0)) < int(after.get("required_starter_count", 0)):
        damage += 12
    if bool(before.get("risky_starter_count")) and int(after.get("playable_bench_count", 0)) == 0:
        damage += 10
    if position in SKILL_POSITIONS and primary_position(candidate) in {"QB", "K", "DEF"}:
        damage += 8
    return damage


def injury_coverage_impact(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    if not drop_candidate:
        return 0.0
    before = roster_position_summary(roster_players)
    after = roster_position_summary(roster_after_move(candidate, drop_candidate, roster_players))
    impact = 0.0
    for position, group in before.items():
        if not group.get("risky_starter_count"):
            continue
        before_cover = int(group.get("playable_bench_count", 0))
        after_cover = int(after.get(position, {}).get("playable_bench_count", 0))
        if after_cover > before_cover:
            impact += 8
        elif after_cover < before_cover:
            impact -= 10
    return impact


def bye_week_impact(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    warnings = move_bye_warnings(candidate, drop_candidate, roster_players) if drop_candidate else []
    if not warnings:
        return 0.0
    return -4.0 * len(warnings)


def waiver_streamer_penalty(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> float:
    position = primary_position(candidate)
    if position not in {"K", "DEF"}:
        return 0.0
    if not drop_candidate:
        return 20.0
    if is_same_position(candidate, drop_candidate):
        return 0.0
    summary = roster_position_summary(roster_players).get(position, {})
    lacks_playable_option = int(summary.get("playable_count", 0)) == 0
    if lacks_playable_option:
        return 0.0
    drop_position = primary_position(drop_candidate)
    if drop_position in SKILL_POSITIONS:
        return 24.0
    return 12.0


def waiver_stash_penalty(candidate: dict[str, Any], roster_players: list[dict[str, Any]]) -> float:
    position = primary_position(candidate)
    role = str(candidate.get("role_tag") or candidate.get("value_tier") or "").lower()
    if position in SKILL_POSITIONS and role in {"stash", "depth", "strong_starter", "elite"}:
        return 0.0
    if position == "QB" and backup_qb_suppression_applies(candidate, roster_players):
        return 16.0
    if position in {"K", "DEF"}:
        return 4.0
    return 0.0


def backup_qb_suppression_applies(candidate: dict[str, Any], roster_players: list[dict[str, Any]]) -> bool:
    if primary_position(candidate) != "QB":
        return False
    summary = roster_position_summary(roster_players)
    qb_group = summary.get("QB", {})
    if int(qb_group.get("required_starter_count", 0)) > 1:
        return False
    if int(qb_group.get("risky_starter_count", 0)):
        return False
    starter_points = float(qb_group.get("top_starter_points") or 0)
    if starter_points < 22:
        return False
    candidate_points = float(candidate.get("projected_points") or 0)
    rostered_pct = float(candidate.get("rostered_percent") or 0)
    value_tier = str(candidate.get("value_tier") or "").lower()
    role_tag = str(candidate.get("role_tag") or "").lower()
    clear_stash = (
        candidate_points >= 18
        or rostered_pct >= 65
        or value_tier in {"elite", "strong_starter"}
        or role_tag in {"starter", "depth"}
    )
    return not clear_stash


def waiver_recommendation(
    *,
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
    move_score: float,
    projected_gain: float,
    damage_score: float,
    streamer_penalty: float,
    stash_penalty: float,
) -> str:
    if not drop_candidate:
        return "reject"
    if projected_gain <= 0 and move_score < 8:
        return "reject"
    if backup_qb_suppression_applies(candidate, roster_players):
        return "watch"
    if damage_score >= 20 or streamer_penalty >= 20 or stash_penalty >= 16:
        return "watch" if move_score > 0 else "reject"
    if normalize_market_type(candidate.get("market_type")) == "unknown":
        return "watch" if move_score >= 6 else "reject"
    if move_score >= 6:
        return "recommend"
    if move_score > 0:
        return "watch"
    return "reject"


def waiver_reasoning_summary(
    *,
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    recommendation: str,
    move_score: float,
    need_score: float,
    damage_score: float,
    streamer_penalty: float,
    stash_penalty: float,
    week_delta: float,
) -> str:
    reasons = [
        f"{candidate.get('name')} over {drop_candidate.get('name', 'no drop')} scores {move_score:.2f}",
        f"week delta {week_delta:.2f}",
    ]
    if need_score:
        reasons.append(f"need score {need_score:.2f}")
    if damage_score:
        reasons.append(f"roster damage {damage_score:.2f}")
    if streamer_penalty:
        reasons.append(f"streamer penalty {streamer_penalty:.2f}")
    if stash_penalty:
        reasons.append(f"stash penalty {stash_penalty:.2f}")
    reasons.append(f"recommendation {recommendation}")
    return "; ".join(reasons)


def selected_drop_reason(candidate: dict[str, Any], drop_candidate: dict[str, Any]) -> str:
    if not drop_candidate:
        return "no unprotected drop candidate"
    if same_position_family(candidate, drop_candidate):
        return "lowest risk active roster cut with comparable position coverage"
    return "lowest risk active roster cut across positions"


def waiver_move_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    recommendation_rank = {"recommend": 2.0, "watch": 1.0, "reject": 0.0}.get(str(row.get("recommendation")), 0.0)
    return (
        recommendation_rank,
        float(row.get("move_score") or 0),
        float(row.get("week_value_delta") or 0),
        float(row.get("three_week_value_delta") or 0),
    )


def roster_after_move(
    candidate: dict[str, Any],
    drop_candidate: dict[str, Any],
    roster_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    drop_id = drop_candidate.get("player_id")
    incoming = {
        **candidate,
        "lineup_status": "bench",
        "slot": "BN",
        "active_roster_spot": True,
    }
    return [row for row in roster_players if row.get("player_id") != drop_id] + [incoming]


def roster_position_summary(roster_players: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for row in roster_players:
        position = primary_position(row)
        if not position:
            continue
        group = summary.setdefault(
            position,
            {
                "active_count": 0,
                "playable_count": 0,
                "starter_count": 0,
                "required_starter_count": 0,
                "playable_bench_count": 0,
                "risky_starter_count": 0,
                "top_starter_points": 0.0,
            },
        )
        if not row.get("active_roster_spot", True):
            continue
        group["active_count"] += 1
        projected_points = float(row.get("projected_points") or 0)
        if is_playable_for_waivers(row, position):
            group["playable_count"] += 1
            if str(row.get("lineup_status") or "").lower() == "bench":
                group["playable_bench_count"] += 1
        if str(row.get("lineup_status") or "").lower() == "starter":
            group["starter_count"] += 1
            group["required_starter_count"] += 1
            group["top_starter_points"] = max(float(group["top_starter_points"]), projected_points)
            if is_availability_risk(row):
                group["risky_starter_count"] += 1
    return summary


def is_playable_for_waivers(row: dict[str, Any], position: str) -> bool:
    if not row.get("active_roster_spot", True):
        return False
    if str(row.get("lineup_status") or "").lower() == "reserve":
        return False
    if is_availability_risk(row):
        return False
    return float(row.get("projected_points") or 0) >= playable_threshold(position)


def market_confidence(candidate: dict[str, Any]) -> str:
    explicit = str(candidate.get("market_confidence") or "").strip().lower()
    if explicit in {"high", "medium", "low"}:
        return explicit
    market_type = normalize_market_type(candidate.get("market_type"))
    if market_type == "unknown":
        return "low"
    source_metadata = candidate.get("source_metadata") or {}
    if source_metadata.get("market") == "unknown":
        return "medium"
    return "high"


def primary_position(row: dict[str, Any]) -> str:
    position = str(row.get("position") or "").upper()
    if position:
        return position
    for value in row.get("fantasy_positions") or []:
        normalized = str(value or "").upper()
        if normalized:
            return normalized
    return ""


def is_same_position(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return primary_position(left) == primary_position(right)


def number_or(*values: Any) -> float:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


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
        if (
            float(row.get("projected_gain_over_drop") or 0) > 0
            and row.get("recommendation") in {"recommend", "watch"}
        ):
            grouped[position].append(row)

    return {
        position: sorted(
            rows,
            key=waiver_move_sort_key,
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
    market_type = normalize_market_type(candidate.get("market_type"))
    recommendation = str(candidate.get("recommendation") or "")
    cap_at_medium = market_type == "unknown" or recommendation in {"watch", "reject"}
    if projected_gain >= 6 or net_trend_count >= 1500:
        return "medium" if cap_at_medium else "high"
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
    league: dict[str, Any] | None = None,
    lineup: dict[str, Any],
    matchups: list[dict[str, Any]] | None = None,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    positions: list[str],
    targets_per_team: int,
    offers_per_team: int,
) -> dict[str, Any]:
    users_by_id = {str(user.get("user_id")): user for user in users}
    matchups = matchups or []
    normalized_positions = [str(position).upper() for position in positions]
    league_context = league or {"roster_positions": lineup.get("roster_slots") or []}
    value_index = trade_value_index(
        players=players,
        projection_rows=projection_rows,
        scoring_settings=league_context.get("scoring_settings") or {},
    )
    trade_projection_rows = projection_rows_with_trade_points(projection_rows, value_index)
    projections_by_player = {str(row.get("player_id")): row for row in trade_projection_rows}
    roster_rows_by_id = {
        int(roster.get("roster_id", 0)): enrich_trade_rows(
            trade_roster_rows(
                roster=roster,
                matchup=next(
                    (
                        row
                        for row in matchups
                        if int(row.get("roster_id", 0)) == int(roster.get("roster_id", 0))
                    ),
                    {},
                ),
                slots=starter_slots(league_context),
                players=players,
                projections_by_player=projections_by_player,
            ),
            value_index,
        )
        for roster in rosters
        if roster.get("roster_id") is not None
    }
    if roster_id not in roster_rows_by_id:
        roster_rows_by_id[roster_id] = enrich_trade_rows(
            [
                row
                for row in lineup.get("lineup_table", lineup.get("starters", []) + lineup.get("bench", []))
                if str(row.get("player_id") or "") != "0"
            ],
            value_index,
        )

    analyses_by_roster = trade_roster_analyses(
        league_id=league_id,
        roster_id=roster_id,
        season=season,
        week=week,
        league=league_context,
        users=users,
        rosters=rosters,
        matchups=matchups,
        players=players,
        projection_rows=trade_projection_rows,
        roster_rows_by_id=roster_rows_by_id,
    )
    my_analysis = analyses_by_roster.get(roster_id) or fallback_trade_analysis(
        roster_id=roster_id,
        team_name=lineup.get("team_name", ""),
        rows=roster_rows_by_id.get(roster_id, []),
        positions=normalized_positions,
    )
    my_roster_players = [
        row
        for row in roster_rows_by_id.get(roster_id, [])
        if str(row.get("player_id") or "") != "0"
        and str(row.get("position") or "").upper() in set(normalized_positions)
    ]
    my_offer_pool = trade_candidate_pool(
        rows=my_roster_players,
        analysis=my_analysis,
        positions=normalized_positions,
        side="offer",
        limit=7,
    )

    teams = []
    for roster in rosters:
        other_roster_id = int(roster.get("roster_id", 0))
        if other_roster_id == roster_id:
            continue
        owner = users_by_id.get(str(roster.get("owner_id")))
        roster_rows = [
            row
            for row in roster_rows_by_id.get(other_roster_id, [])
            if str(row.get("position") or "").upper() in set(normalized_positions)
        ]
        analysis = analyses_by_roster.get(other_roster_id) or fallback_trade_analysis(
            roster_id=other_roster_id,
            team_name=owner_display_name(owner),
            rows=roster_rows,
            positions=normalized_positions,
        )
        needs = filter_position_rows(
            analysis.get("need_positions", roster_needs(roster_rows, normalized_positions)),
            normalized_positions,
        )
        surplus = filter_position_rows(
            analysis.get("surplus_positions", roster_surplus(roster_rows, normalized_positions)),
            normalized_positions,
        )
        ask_pool = trade_candidate_pool(
            rows=roster_rows,
            analysis=analysis,
            positions=normalized_positions,
            side="ask",
            limit=8,
        )
        package_matrix = build_trade_package_matrix(
            opponent_roster_id=other_roster_id,
            opponent_team_name=owner_display_name(owner),
            my_rows=my_roster_players,
            opponent_rows=roster_rows,
            my_analysis=my_analysis,
            opponent_analysis=analysis,
            offer_pool=my_offer_pool,
            ask_pool=ask_pool,
            week=week,
            offers_per_team=offers_per_team,
        )
        targets = trade_targets_from_pool(
            ask_pool,
            my_roster_players=my_roster_players,
            my_needs=position_names(my_analysis.get("need_positions", [])),
            targets_per_team=targets_per_team,
        )
        teams.append(
            {
                "roster_id": other_roster_id,
                "team_name": owner_display_name(owner),
                "needs": needs,
                "surplus": surplus,
                "targets": targets,
                "package_matrix": package_matrix,
                "offer_angles": [],
                "reasoning": [],
            }
        )

    suppress_repeated_generic_packages(teams)
    for team in teams:
        matrix = sorted(
            team.get("package_matrix", []),
            key=trade_package_sort_key,
            reverse=True,
        )
        team["package_matrix"] = matrix
        team["offer_angles"] = [
            row
            for row in matrix
            if row.get("recommendation") in {"pursue", "explore", "monitor"}
        ][:offers_per_team]
        team["reasoning"] = trade_reasoning(
            team.get("needs", []),
            team.get("surplus", []),
            team.get("offer_angles", []),
            matrix,
        )

    return {
        "league_id": league_id,
        "roster_id": roster_id,
        "team_name": lineup.get("team_name"),
        "season": season,
        "week": week,
        "supported_package_types": [f"{offer_count}:{ask_count}" for offer_count, ask_count in TRADE_PACKAGE_TYPES],
        "teams": teams,
        "evidence": [
            "trade opportunities use deterministic mutual-fit package scoring across supported package sizes",
            "each opposing roster is included even when no attractive offer angle is found",
            "offer packages prioritize movable and surplus players while protected players are excluded or heavily penalized",
            "recommendations require opponent need fit or meaningful value fairness and are downgraded for roster-balance damage",
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


def trade_value_index(
    *,
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
    scoring_settings: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    return {
        row["player_id"]: row
        for row in build_player_values(
            players=players,
            projection_rows=projection_rows,
            scoring_settings=scoring_settings,
        )
    }


def projection_rows_with_trade_points(
    projection_rows: list[dict[str, Any]],
    value_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for row in projection_rows:
        player_id = str(row.get("player_id") or "")
        if not player_id or row.get("points") not in (None, ""):
            rows.append(row)
            continue
        value = value_index.get(player_id, {})
        rows.append({**row, "points": value.get("week_value", 0)})
    return rows


def trade_roster_rows(
    *,
    roster: dict[str, Any],
    matchup: dict[str, Any],
    slots: list[str],
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    roster_ids = ordered_player_ids(roster.get("players") or [])
    reserve_ids = set(ordered_player_ids(roster.get("reserve") or []))
    starter_ids = ordered_player_ids(matchup.get("starters") or [])
    if not starter_ids:
        starter_ids = inferred_starters(
            roster_ids=[player_id for player_id in roster_ids if player_id not in reserve_ids],
            slots=slots,
            players=players,
            projections_by_player=projections_by_player,
        )
    player_points = matchup.get("players_points") or {}
    starter_set = set(starter_ids)
    rows = [
        player_lineup_summary(
            player_id,
            players=players,
            projections_by_player=projections_by_player,
            player_points=player_points,
            slot=slots[index] if index < len(slots) else f"STARTER_{index + 1}",
            lineup_status="starter",
        )
        for index, player_id in enumerate(starter_ids)
        if player_id in roster_ids and player_id not in reserve_ids
    ]
    for player_id in roster_ids:
        if player_id in starter_set:
            continue
        rows.append(
            player_lineup_summary(
                player_id,
                players=players,
                projections_by_player=projections_by_player,
                player_points=player_points,
                slot="IR" if player_id in reserve_ids else "BN",
                lineup_status="reserve" if player_id in reserve_ids else "bench",
            )
        )
    return rows


def inferred_starters(
    *,
    roster_ids: list[str],
    slots: list[str],
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
) -> list[str]:
    candidates = [
        _player_projection_summary(player_id, players, projections_by_player)
        for player_id in roster_ids
        if str(player_id) != "0"
    ]
    selected: list[str] = []
    remaining = {row["player_id"]: row for row in candidates}
    for slot in slots:
        eligible = [
            row
            for row in remaining.values()
            if is_player_eligible_for_slot(row, slot)
        ]
        if not eligible:
            continue
        pick = max(eligible, key=lambda row: float(row.get("projected_points") or 0))
        selected.append(str(pick["player_id"]))
        remaining.pop(str(pick["player_id"]), None)
    return selected


def enrich_trade_rows(
    rows: list[dict[str, Any]],
    value_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched = []
    for row in rows:
        value = value_index.get(str(row.get("player_id")))
        if not value:
            enriched.append(row)
            continue
        projected_points = row.get("projected_points", 0)
        if not projected_points:
            projected_points = value.get("week_value", 0)
        enriched.append(
            {
                **row,
                "projected_points": projected_points,
                "week_value": value.get("week_value", 0),
                "three_week_value": value.get("three_week_value", 0),
                "season_value": value.get("season_value", 0),
                "decision_value": value.get("decision_value", 0),
                "value_tier": value.get("value_tier", row.get("value_tier", "")),
                "role_tag": value.get("role_tag", ""),
                "value_above_replacement": value.get("value_above_replacement", {}),
            }
        )
    return enriched


def trade_roster_analyses(
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
    roster_rows_by_id: dict[int, list[dict[str, Any]]],
) -> dict[int, dict[str, Any]]:
    try:
        from sleeper_tooling.roster_analysis import build_league_roster_analysis

        report = build_league_roster_analysis(
            league_id=league_id,
            season=season,
            week=week,
            league=league,
            users=users,
            rosters=rosters,
            matchups=matchups,
            players=players,
            projection_rows=projection_rows,
        )
        return {
            int(team["roster_id"]): team
            for team in report.get("teams", [])
            if team.get("roster_id") is not None
        }
    except (ImportError, ValueError, TypeError, KeyError):
        users_by_id = {str(user.get("user_id")): user for user in users}
        analyses = {}
        for roster in rosters:
            current_roster_id = int(roster.get("roster_id", 0))
            owner = users_by_id.get(str(roster.get("owner_id")))
            analyses[current_roster_id] = fallback_trade_analysis(
                roster_id=current_roster_id,
                team_name=owner_display_name(owner),
                rows=roster_rows_by_id.get(current_roster_id, []),
                positions=[],
            )
        if roster_id not in analyses:
            analyses[roster_id] = fallback_trade_analysis(
                roster_id=roster_id,
                team_name="",
                rows=roster_rows_by_id.get(roster_id, []),
                positions=[],
            )
        return analyses


def fallback_trade_analysis(
    *,
    roster_id: int,
    team_name: str,
    rows: list[dict[str, Any]],
    positions: list[str],
) -> dict[str, Any]:
    position_list = positions or sorted({str(row.get("position") or "").upper() for row in rows if row.get("position")})
    return {
        "roster_id": roster_id,
        "team_name": team_name,
        "need_positions": roster_needs(rows, position_list),
        "surplus_positions": roster_surplus(rows, position_list),
        "protected_players": [
            {
                "player_id": row.get("player_id"),
                "name": row.get("name"),
                "position": row.get("position"),
                "reasons": [drop_protection_reason(row, rows)],
            }
            for row in rows
            if drop_protection_reason(row, rows)
        ],
        "movable_players": trade_offer_chips(rows),
        "position_groups": {},
        "roster_balance_score": 70,
    }


def trade_candidate_pool(
    *,
    rows: list[dict[str, Any]],
    analysis: dict[str, Any],
    positions: list[str],
    side: str,
    limit: int,
) -> list[dict[str, Any]]:
    allowed = {position.upper() for position in positions}
    protected_reasons = protected_reason_by_player(analysis)
    movable_ids = {str(row.get("player_id")) for row in analysis.get("movable_players", [])}
    surplus_positions = position_names(analysis.get("surplus_positions", []))
    candidates = []
    for row in rows:
        position = str(row.get("position") or "").upper()
        if allowed and position not in allowed:
            continue
        if not row.get("active_roster_spot", True):
            continue
        if float(row.get("projected_points") or row.get("week_value") or 0) <= 0:
            continue
        lineup_status = str(row.get("lineup_status") or "").lower()
        protected_reason = protected_reasons.get(str(row.get("player_id")), "")
        protected = bool(protected_reason)
        if side == "offer" and lineup_status == "starter":
            continue
        if side == "offer" and protected and str(row.get("player_id")) not in movable_ids:
            candidate_penalty = 30
        elif protected:
            candidate_penalty = 22 if lineup_status == "starter" else 12
        elif str(row.get("player_id")) in movable_ids or position in surplus_positions:
            candidate_penalty = 0
        else:
            candidate_penalty = 5 if side == "ask" else 8
        reasons = []
        if protected_reason:
            reasons.append(f"protected: {protected_reason}")
        if position in surplus_positions:
            reasons.append(f"{position} surplus")
        if str(row.get("player_id")) in movable_ids:
            reasons.append("movable roster piece")
        candidates.append(
            {
                **compact_trade_player(row),
                "lineup_status": row.get("lineup_status", ""),
                "week_value": value_number(row, "week_value", "projected_points"),
                "three_week_value": value_number(row, "three_week_value", "projected_points"),
                "season_value": value_number(row, "season_value", "projected_points"),
                "decision_value": value_number(row, "decision_value", "projected_points"),
                "candidate_penalty": candidate_penalty,
                "candidate_reasons": reasons,
                "protected": protected,
                "surplus_position": position in surplus_positions,
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            -float(row.get("candidate_penalty") or 0),
            float(row.get("surplus_position") is True),
            float(row.get("decision_value") or 0),
            float(row.get("projected_points") or 0),
        ),
        reverse=True,
    )[:limit]


def build_trade_package_matrix(
    *,
    opponent_roster_id: int,
    opponent_team_name: str,
    my_rows: list[dict[str, Any]],
    opponent_rows: list[dict[str, Any]],
    my_analysis: dict[str, Any],
    opponent_analysis: dict[str, Any],
    offer_pool: list[dict[str, Any]],
    ask_pool: list[dict[str, Any]],
    week: int,
    offers_per_team: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    per_type_limit = max(3, offers_per_team)
    for offer_count, ask_count in TRADE_PACKAGE_TYPES:
        if len(offer_pool) < offer_count or len(ask_pool) < ask_count:
            continue
        typed_rows = [
            score_trade_package(
                opponent_roster_id=opponent_roster_id,
                opponent_team_name=opponent_team_name,
                package_type=f"{offer_count}:{ask_count}",
                offer=list(offer),
                ask=list(ask),
                my_rows=my_rows,
                opponent_rows=opponent_rows,
                my_analysis=my_analysis,
                opponent_analysis=opponent_analysis,
                week=week,
            )
            for offer in combinations(offer_pool, offer_count)
            for ask in combinations(ask_pool, ask_count)
        ]
        rows.extend(sorted(typed_rows, key=trade_package_sort_key, reverse=True)[:per_type_limit])
    return sorted(rows, key=trade_package_sort_key, reverse=True)


def score_trade_package(
    *,
    opponent_roster_id: int,
    opponent_team_name: str,
    package_type: str,
    offer: list[dict[str, Any]],
    ask: list[dict[str, Any]],
    my_rows: list[dict[str, Any]],
    opponent_rows: list[dict[str, Any]],
    my_analysis: dict[str, Any],
    opponent_analysis: dict[str, Any],
    week: int,
) -> dict[str, Any]:
    my_needs = position_names(my_analysis.get("need_positions", []))
    opponent_needs = position_names(opponent_analysis.get("need_positions", []))
    my_surplus = position_names(my_analysis.get("surplus_positions", []))
    opponent_surplus = position_names(opponent_analysis.get("surplus_positions", []))
    opponent_need_matched = matched_positions(offer, opponent_needs)
    my_need_solved = matched_positions(ask, my_needs)
    my_values = package_values(ask, offer)
    opponent_values = package_values(offer, ask)
    projected_lineup_gain, upgrade_over = lineup_gain(ask, outgoing=offer, roster_rows=my_rows)
    opponent_projected_gain, opponent_upgrade_over = lineup_gain(offer, outgoing=ask, roster_rows=opponent_rows)
    my_roster_balance_after = roster_balance_after_trade(
        incoming=ask,
        outgoing=offer,
        roster_players=my_rows,
        analysis=my_analysis,
        side="my",
    )
    opponent_roster_balance_after = roster_balance_after_trade(
        incoming=offer,
        outgoing=ask,
        roster_players=opponent_rows,
        analysis=opponent_analysis,
        side="opponent",
    )
    backup_risk = trade_backup_risk(offer, my_rows, my_analysis)
    bye_week_risk = trade_bye_week_risk(
        incoming=ask,
        outgoing=offer,
        roster_players=my_rows,
        week=week,
    )
    value_balance = round(abs(my_values["outgoing_decision"] - my_values["incoming_decision"]), 2)
    fairness_supported = opponent_values["week_delta"] >= -2 and value_balance <= 8
    my_gain = round(projected_lineup_gain + len(my_need_solved) * 2, 2)
    opponent_gain = round(opponent_projected_gain + len(opponent_need_matched) * 2, 2)
    rejection_reasons = trade_rejection_reasons(
        opponent_need_matched=opponent_need_matched,
        my_need_solved=my_need_solved,
        fairness_supported=fairness_supported,
        my_roster_balance_after=my_roster_balance_after,
        opponent_roster_balance_after=opponent_roster_balance_after,
        backup_risk=backup_risk,
        value_balance=value_balance,
    )
    if (
        len(offer) > len(ask)
        and my_values["week_delta"] <= -3
        and not my_need_solved
        and "harms my roster balance" not in rejection_reasons
    ):
        rejection_reasons.append("harms my roster balance")
    candidate_penalty = sum(float(row.get("candidate_penalty") or 0) for row in offer + ask)
    surplus_fit = sum(1 for row in offer if str(row.get("position") or "").upper() in my_surplus)
    opponent_surplus_fit = sum(1 for row in ask if str(row.get("position") or "").upper() in opponent_surplus)
    package_focus_score = opponent_need_focus_score(offer, opponent_need_matched)
    package_shape_bonus = max(0, len(offer) - len(ask)) * 3
    trade_score = round(
        (my_gain * 7)
        + (opponent_gain * 6)
        + len(opponent_need_matched) * 14
        + package_focus_score * 6
        + len(my_need_solved) * 8
        + (surplus_fit + opponent_surplus_fit) * 3
        + package_shape_bonus
        - value_balance * 1.2
        - float(my_roster_balance_after.get("penalty") or 0)
        - float(opponent_roster_balance_after.get("penalty") or 0) * 0.7
        - float(backup_risk.get("penalty") or 0)
        - float(bye_week_risk.get("penalty") or 0)
        - candidate_penalty,
        2,
    )
    recommendation = trade_recommendation(trade_score, rejection_reasons)
    reasoning_summary = trade_package_reasoning(
        package_type=package_type,
        ask=ask,
        offer=offer,
        opponent_need_matched=opponent_need_matched,
        my_need_solved=my_need_solved,
        value_balance=value_balance,
        recommendation=recommendation,
        rejection_reasons=rejection_reasons,
    )
    ask_for = ask[0] if len(ask) == 1 else {"players": [compact_trade_player(row) for row in ask]}
    return {
        "opponent_roster_id": opponent_roster_id,
        "opponent_team_name": opponent_team_name,
        "package_type": package_type,
        "angle_type": package_type,
        "ask": [compact_trade_player(row) for row in ask],
        "ask_for": ask_for,
        "offer": [compact_trade_player(row) for row in offer],
        "offer_projected_points": round(sum(float(row.get("projected_points") or 0) for row in offer), 2),
        "ask_projected_points": round(sum(float(row.get("projected_points") or 0) for row in ask), 2),
        "projected_lineup_gain": projected_lineup_gain,
        "upgrade_over": upgrade_over,
        "opponent_projected_lineup_gain": opponent_projected_gain,
        "opponent_upgrade_over": opponent_upgrade_over,
        "my_gain": my_gain,
        "opponent_gain": opponent_gain,
        "my_week_value_delta": my_values["week_delta"],
        "my_three_week_value_delta": my_values["three_week_delta"],
        "my_season_value_delta": my_values["season_delta"],
        "opponent_week_value_delta": opponent_values["week_delta"],
        "opponent_three_week_value_delta": opponent_values["three_week_delta"],
        "opponent_season_value_delta": opponent_values["season_delta"],
        "opponent_need_matched": opponent_need_matched,
        "my_need_solved": my_need_solved,
        "package_focus_score": package_focus_score,
        "value_balance": value_balance,
        "backup_risk": backup_risk,
        "bye_week_risk": bye_week_risk,
        "my_roster_balance_after": my_roster_balance_after,
        "opponent_roster_balance_after": opponent_roster_balance_after,
        "roster_balance_after": my_roster_balance_after,
        "trade_score": trade_score,
        "recommendation": recommendation,
        "reasoning_summary": reasoning_summary,
        "reasoning": reasoning_summary,
        "rejection_reasons": rejection_reasons,
    }


def trade_rejection_reasons(
    *,
    opponent_need_matched: list[str],
    my_need_solved: list[str],
    fairness_supported: bool,
    my_roster_balance_after: dict[str, Any],
    opponent_roster_balance_after: dict[str, Any],
    backup_risk: dict[str, Any],
    value_balance: float,
) -> list[str]:
    reasons = []
    if not opponent_need_matched and not fairness_supported:
        reasons.append("does not address opponent need or enough value fairness")
    if backup_risk.get("level") == "high" or float(my_roster_balance_after.get("penalty") or 0) >= 18:
        reasons.append("harms my roster balance")
    if float(opponent_roster_balance_after.get("penalty") or 0) >= 28:
        reasons.append("damages opponent roster balance")
    if value_balance > 18 and not (opponent_need_matched and my_need_solved):
        reasons.append("value gap is too wide for a mutual-fit package")
    return reasons


def trade_recommendation(trade_score: float, rejection_reasons: list[str]) -> str:
    if rejection_reasons:
        return "reject"
    if trade_score >= 75:
        return "pursue"
    if trade_score >= 45:
        return "explore"
    if trade_score >= 25:
        return "monitor"
    return "pass"


def trade_package_reasoning(
    *,
    package_type: str,
    ask: list[dict[str, Any]],
    offer: list[dict[str, Any]],
    opponent_need_matched: list[str],
    my_need_solved: list[str],
    value_balance: float,
    recommendation: str,
    rejection_reasons: list[str],
) -> str:
    pieces = [f"{package_type} package"]
    if opponent_need_matched:
        pieces.append("matches their " + "/".join(opponent_need_matched) + " need")
    if my_need_solved:
        pieces.append("helps your " + "/".join(my_need_solved) + " need")
    pieces.append(f"value balance {value_balance:.2f}")
    if rejection_reasons:
        pieces.append("rejected: " + "; ".join(rejection_reasons))
    else:
        pieces.append(f"recommendation {recommendation}")
    return "; ".join(pieces)


def opponent_need_focus_score(
    offer: list[dict[str, Any]],
    opponent_need_matched: list[str],
) -> float:
    if not opponent_need_matched:
        return 0.0
    matched = set(opponent_need_matched)
    positions = [str(row.get("position") or "").upper() for row in offer if row.get("position")]
    if not positions:
        return 0.0
    matching_count = sum(1 for position in positions if position in matched)
    unrelated_count = len(positions) - matching_count
    return round((matching_count / len(positions)) - (unrelated_count * 0.35), 2)


def package_values(incoming: list[dict[str, Any]], outgoing: list[dict[str, Any]]) -> dict[str, float]:
    incoming_week = sum(value_number(row, "week_value", "projected_points") for row in incoming)
    outgoing_week = sum(value_number(row, "week_value", "projected_points") for row in outgoing)
    incoming_three = sum(value_number(row, "three_week_value", "projected_points") for row in incoming)
    outgoing_three = sum(value_number(row, "three_week_value", "projected_points") for row in outgoing)
    incoming_season = sum(value_number(row, "season_value", "projected_points") for row in incoming)
    outgoing_season = sum(value_number(row, "season_value", "projected_points") for row in outgoing)
    incoming_decision = sum(value_number(row, "decision_value", "projected_points") for row in incoming)
    outgoing_decision = sum(value_number(row, "decision_value", "projected_points") for row in outgoing)
    return {
        "incoming_decision": round(incoming_decision, 2),
        "outgoing_decision": round(outgoing_decision, 2),
        "week_delta": round(incoming_week - outgoing_week, 2),
        "three_week_delta": round(incoming_three - outgoing_three, 2),
        "season_delta": round(incoming_season - outgoing_season, 2),
    }


def lineup_gain(
    incoming: list[dict[str, Any]],
    *,
    outgoing: list[dict[str, Any]],
    roster_rows: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    outgoing_ids = {str(row.get("player_id")) for row in outgoing}
    starters = [
        row
        for row in roster_rows
        if str(row.get("lineup_status") or "").lower() == "starter"
        and str(row.get("player_id")) not in outgoing_ids
    ]
    best_gain = 0.0
    replaced: dict[str, Any] = {}
    for target in incoming:
        comparable = [row for row in starters if same_position_family(target, row)]
        if not comparable:
            continue
        replacement = min(comparable, key=lambda row: float(row.get("projected_points") or 0))
        gain = round(float(target.get("projected_points") or 0) - float(replacement.get("projected_points") or 0), 2)
        if gain > best_gain:
            best_gain = gain
            replaced = compact_trade_player(replacement)
    return max(0.0, best_gain), replaced


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


def trade_backup_risk(
    offer: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protected_reasons = protected_reason_by_player(analysis or {})
    reasons = []
    penalty = 0
    outgoing_ids = {row.get("player_id") for row in offer}
    for row in offer:
        reason = protected_reasons.get(str(row.get("player_id")), "")
        if analysis is None:
            reason = drop_protection_reason(row, roster_players)
        if reason:
            reasons.append(f"{row.get('name')} is protected: {reason}")
            penalty += 25
        position = str(row.get("position") or "").upper()
        remaining_playable = [
            player
            for player in roster_players
            if player.get("player_id") not in outgoing_ids
            and str(player.get("position") or "").upper() == position
            and player.get("active_roster_spot", True)
            and float(player.get("projected_points") or 0) >= playable_threshold(position)
        ]
        if len(remaining_playable) < max(1, min(desired_depth(position), 2)):
            reasons.append(f"{position} depth would fall below playable coverage")
            penalty += 10
    level = "high" if penalty >= 25 else "medium" if penalty else "low"
    return {"level": level, "penalty": penalty, "reasons": reasons}


def trade_bye_week_risk(
    *,
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
    week: int,
) -> dict[str, Any]:
    outgoing_ids = {row.get("player_id") for row in outgoing}
    after = [
        player
        for player in roster_players
        if player.get("player_id") not in outgoing_ids
    ] + incoming
    counts = Counter(
        int(player.get("bye_week"))
        for player in after
        if str(player.get("bye_week") or "").isdigit()
    )
    clustered = {
        bye_week: count
        for bye_week, count in counts.items()
        if count >= 4 or bye_week == week and count >= 2
    }
    penalty = sum((count - 3) * 4 if bye_week != week else count * 4 for bye_week, count in clustered.items())
    return {
        "level": "medium" if penalty else "low",
        "penalty": penalty,
        "clustered_byes": clustered,
        "incoming_bye_weeks": [row.get("bye_week", "") for row in incoming],
        "outgoing_bye_weeks": [row.get("bye_week", "") for row in outgoing],
    }


def roster_balance_after_trade(
    *,
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    roster_players: list[dict[str, Any]],
    analysis: dict[str, Any],
    side: str,
) -> dict[str, Any]:
    outgoing_ids = {row.get("player_id") for row in outgoing}
    after = [
        player
        for player in roster_players
        if player.get("player_id") not in outgoing_ids
    ] + incoming
    counts = Counter(str(row.get("position") or "").upper() for row in after if row.get("position"))
    incoming_positions = {str(row.get("position") or "").upper() for row in incoming}
    outgoing_positions = {str(row.get("position") or "").upper() for row in outgoing}
    groups = analysis.get("position_groups") or {}
    raw_need_positions = position_names(analysis.get("need_positions", []))
    need_positions = {
        position
        for position in raw_need_positions
        if position in incoming_positions
        or position in outgoing_positions
        or counts.get(position, 0) > 0
        or int((groups.get(position) or {}).get("required_starter_count") or 0) > 0
    }
    protected_reasons = protected_reason_by_player(analysis)
    warnings = []
    penalty = 0
    for row in outgoing:
        reason = protected_reasons.get(str(row.get("player_id")))
        if reason:
            warnings.append(f"{row.get('name')} is protected: {reason}")
            penalty += 18
    for position in sorted(need_positions):
        if position in outgoing_positions and position not in incoming_positions:
            warnings.append(f"{position} need gets worse after trade")
            penalty += 14
    checked_positions = set(counts) | need_positions | incoming_positions | outgoing_positions
    for position in sorted(checked_positions):
        group = groups.get(position) or {}
        required = int(group.get("required_starter_count") or 0)
        if required == 0 and counts.get(position, 0) == 0 and position not in incoming_positions and position not in outgoing_positions:
            continue
        if required == 0:
            desired = 1 if counts.get(position, 0) or position in incoming_positions or position in outgoing_positions else 0
        else:
            desired = max(required, min(desired_depth(position), required + 1))
        if counts.get(position, 0) < required:
            warnings.append(f"{position} falls below starter requirement")
            penalty += 20
        elif counts.get(position, 0) < desired and position not in incoming_positions:
            warnings.append(f"{position} playable depth thins after trade")
            penalty += 6
    solved_needs = len(need_positions & incoming_positions)
    before_score = int(analysis.get("roster_balance_score") or 70)
    score = max(0, min(100, int(round(before_score + solved_needs * 6 - penalty))))
    level = "high" if penalty >= 24 else "medium" if penalty else "low"
    return {
        "side": side,
        "score": score,
        "before_score": before_score,
        "position_counts": dict(sorted(counts.items())),
        "warnings": warnings,
        "penalty": penalty,
        "level": level,
    }


def trade_targets_from_pool(
    ask_pool: list[dict[str, Any]],
    *,
    my_roster_players: list[dict[str, Any]],
    my_needs: set[str],
    targets_per_team: int,
) -> list[dict[str, Any]]:
    targets = []
    for player in ask_pool:
        projected_lineup_gain, upgrade_over = lineup_gain([player], outgoing=[], roster_rows=my_roster_players)
        position = str(player.get("position") or "").upper()
        targets.append(
            {
                **compact_trade_player(player),
                "projected_lineup_gain": projected_lineup_gain,
                "upgrade_over": upgrade_over,
                "my_need_solved": position in my_needs,
                "opponent_surplus_position": bool(player.get("surplus_position")),
                "target_reason": "; ".join(player.get("candidate_reasons") or []) or "value target",
            }
        )
    return sorted(
        targets,
        key=lambda row: (
            float(row.get("my_need_solved") is True),
            float(row.get("opponent_surplus_position") is True),
            float(row.get("projected_lineup_gain") or 0),
            float(row.get("projected_points") or 0),
        ),
        reverse=True,
    )[:targets_per_team]


def suppress_repeated_generic_packages(teams: list[dict[str, Any]]) -> None:
    rows_by_offer: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for team in teams:
        for row in team.get("package_matrix", []):
            signature = tuple(sorted(str(player.get("player_id")) for player in row.get("offer", [])))
            if signature:
                rows_by_offer.setdefault(signature, []).append(row)
    for rows in rows_by_offer.values():
        if len(rows) <= 1:
            continue
        for row in rows:
            if independently_supported_trade(row):
                continue
            add_trade_rejection(row, "repeated generic package without independent opponent fit", penalty=18)


def independently_supported_trade(row: dict[str, Any]) -> bool:
    return bool(row.get("opponent_need_matched")) and (
        float(row.get("opponent_gain") or 0) > 0
        or float(row.get("opponent_week_value_delta") or 0) >= -2
        or float(row.get("value_balance") or 0) <= 5
    )


def add_trade_rejection(row: dict[str, Any], reason: str, *, penalty: float) -> None:
    reasons = row.setdefault("rejection_reasons", [])
    if reason not in reasons:
        reasons.append(reason)
    row["trade_score"] = round(float(row.get("trade_score") or 0) - penalty, 2)
    row["recommendation"] = "reject"
    row["reasoning_summary"] = trade_package_reasoning(
        package_type=str(row.get("package_type") or ""),
        ask=row.get("ask", []),
        offer=row.get("offer", []),
        opponent_need_matched=row.get("opponent_need_matched", []),
        my_need_solved=row.get("my_need_solved", []),
        value_balance=float(row.get("value_balance") or 0),
        recommendation="reject",
        rejection_reasons=reasons,
    )
    row["reasoning"] = row["reasoning_summary"]


def trade_reasoning(
    needs: list[dict[str, Any]],
    surplus: list[dict[str, Any]],
    offer_angles: list[dict[str, Any]],
    package_matrix: list[dict[str, Any]] | None = None,
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
        types = sorted({str(row.get("package_type")) for row in offer_angles if row.get("package_type")})
        reasons.append("Recommended mutual-fit packages: " + ", ".join(types))
    elif package_matrix:
        rejected = sum(1 for row in package_matrix if row.get("recommendation") == "reject")
        reasons.append(f"No recommended package after mutual-fit scoring; {rejected} matrix rows rejected or downgraded.")
    else:
        reasons.append("No clear mutual-fit package from current roster depth.")
    return reasons


def compact_trade_player(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": row.get("player_id"),
        "name": row.get("name"),
        "position": row.get("position"),
        "team": row.get("team"),
        "projected_points": row.get("projected_points"),
        "week_value": row.get("week_value", row.get("projected_points", 0)),
        "three_week_value": row.get("three_week_value", row.get("projected_points", 0)),
        "season_value": row.get("season_value", row.get("projected_points", 0)),
        "decision_value": row.get("decision_value", row.get("projected_points", 0)),
        "status": row.get("status", ""),
        "injury_status": row.get("injury_status", ""),
        "bye_week": row.get("bye_week", ""),
    }


def protected_reason_by_player(analysis: dict[str, Any]) -> dict[str, str]:
    reasons = {}
    for row in analysis.get("protected_players", []) or []:
        player_id = str(row.get("player_id"))
        row_reasons = row.get("reasons") or []
        reasons[player_id] = "; ".join(str(reason) for reason in row_reasons if reason) or "protected roster piece"
    return reasons


def position_names(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("position") or "").upper() for row in rows if row.get("position")}


def filter_position_rows(rows: list[dict[str, Any]], positions: list[str]) -> list[dict[str, Any]]:
    allowed = {str(position).upper() for position in positions}
    return [
        row
        for row in rows
        if str(row.get("position") or "").upper() in allowed
    ]


def matched_positions(players: list[dict[str, Any]], need_positions: set[str]) -> list[str]:
    return sorted(
        {
            str(row.get("position") or "").upper()
            for row in players
            if str(row.get("position") or "").upper() in need_positions
        }
    )


def value_number(row: dict[str, Any], primary: str, fallback: str) -> float:
    for key in (primary, fallback):
        try:
            return round(float(row.get(key) or 0), 2)
        except (TypeError, ValueError):
            continue
    return 0.0


def trade_package_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
    recommended = 0.0 if row.get("recommendation") == "reject" else 1.0
    return (
        recommended,
        float(row.get("package_focus_score") or 0),
        float(row.get("trade_score") or 0),
        float(row.get("opponent_gain") or 0),
        float(row.get("my_gain") or 0),
    )


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
