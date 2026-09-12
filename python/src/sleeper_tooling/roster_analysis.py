from __future__ import annotations

from collections import Counter
from typing import Any

from sleeper_tooling.decision_reports import (
    FLEX_SLOT_POSITIONS,
    NON_STARTER_SLOTS,
    is_player_eligible_for_slot,
    starter_slots,
)
from sleeper_tooling.reports import owner_display_name, player_name

CORE_POSITION_FAMILIES = ["QB", "RB", "WR", "TE", "K", "DEF"]
ACTIVE_LINEUP_STATUSES = {"starter", "bench"}


def build_league_roster_analysis(
    *,
    league_id: str,
    season: int,
    week: int,
    league: dict[str, Any],
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    matchups: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    projection_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    teams = [
        build_roster_analysis(
            league_id=league_id,
            roster_id=int(roster.get("roster_id", 0)),
            season=season,
            week=week,
            league=league,
            users=users,
            rosters=rosters,
            matchups=matchups,
            players=players,
            projection_rows=projection_rows,
        )
        for roster in sorted(rosters, key=lambda row: int(row.get("roster_id", 0)))
        if roster.get("roster_id") is not None
    ]
    return {
        "league_id": league_id,
        "season": season,
        "week": week,
        "teams": teams,
        "source_metadata": {
            "builder": "sleeper_tooling.roster_analysis.build_league_roster_analysis",
            "team_count": len(teams),
            "inputs": [
                "league",
                "users",
                "rosters",
                "matchups",
                "players",
                "projection_rows",
            ],
        },
    }


def build_roster_analysis(
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
    matchup = next(
        (row for row in matchups if int(row.get("roster_id", 0)) == roster_id),
        {},
    )
    owner = users_by_id.get(str(roster.get("owner_id")))
    slots = starter_slots(league)
    projections_by_player = {str(row.get("player_id")): row for row in projection_rows}
    rows = roster_player_rows(
        roster=roster,
        matchup=matchup,
        slots=slots,
        players=players,
        projections_by_player=projections_by_player,
    )
    position_groups = build_position_groups(rows=rows, slots=slots, week=week)
    risk_context = {
        group["family"]: group for group in position_groups.values()
    }
    injury_risks = build_injury_risks(rows, risk_context)
    bye_week_risks = build_bye_week_risks(rows, week)
    protected_players = build_protected_players(rows, position_groups, week)
    protected_ids = {row["player_id"] for row in protected_players}
    movable_players = build_movable_players(rows, protected_ids)
    surplus_positions = build_surplus_positions(position_groups, rows)
    need_positions = build_need_positions(position_groups, rows, week)
    streaming_slots = build_streaming_slots(position_groups)
    starter_strengths = build_strengths(position_groups, kind="starter")
    starter_weaknesses = build_weaknesses(position_groups, kind="starter")
    depth_strengths = build_strengths(position_groups, kind="depth")
    depth_weaknesses = build_weaknesses(position_groups, kind="depth")
    roster_balance_score = calculate_roster_balance_score(
        position_groups=position_groups,
        injury_risks=injury_risks,
        bye_week_risks=bye_week_risks,
        streaming_slots=streaming_slots,
        movable_players=movable_players,
    )

    return {
        "league_id": league_id,
        "roster_id": roster_id,
        "team_name": owner_display_name(owner),
        "season": season,
        "week": week,
        "starter_strengths": starter_strengths,
        "starter_weaknesses": starter_weaknesses,
        "depth_strengths": depth_strengths,
        "depth_weaknesses": depth_weaknesses,
        "injury_risks": injury_risks,
        "bye_week_risks": bye_week_risks,
        "streaming_slots": streaming_slots,
        "protected_players": protected_players,
        "movable_players": movable_players,
        "surplus_positions": surplus_positions,
        "need_positions": need_positions,
        "position_groups": position_groups,
        "roster_balance_score": roster_balance_score,
        "trade_posture": trade_posture(
            surplus_positions=surplus_positions,
            need_positions=need_positions,
            movable_players=movable_players,
            roster_balance_score=roster_balance_score,
        ),
        "waiver_posture": waiver_posture(
            need_positions=need_positions,
            streaming_slots=streaming_slots,
            roster_balance_score=roster_balance_score,
        ),
        "source_metadata": {
            "builder": "sleeper_tooling.roster_analysis.build_roster_analysis",
            "context_scope": "league_week",
            "player_context_note": "owner, slot, lineup status, reserve status, and has_played_this_week are derived from league/week roster and matchup inputs",
            "lineup_found": bool(matchup),
            "roster_player_count": len(rows),
            "starter_slots": slots,
            "classification_rules": {
                "qb_backup": "1QB backups are low need unless starter is injured, on bye, or backup is a high-value stash",
                "k_def": "K/DEF are classified as elite hold, strong weekly play, streamer, or replacement-level",
                "protection": "reserve/IR, starters, last playable backups, and coverage for questionable starters are protected",
            },
        },
    }


def roster_player_rows(
    *,
    roster: dict[str, Any],
    matchup: dict[str, Any],
    slots: list[str],
    players: dict[str, dict[str, Any]],
    projections_by_player: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    starter_ids = ordered_ids(matchup.get("starters") or [])
    reserve_order = ordered_ids(roster.get("reserve") or [])
    reserve_ids = set(reserve_order)
    all_ids = ordered_ids(
        list(matchup.get("players") or [])
        + list(roster.get("players") or [])
        + reserve_order
    )
    starter_id_set = set(starter_ids)
    player_points = {
        str(player_id): points
        for player_id, points in (matchup.get("players_points") or {}).items()
    }
    rows = []
    for index, player_id in enumerate(starter_ids):
        rows.append(
            player_context_row(
                player_id,
                players=players,
                projection=projections_by_player.get(player_id, {}),
                lineup_status="starter",
                slot=slots[index] if index < len(slots) else f"STARTER_{index + 1}",
                reserve_ids=reserve_ids,
                player_points=player_points,
            )
        )
    for player_id in all_ids:
        if player_id in starter_id_set:
            continue
        rows.append(
            player_context_row(
                player_id,
                players=players,
                projection=projections_by_player.get(player_id, {}),
                lineup_status="reserve" if player_id in reserve_ids else "bench",
                slot="IR" if player_id in reserve_ids else "BN",
                reserve_ids=reserve_ids,
                player_points=player_points,
            )
        )
    return rows


def player_context_row(
    player_id: str,
    *,
    players: dict[str, dict[str, Any]],
    projection: dict[str, Any],
    lineup_status: str,
    slot: str,
    reserve_ids: set[str],
    player_points: dict[str, Any],
) -> dict[str, Any]:
    player = players.get(str(player_id), {})
    position = str(player.get("position") or projection.get("position") or "").upper()
    fantasy_positions = [
        str(value).upper()
        for value in player.get("fantasy_positions") or [position]
        if value
    ]
    projected_points = projection_points(projection)
    has_played = str(player_id) in player_points
    return {
        "player_id": str(player_id),
        "name": player_name(player, str(player_id)),
        "team": player.get("team") or projection.get("team") or "",
        "position": position,
        "fantasy_positions": fantasy_positions,
        "slot": slot,
        "lineup_status": lineup_status,
        "is_starter": lineup_status == "starter",
        "is_bench": lineup_status == "bench",
        "is_reserve": str(player_id) in reserve_ids or lineup_status == "reserve",
        "active_roster_spot": lineup_status in ACTIVE_LINEUP_STATUSES,
        "has_played_this_week": has_played,
        "actual_points": player_points.get(str(player_id), 0),
        "projected_points": projected_points,
        "value_tier": value_tier(position, projected_points),
        "status": player.get("status") or "",
        "injury_status": player.get("injury_status") or "",
        "bye_week": first_present(player, "bye_week", "bye"),
        "k_def_classification": k_def_classification(position, projected_points),
        "source_metadata": {
            "context_scope": "league_week",
            "projection_source": "projection_rows" if projection else "missing_projection",
            "player_source": "sleeper_players" if player else "missing_player",
        },
    }


def build_position_groups(
    *,
    rows: list[dict[str, Any]],
    slots: list[str],
    week: int,
) -> dict[str, dict[str, Any]]:
    families = position_families(slots, rows)
    required_by_family = Counter(slot for slot in slots if slot in CORE_POSITION_FAMILIES)
    groups = {}
    for family in families:
        family_rows = [row for row in rows if row_in_family(row, family)]
        active_rows = [row for row in family_rows if row["active_roster_spot"]]
        starter_rows = [row for row in family_rows if row["lineup_status"] == "starter"]
        bench_rows = [row for row in family_rows if row["lineup_status"] == "bench"]
        reserve_rows = [row for row in family_rows if row["lineup_status"] == "reserve"]
        playable_rows = [
            row
            for row in active_rows
            if is_playable(row, family)
        ]
        playable_bench = [row for row in bench_rows if is_playable(row, family)]
        values = [float(row.get("projected_points") or 0) for row in active_rows]
        best_starter = compact_player(max_by_projection(starter_rows))
        best_bench_cover = compact_player(max_by_projection(playable_bench or bench_rows))
        required_starters = required_starters_for_family(
            family,
            required_by_family=required_by_family,
            slots=slots,
        )
        replacement_risk = replacement_risk_for_group(
            family=family,
            required_starters=required_starters,
            active_rows=active_rows,
            starter_rows=starter_rows,
            playable_rows=playable_rows,
            playable_bench=playable_bench,
            week=week,
        )
        groups[family] = {
            "family": family,
            "playable_count": len(playable_rows),
            "starter_count": len(starter_rows),
            "bench_count": len(bench_rows),
            "reserve_count": len(reserve_rows),
            "playable_bench_count": len(playable_bench),
            "required_starter_count": required_starters,
            "top_value": round(max(values, default=0.0), 2),
            "average_value": round(sum(values) / len(values), 2) if values else 0.0,
            "best_starter": best_starter,
            "best_bench_cover": best_bench_cover,
            "replacement_risk": replacement_risk,
            "tier_distribution": dict(
                sorted(
                    Counter(str(row.get("value_tier") or "replacement") for row in family_rows).items()
                )
            ),
            "k_def_classification": k_def_group_classification(family, active_rows),
        }
    return groups


def build_injury_risks(
    rows: list[dict[str, Any]],
    position_groups: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    risks = []
    for row in rows:
        if not is_availability_risk(row):
            continue
        family = primary_family(row)
        cover = position_groups.get(family, {}).get("best_bench_cover") or {}
        covered = bool(cover.get("player_id")) and cover.get("player_id") != row.get("player_id")
        risks.append(
            {
                **compact_player(row),
                "lineup_status": row.get("lineup_status"),
                "slot": row.get("slot"),
                "coverage_status": "covered" if covered else "thin",
                "best_cover": cover,
                "severity": "high" if row.get("lineup_status") == "starter" and not covered else "medium",
                "reason": "availability risk with bench cover" if covered else "availability risk without playable bench cover",
            }
        )
    return sorted(
        risks,
        key=lambda row: (risk_sort(row.get("severity")), str(row.get("lineup_status") != "starter"), row.get("name", "")),
    )


def build_bye_week_risks(rows: list[dict[str, Any]], week: int) -> list[dict[str, Any]]:
    risks = []
    for row in rows:
        if not row.get("active_roster_spot") or not is_current_bye(row, week):
            continue
        family = primary_family(row)
        risks.append(
            {
                **compact_player(row),
                "lineup_status": row.get("lineup_status"),
                "slot": row.get("slot"),
                "severity": "high" if row.get("lineup_status") == "starter" else "medium",
                "reason": f"{family} is on bye in week {week}",
            }
        )
    by_bye = Counter(
        int(row.get("bye_week"))
        for row in rows
        if row.get("active_roster_spot") and str(row.get("bye_week") or "").isdigit()
    )
    for bye_week, count in sorted(by_bye.items()):
        if count >= 4:
            risks.append(
                {
                    "week": bye_week,
                    "player_count": count,
                    "severity": "high" if count >= 5 else "medium",
                    "reason": f"{count} active roster players share bye week {bye_week}",
                }
            )
    return sorted(risks, key=lambda row: (risk_sort(row.get("severity")), str(row.get("reason", ""))))


def build_protected_players(
    rows: list[dict[str, Any]],
    position_groups: dict[str, dict[str, Any]],
    week: int,
) -> list[dict[str, Any]]:
    protected = []
    for row in rows:
        reasons = protection_reasons(row, rows, position_groups, week)
        if not reasons:
            continue
        protected.append(
            {
                **compact_player(row),
                "lineup_status": row.get("lineup_status"),
                "slot": row.get("slot"),
                "reasons": reasons,
            }
        )
    return sorted(
        protected,
        key=lambda row: (
            str(row.get("lineup_status") != "starter"),
            str(row.get("lineup_status") != "reserve"),
            -float(row.get("projected_points") or 0),
            row.get("name", ""),
        ),
    )


def protection_reasons(
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    position_groups: dict[str, dict[str, Any]],
    week: int,
) -> list[str]:
    if row.get("player_id") in (None, "", "0"):
        return ["placeholder roster row"]
    reasons = []
    if row.get("lineup_status") == "starter":
        reasons.append("current starter")
    if row.get("is_reserve"):
        reasons.append("reserve/IR protected by default")
    family = primary_family(row)
    if family in {"K", "DEF"} and row.get("k_def_classification") == "elite hold":
        reasons.append("elite K/DEF hold")
    if row.get("lineup_status") == "bench":
        if is_last_playable_backup(row, rows, family):
            reasons.append("last playable backup at position")
        if covers_risky_starter(row, rows, week):
            reasons.append("coverage for questionable starter")
        if family == "QB" and is_high_value_qb_stash(row):
            reasons.append("high-value 1QB stash")
    return reasons


def build_movable_players(
    rows: list[dict[str, Any]],
    protected_ids: set[str],
) -> list[dict[str, Any]]:
    movable = []
    for row in rows:
        if row.get("lineup_status") != "bench":
            continue
        if row.get("player_id") in protected_ids:
            continue
        movable.append(
            {
                **compact_player(row),
                "lineup_status": row.get("lineup_status"),
                "slot": row.get("slot"),
                "reason": movable_reason(row),
            }
        )
    return sorted(
        movable,
        key=lambda row: (float(row.get("projected_points") or 0), row.get("position", ""), row.get("name", "")),
    )


def build_surplus_positions(
    position_groups: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    surplus = []
    for family, group in position_groups.items():
        if family == "FLEX":
            continue
        threshold = surplus_threshold(family, group)
        if int(group["playable_count"]) <= threshold:
            continue
        if family == "QB" and has_risky_starter([row for row in rows if primary_family(row) == "QB"]):
            continue
        surplus.append(
            {
                "position": family,
                "playable_count": group["playable_count"],
                "desired_count": threshold,
                "surplus_count": int(group["playable_count"]) - threshold,
                "top_names": [
                    row.get("name")
                    for row in sorted(
                        [row for row in rows if row_in_family(row, family) and is_playable(row, family)],
                        key=lambda item: float(item.get("projected_points") or 0),
                        reverse=True,
                    )[:3]
                ],
                "reason": "playable depth above roster need",
            }
        )
    return sorted(surplus, key=lambda row: (-int(row["surplus_count"]), row["position"]))


def build_need_positions(
    position_groups: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    week: int,
) -> list[dict[str, Any]]:
    needs = []
    for family, group in position_groups.items():
        if family == "FLEX":
            continue
        family_rows = [row for row in rows if row_in_family(row, family)]
        reasons = need_reasons(family, group, family_rows, week)
        if not reasons:
            continue
        needs.append(
            {
                "position": family,
                "playable_count": group["playable_count"],
                "required_starter_count": group["required_starter_count"],
                "bench_count": group["bench_count"],
                "replacement_risk": group["replacement_risk"],
                "reasons": reasons,
            }
        )
    return sorted(needs, key=lambda row: (risk_sort(row["replacement_risk"]), row["position"]))


def need_reasons(
    family: str,
    group: dict[str, Any],
    family_rows: list[dict[str, Any]],
    week: int,
) -> list[str]:
    required = int(group["required_starter_count"])
    playable_count = int(group["playable_count"])
    bench_cover = int(group.get("playable_bench_count") or 0) > 0
    risky_starter = any(
        row.get("lineup_status") == "starter"
        and (is_availability_risk(row) or is_current_bye(row, week))
        for row in family_rows
    )
    if family == "QB" and required <= 1:
        if risky_starter and not bench_cover:
            return ["1QB starter has injury/bye risk without playable cover"]
        if playable_count == 0:
            return ["no playable QB"]
        return []

    reasons = []
    if playable_count < max(1, required):
        reasons.append("playable count below starter requirement")
    if not bench_cover and required > 0:
        reasons.append("no playable bench cover")
    if risky_starter and not bench_cover:
        reasons.append("starter risk lacks coverage")
    if group["replacement_risk"] == "high" and not reasons:
        reasons.append("high replacement risk")
    return reasons


def build_streaming_slots(position_groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    streaming = []
    for family, group in position_groups.items():
        classification = group.get("k_def_classification")
        if family in {"K", "DEF"}:
            if classification in {"streamer", "replacement-level"} or group["playable_count"] == 0:
                streaming.append(
                    {
                        "position": family,
                        "classification": classification or "replacement-level",
                        "replacement_risk": group["replacement_risk"],
                        "reason": f"{family} grades as {classification or 'replacement-level'}",
                    }
                )
        elif family in {"QB", "TE"} and group["replacement_risk"] == "high":
            streaming.append(
                {
                    "position": family,
                    "classification": "streamer",
                    "replacement_risk": group["replacement_risk"],
                    "reason": f"{family} has high replacement risk",
                }
            )
    return sorted(streaming, key=lambda row: (risk_sort(row["replacement_risk"]), row["position"]))


def build_strengths(
    position_groups: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    strengths = []
    for family, group in position_groups.items():
        if family == "FLEX":
            continue
        if kind == "starter":
            starter = group.get("best_starter") or {}
            if starter.get("value_tier") in {"elite", "strong"}:
                strengths.append(
                    {
                        "position": family,
                        "summary": f"{family} starter tier is {starter['value_tier']}",
                        "top_value": group["top_value"],
                        "best_starter": starter,
                        "position_group": group,
                    }
                )
        else:
            if int(group.get("playable_count") or 0) > int(group.get("required_starter_count") or 0):
                cover = group.get("best_bench_cover") or {}
                if cover.get("player_id"):
                    strengths.append(
                        {
                            "position": family,
                            "summary": f"{family} has playable bench cover",
                            "best_bench_cover": cover,
                            "position_group": group,
                        }
                    )
    return sorted(strengths, key=lambda row: (-float(row["position_group"].get("top_value") or 0), row["position"]))


def build_weaknesses(
    position_groups: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    weaknesses = []
    for family, group in position_groups.items():
        if family == "FLEX":
            continue
        risk = group.get("replacement_risk")
        if kind == "starter":
            starter = group.get("best_starter") or {}
            if risk == "high" or starter.get("value_tier") in {"replacement", ""}:
                weaknesses.append(
                    {
                        "position": family,
                        "summary": f"{family} starter group has {risk} replacement risk",
                        "best_starter": starter,
                        "position_group": group,
                    }
                )
        else:
            cover = group.get("best_bench_cover") or {}
            if risk in {"high", "medium"} or not cover.get("player_id"):
                weaknesses.append(
                    {
                        "position": family,
                        "summary": f"{family} depth has {risk} replacement risk",
                        "best_bench_cover": cover,
                        "position_group": group,
                    }
                )
    return sorted(weaknesses, key=lambda row: (risk_sort(row["position_group"].get("replacement_risk")), row["position"]))


def calculate_roster_balance_score(
    *,
    position_groups: dict[str, dict[str, Any]],
    injury_risks: list[dict[str, Any]],
    bye_week_risks: list[dict[str, Any]],
    streaming_slots: list[dict[str, Any]],
    movable_players: list[dict[str, Any]],
) -> int:
    score = 100
    for group in position_groups.values():
        if group["family"] == "FLEX":
            continue
        if group["replacement_risk"] == "high":
            score -= 14
        elif group["replacement_risk"] == "medium":
            score -= 7
        if (group.get("best_starter") or {}).get("value_tier") == "elite":
            score += 3
    score -= 8 * sum(1 for row in injury_risks if row.get("severity") == "high")
    score -= 5 * sum(1 for row in bye_week_risks if row.get("severity") == "high")
    score -= 4 * len(streaming_slots)
    score += min(8, len(movable_players) * 2)
    return max(0, min(100, int(round(score))))


def trade_posture(
    *,
    surplus_positions: list[dict[str, Any]],
    need_positions: list[dict[str, Any]],
    movable_players: list[dict[str, Any]],
    roster_balance_score: int,
) -> dict[str, Any]:
    if surplus_positions and need_positions:
        posture = "consolidate_surplus_for_needs"
    elif surplus_positions and roster_balance_score >= 75:
        posture = "shop_depth_for_upgrade"
    elif need_positions:
        posture = "buy_depth_or_starter"
    else:
        posture = "hold"
    return {
        "posture": posture,
        "surplus_positions": [row["position"] for row in surplus_positions],
        "need_positions": [row["position"] for row in need_positions],
        "movable_player_count": len(movable_players),
        "reason": "trade posture is derived from roster surplus, needs, movable players, and balance score",
    }


def waiver_posture(
    *,
    need_positions: list[dict[str, Any]],
    streaming_slots: list[dict[str, Any]],
    roster_balance_score: int,
) -> dict[str, Any]:
    if streaming_slots:
        posture = "stream_and_patch"
    elif need_positions:
        posture = "prioritize_needs"
    elif roster_balance_score >= 82:
        posture = "opportunistic_upside"
    else:
        posture = "watchlist"
    return {
        "posture": posture,
        "priority_positions": sorted(
            {
                str(row["position"])
                for row in need_positions + streaming_slots
                if row.get("position")
            }
        ),
        "reason": "waiver posture is derived from needs, streaming slots, and roster balance",
    }


def ordered_ids(player_ids: list[Any]) -> list[str]:
    ordered = []
    seen = set()
    for player_id in player_ids:
        if player_id is None:
            continue
        normalized = str(player_id)
        if normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def projection_points(row: dict[str, Any]) -> float:
    for key in ("points", "projected_points", "sleeper_points"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            continue
    return 0.0


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return ""


def position_families(slots: list[str], rows: list[dict[str, Any]]) -> list[str]:
    families = list(CORE_POSITION_FAMILIES)
    for slot in slots:
        if slot in FLEX_SLOT_POSITIONS and slot not in families:
            families.append(slot)
    for row in rows:
        position = str(row.get("position") or "").upper()
        if position and position not in families and position not in NON_STARTER_SLOTS:
            families.append(position)
    return families


def row_in_family(row: dict[str, Any], family: str) -> bool:
    if family in FLEX_SLOT_POSITIONS:
        return is_player_eligible_for_slot(row, family)
    return primary_family(row) == family


def primary_family(row: dict[str, Any]) -> str:
    position = str(row.get("position") or "").upper()
    if position in CORE_POSITION_FAMILIES:
        return position
    positions = [str(value).upper() for value in row.get("fantasy_positions") or []]
    for candidate in CORE_POSITION_FAMILIES:
        if candidate in positions:
            return candidate
    return position


def required_starters_for_family(
    family: str,
    *,
    required_by_family: Counter[str],
    slots: list[str],
) -> int:
    if family in FLEX_SLOT_POSITIONS:
        return sum(1 for slot in slots if slot == family)
    return int(required_by_family.get(family, 0))


def is_playable(row: dict[str, Any], family: str) -> bool:
    if row.get("lineup_status") == "reserve":
        return False
    if not row.get("active_roster_spot"):
        return False
    if is_unavailable(row):
        return False
    return float(row.get("projected_points") or 0) >= playable_threshold(family)


def is_unavailable(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    injury = str(row.get("injury_status") or "").strip().lower()
    if status in {"out", "injured reserve", "ir", "pup", "suspended"}:
        return True
    return injury in {"out", "injured reserve", "ir", "pup", "suspended"}


def is_availability_risk(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    injury = str(row.get("injury_status") or "").strip().lower()
    if injury and injury != "healthy":
        return True
    return bool(status and status not in {"active", "healthy"})


def is_current_bye(row: dict[str, Any], week: int) -> bool:
    try:
        return int(row.get("bye_week")) == int(week)
    except (TypeError, ValueError):
        return False


def value_tier(position: str, points: float) -> str:
    elite, strong, playable = tier_thresholds(position)
    if points >= elite:
        return "elite"
    if points >= strong:
        return "strong"
    if points >= playable:
        return "playable"
    return "replacement"


def tier_thresholds(position: str) -> tuple[float, float, float]:
    return {
        "QB": (22.0, 18.0, 14.0),
        "RB": (16.0, 12.0, 8.0),
        "WR": (16.0, 12.0, 8.0),
        "TE": (12.0, 8.0, 6.0),
        "K": (9.0, 7.0, 5.0),
        "DEF": (9.0, 7.0, 5.0),
    }.get(position.upper(), (12.0, 8.0, 6.0))


def playable_threshold(position: str) -> float:
    return tier_thresholds(position)[2]


def replacement_risk_for_group(
    *,
    family: str,
    required_starters: int,
    active_rows: list[dict[str, Any]],
    starter_rows: list[dict[str, Any]],
    playable_rows: list[dict[str, Any]],
    playable_bench: list[dict[str, Any]],
    week: int,
) -> str:
    if required_starters == 0 and not active_rows:
        return "low"
    if family == "QB" and required_starters <= 1:
        starter = max_by_projection(starter_rows)
        if starter and (is_availability_risk(starter) or is_current_bye(starter, week)):
            return "low" if playable_bench else "high"
        if not playable_rows:
            return "high"
        return "low"
    if len(playable_rows) < max(1, required_starters):
        return "high"
    if any(is_availability_risk(row) or is_current_bye(row, week) for row in starter_rows) and not playable_bench:
        return "high"
    if not playable_bench and required_starters > 0:
        return "medium"
    if len(playable_rows) <= max(1, required_starters):
        return "medium"
    return "low"


def k_def_classification(position: str, points: float) -> str:
    if position not in {"K", "DEF"}:
        return ""
    if points >= 9:
        return "elite hold"
    if points >= 7:
        return "strong weekly play"
    if points >= 5:
        return "streamer"
    return "replacement-level"


def k_def_group_classification(family: str, rows: list[dict[str, Any]]) -> str:
    if family not in {"K", "DEF"}:
        return ""
    top = max_by_projection(rows)
    if not top:
        return "replacement-level"
    return str(top.get("k_def_classification") or "replacement-level")


def compact_player(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    compact = {
        "player_id": row.get("player_id"),
        "name": row.get("name"),
        "team": row.get("team"),
        "position": row.get("position"),
        "projected_points": row.get("projected_points", 0),
        "value_tier": row.get("value_tier", ""),
        "status": row.get("status", ""),
        "injury_status": row.get("injury_status", ""),
        "bye_week": row.get("bye_week", ""),
        "has_played_this_week": row.get("has_played_this_week", False),
    }
    if row.get("k_def_classification"):
        compact["k_def_classification"] = row["k_def_classification"]
    return compact


def max_by_projection(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda row: float(row.get("projected_points") or 0), default=None)


def risk_sort(value: Any) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(value), 3)


def is_last_playable_backup(
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    family: str,
) -> bool:
    if family in {"K", "DEF"}:
        return False
    if not is_playable(row, family):
        return False
    if family == "QB" and one_qb_starter_count(rows) <= 1 and not has_risky_starter(
        [candidate for candidate in rows if row_in_family(candidate, "QB")]
    ):
        return False
    playable_backups = [
        candidate
        for candidate in rows
        if candidate.get("lineup_status") == "bench"
        and row_in_family(candidate, family)
        and is_playable(candidate, family)
    ]
    return len(playable_backups) <= 1


def covers_risky_starter(row: dict[str, Any], rows: list[dict[str, Any]], week: int) -> bool:
    if row.get("lineup_status") != "bench":
        return False
    family = primary_family(row)
    if not is_playable(row, family):
        return False
    return any(
        candidate.get("lineup_status") == "starter"
        and row_in_family(candidate, family)
        and (is_availability_risk(candidate) or is_current_bye(candidate, week))
        for candidate in rows
    )


def is_high_value_qb_stash(row: dict[str, Any]) -> bool:
    return primary_family(row) == "QB" and float(row.get("projected_points") or 0) >= 18


def has_risky_starter(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("lineup_status") == "starter" and is_availability_risk(row) for row in rows)


def one_qb_starter_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("lineup_status") == "starter" and primary_family(row) == "QB"
    )


def surplus_threshold(family: str, group: dict[str, Any]) -> int:
    required = int(group.get("required_starter_count") or 0)
    if family == "QB" and required <= 1:
        return 1
    desired = {"RB": 3, "WR": 4, "TE": 2, "K": 1, "DEF": 1}.get(family, max(1, required + 1))
    return max(required, desired)


def movable_reason(row: dict[str, Any]) -> str:
    family = primary_family(row)
    if family == "QB":
        return "1QB backup has low need without starter injury/bye or high-value stash profile"
    if family in {"K", "DEF"}:
        return f"{family} is {row.get('k_def_classification') or 'replacement-level'}"
    if not is_playable(row, family):
        return "below playable projection threshold"
    return "bench depth above current protection rules"
