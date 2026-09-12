from __future__ import annotations

import argparse
import json
from typing import Any

from sleeper_tooling.mcp_tools import FantasyToolRunner


def build_decision_smoke_report(
    runner: FantasyToolRunner,
    *,
    league_id: str | None = None,
    roster_id: int | None = None,
    season: int | None = None,
    week: int | None = None,
    positions: str = "QB,RB,WR,TE,K,DEF",
    per_position_limit: int = 3,
    targets_per_team: int = 2,
    offers_per_team: int = 2,
) -> dict[str, Any]:
    context_args = compact_kwargs(
        league_id=league_id,
        roster_id=roster_id,
        season=season,
        week=week,
    )
    lineup = runner.my_lineup(positions=positions, **context_args)
    waivers = runner.waiver_wire_by_position(
        positions=positions,
        per_position_limit=per_position_limit,
        **context_args,
    )
    trades = runner.trade_opportunities(
        league_id=league_id,
        roster_id=roster_id,
        season=season,
        week=week,
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
            "projected_active_roster_total": lineup.get("projected_active_roster_total", 0),
            "projected_roster_total": lineup.get("projected_roster_total", 0),
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


def compact_kwargs(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def render_decision_smoke_tables(report: dict[str, Any]) -> str:
    lineup = report.get("current_lineup", {})
    waivers = report.get("waiver_wire_by_position", {})
    trades = report.get("trade_opportunities", {})
    sections = [
        "## Current Lineup",
        markdown_table(
            ["Field", "Value"],
            [
                ["Team", value_text(lineup.get("team_name"))],
                ["Season", value_text(lineup.get("season"))],
                ["Week", value_text(lineup.get("week"))],
                ["Current Total", value_text(lineup.get("current_total"))],
                ["Projected Starter Total", value_text(lineup.get("projected_starter_total"))],
                ["Projected Total (Starters)", value_text(lineup.get("projected_total"))],
                ["Projected Active Roster Total", value_text(lineup.get("projected_active_roster_total"))],
                ["Projected Roster Total", value_text(lineup.get("projected_roster_total"))],
                ["Active Bench Count", value_text(lineup.get("active_bench_count"))],
                ["Reserve Count", value_text(lineup.get("reserve_count"))],
                ["Bye Warnings", warnings_text(lineup.get("bye_week_warnings", []))],
            ],
        ),
        markdown_table(
            [
                "Slot",
                "Status",
                "Player",
                "Team",
                "Pos",
                "NFL Status",
                "Injury",
                "Actual",
                "Projected",
                "Active Spot",
                "Stash",
            ],
            [
                [
                    row.get("slot"),
                    row.get("lineup_status"),
                    row.get("name"),
                    row.get("team"),
                    row.get("position"),
                    row.get("status"),
                    row.get("injury_status"),
                    points_text(row.get("actual_points")),
                    points_text(row.get("projected_points")),
                    row.get("active_roster_spot"),
                    row.get("stash_value"),
                ]
                for row in lineup.get("lineup_table", [])
            ],
        ),
        "## Waiver By Position",
        markdown_table(
            [
                "Pos",
                "Add",
                "Team",
                "Market",
                "Action",
                "Urgency",
                "Recommendation",
                "Move Score",
                "Reasoning",
                "Drop",
                "Drop Status",
                "Projected",
                "Gain",
                "Week Delta",
                "3W Delta",
                "Season Delta",
                "Warnings",
            ],
            waiver_table_rows(waivers.get("by_position", {})),
        ),
        "## Trade Opportunities",
        markdown_table(
            [
                "Team",
                "Needs",
                "Surplus",
                "Package Type",
                "Ask",
                "Offer",
                "My Gain",
                "Opponent Gain",
                "Value Balance",
                "Trade Score",
                "Recommendation",
                "Reasoning Summary",
            ],
            trade_table_rows(trades.get("teams", [])),
        ),
        "## Trade Evidence",
        markdown_table(["Evidence"], [[item] for item in trades.get("evidence", [])]),
    ]
    return "\n\n".join(section for section in sections if section)


def waiver_table_rows(by_position: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for position, options in by_position.items():
        for option in options:
            rows.append(
                [
                    position,
                    option.get("add"),
                    option.get("team"),
                    option.get("market_type"),
                    option.get("acquisition_action"),
                    option.get("urgency"),
                    option.get("recommendation"),
                    points_text(option.get("move_score")),
                    option.get("reasoning_summary"),
                    option.get("drop"),
                    option.get("drop_status"),
                    points_text(option.get("projected_points")),
                    points_text(option.get("projected_gain")),
                    points_text(option.get("week_value_delta")),
                    points_text(option.get("three_week_value_delta")),
                    points_text(option.get("season_value_delta")),
                    warnings_text(option.get("bye_week_warnings", [])),
                ]
            )
    return rows


def trade_table_rows(teams: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for team in teams:
        angles = team.get("offer_angles", []) or []
        if not angles:
            rows.append(
                [
                    team.get("team_name"),
                    list_text(team.get("needs", [])),
                    list_text(team.get("surplus", [])),
                    "none",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    list_text(team.get("reasoning", [])),
                ]
            )
            continue
        for angle in angles:
            rows.append(
                [
                    team.get("team_name"),
                    list_text(team.get("needs", [])),
                    list_text(team.get("surplus", [])),
                    angle.get("package_type"),
                    player_names_text(angle.get("ask", [])),
                    player_names_text(angle.get("offer", [])),
                    points_text(angle.get("my_gain")),
                    points_text(angle.get("opponent_gain")),
                    points_text(angle.get("value_balance")),
                    points_text(angle.get("trade_score")),
                    angle.get("recommendation"),
                    angle.get("reasoning_summary"),
                ]
            )
    return rows


def player_names_text(players: Any) -> str:
    if not players:
        return "none"
    names = [
        player.get("name") if isinstance(player, dict) else player
        for player in players
    ]
    return list_text(names)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        rows = [["none"] + [""] * (len(headers) - 1)]
    lines = [
        "| " + " | ".join(escape_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(escape_cell(value) for value in padded[: len(headers)]) + " |")
    return "\n".join(lines)


def escape_cell(value: Any) -> str:
    return value_text(value).replace("|", "\\|").replace("\n", "<br>")


def value_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def points_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return value_text(value)


def list_text(values: Any) -> str:
    if not values:
        return "none"
    return ", ".join(value_text(value) for value in values)


def warnings_text(warnings: Any) -> str:
    if not warnings:
        return "none"
    return "; ".join(
        warning.get("reason", value_text(warning))
        if isinstance(warning, dict)
        else value_text(warning)
        for warning in warnings
    )


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
        "week_value_delta": row.get("week_value_delta"),
        "three_week_value_delta": row.get("three_week_value_delta"),
        "season_value_delta": row.get("season_value_delta"),
        "move_score": row.get("move_score"),
        "recommendation": row.get("recommendation"),
        "reasoning_summary": row.get("reasoning_summary"),
        "starter_impact": row.get("starter_impact"),
        "depth_impact": row.get("depth_impact"),
        "positional_need_score": row.get("positional_need_score"),
        "positional_damage_score": row.get("positional_damage_score"),
        "injury_coverage_impact": row.get("injury_coverage_impact"),
        "bye_week_impact": row.get("bye_week_impact"),
        "streamer_penalty": row.get("streamer_penalty"),
        "stash_penalty": row.get("stash_penalty"),
        "selected_drop_reasoning": row.get("selected_drop_reasoning"),
        "rejected_drop_reasoning": row.get("rejected_drop_reasoning", []),
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
        "package_type": row.get("package_type", row.get("angle_type")),
        "ask": [
            player.get("name")
            for player in row.get("ask", [])
        ],
        "offer": [
            player.get("name")
            for player in row.get("offer", [])
        ],
        "offer_projected_points": row.get("offer_projected_points"),
        "projected_lineup_gain": row.get("projected_lineup_gain"),
        "my_gain": row.get("my_gain"),
        "opponent_gain": row.get("opponent_gain"),
        "value_balance": row.get("value_balance"),
        "trade_score": row.get("trade_score"),
        "recommendation": row.get("recommendation"),
        "reasoning_summary": row.get("reasoning_summary", row.get("reasoning")),
        "opponent_need_matched": row.get("opponent_need_matched", []),
        "my_need_solved": row.get("my_need_solved", []),
        "backup_risk": (row.get("backup_risk") or {}).get("level"),
        "bye_week_risk": (row.get("bye_week_risk") or {}).get("level"),
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
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    report = build_decision_smoke_report(
        FantasyToolRunner(),
        per_position_limit=args.per_position_limit,
        targets_per_team=args.targets_per_team,
        offers_per_team=args.offers_per_team,
    )
    if args.format == "markdown":
        print(render_decision_smoke_tables(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
