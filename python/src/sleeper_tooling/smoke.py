from __future__ import annotations

import argparse
import json
from typing import Any

from sleeper_tooling.mcp_tools import FantasyToolRunner


def build_decision_smoke_report(
    runner: FantasyToolRunner,
    *,
    per_position_limit: int = 3,
    targets_per_team: int = 2,
    offers_per_team: int = 2,
) -> dict[str, Any]:
    lineup = runner.my_lineup()
    waivers = runner.waiver_wire_by_position(
        per_position_limit=per_position_limit,
    )
    trades = runner.trade_opportunities(
        targets_per_team=targets_per_team,
        offers_per_team=offers_per_team,
    )
    lineup_table = [
        {
            "slot": row.get("slot"),
            "lineup_status": row.get("lineup_status"),
            "name": row.get("name"),
            "team": row.get("team"),
            "position": row.get("position"),
            "status": row.get("status", ""),
            "injury_status": row.get("injury_status", ""),
            "active_roster_spot": row.get("active_roster_spot", True),
            "stash_value": row.get("stash_value", False),
            "actual_points": row.get("actual_points", 0),
            "projected_points": row.get("projected_points", 0),
        }
        for row in lineup.get("lineup_table", [])
    ]
    waiver_rows = {
        position: [compact_waiver_row(row) for row in rows]
        for position, rows in (waivers.get("by_position", {}) or {}).items()
    }
    trade_teams = [
        {
            "team_name": team.get("team_name"),
            "needs": [
                f"{need.get('position')} depth"
                for need in team.get("needs", [])
            ],
            "surplus": [
                f"{surplus.get('position')} depth"
                for surplus in team.get("surplus", [])
            ],
            "targets": [compact_trade_target(row) for row in team.get("targets", [])],
            "offer_angles": [
                compact_trade_angle(row)
                for row in team.get("offer_angles", [])
            ],
            "reasoning": team.get("reasoning", []),
        }
        for team in trades.get("teams", [])
    ]
    return {
        "current_lineup": {
            "team_name": lineup.get("team_name"),
            "season": lineup.get("season"),
            "week": lineup.get("week"),
            "current_total": lineup.get("current_total", lineup.get("points_so_far", 0)),
            "projected_starter_total": lineup.get(
                "projected_starter_total",
                lineup.get("projected_starter_points", 0),
            ),
            "projected_total": lineup.get("projected_total", 0),
            "active_bench_count": lineup.get("active_bench_count", lineup.get("bench_count", 0)),
            "reserve_count": lineup.get("reserve_count", 0),
            "bye_week_warnings": lineup.get("bye_week_warnings", []),
            "lineup_table": lineup_table,
        },
        "waiver_wire_by_position": {
            "week": waivers.get("week"),
            "per_position_limit": waivers.get("per_position_limit"),
            "by_position": waiver_rows,
        },
        "trade_opportunities": {
            "week": trades.get("week"),
            "teams": trade_teams,
            "evidence": trades.get("evidence", []),
        },
    }


def compact_waiver_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {
        "add": row.get("add_name"),
        "position": row.get("add_position"),
        "team": row.get("add_team"),
        "status": availability(row),
        "projected_points": row.get("add_projected_points"),
        "drop": row.get("drop_name"),
        "drop_status": row.get("drop_lineup_status"),
        "projected_gain": row.get("projected_gain_over_drop"),
        "market_type": row.get("market_type"),
        "acquisition_action": row.get("acquisition_action"),
        "urgency": row.get("urgency"),
        "bye_week_warnings": row.get("bye_week_warnings", []),
    }
    if "faab_tier" in row:
        output["faab"] = {
            "tier": row.get("faab_tier"),
            "bid_pct": row.get("faab_bid_pct"),
            "reasoning": row.get("faab_reasoning"),
        }
    return output


def compact_trade_target(row: dict[str, Any]) -> dict[str, Any]:
    upgrade = row.get("upgrade_over") or {}
    return {
        "name": row.get("name"),
        "position": row.get("position"),
        "team": row.get("team"),
        "status": availability(row),
        "projected_points": row.get("projected_points"),
        "projected_lineup_gain": row.get("projected_lineup_gain"),
        "upgrade_over": upgrade.get("name"),
    }


def compact_trade_angle(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "angle_type": row.get("angle_type"),
        "ask_for": (row.get("ask_for") or {}).get("name"),
        "offer": [
            player.get("name")
            for player in row.get("offer", [])
        ],
        "offer_projected_points": row.get("offer_projected_points"),
        "projected_lineup_gain": row.get("projected_lineup_gain"),
        "trade_score": row.get("trade_score"),
        "opponent_need_matched": row.get("opponent_need_matched", []),
        "backup_risk": (row.get("backup_risk") or {}).get("level"),
        "bye_week_risk": (row.get("bye_week_risk") or {}).get("level"),
        "reasoning": row.get("reasoning"),
    }


def availability(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    injury_status = str(row.get("injury_status") or "")
    if status and injury_status:
        return f"{status} / {injury_status}"
    return status or injury_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a compact live smoke check for lineup, waiver, and trade MCP workflows."
    )
    parser.add_argument("--per-position-limit", type=int, default=3)
    parser.add_argument("--targets-per-team", type=int, default=2)
    parser.add_argument("--offers-per-team", type=int, default=2)
    args = parser.parse_args()

    report = build_decision_smoke_report(
        FantasyToolRunner(),
        per_position_limit=args.per_position_limit,
        targets_per_team=args.targets_per_team,
        offers_per_team=args.offers_per_team,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
