from __future__ import annotations

from sleeper_tooling.smoke import build_decision_smoke_report, render_decision_smoke_tables


def test_build_decision_smoke_report_summarizes_decision_tools() -> None:
    runner = FakeSmokeRunner()

    report = build_decision_smoke_report(
        runner,
        per_position_limit=2,
        targets_per_team=1,
        offers_per_team=1,
    )

    assert runner.calls == [
        "my_lineup",
        ("waiver_wire_by_position", 2),
        ("trade_opportunities", 1, 1),
    ]
    assert report["current_lineup"] == {
        "team_name": "Me",
        "season": 2026,
        "week": 1,
        "current_total": 12,
        "projected_starter_total": 42,
        "projected_total": 42,
        "projected_active_roster_total": 50,
        "projected_roster_total": 50,
        "active_bench_count": 0,
        "reserve_count": 0,
        "bye_week_warnings": [],
        "lineup_table": [
            {
                "slot": "RB",
                "lineup_status": "starter",
                "name": "Starter RB",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": "",
                "active_roster_spot": True,
                "stash_value": False,
                "actual_points": 7,
                "projected_points": 14,
            }
        ],
    }
    assert report["waiver_wire_by_position"]["by_position"]["RB"][0] == {
        "add": "Free RB",
        "position": "RB",
        "team": "DEN",
        "status": "Active",
        "projected_points": 12,
        "drop": "Bench RB",
        "drop_status": "bench",
        "projected_gain": 4,
        "week_value_delta": 4,
        "three_week_value_delta": 6,
        "season_value_delta": 7,
        "move_score": 22.5,
        "recommendation": "recommend",
        "reasoning_summary": "Free RB over Bench RB scores 22.50",
        "starter_impact": 0,
        "depth_impact": 3,
        "positional_need_score": 2,
        "positional_damage_score": 0,
        "injury_coverage_impact": 0,
        "bye_week_impact": 0,
        "streamer_penalty": 0,
        "stash_penalty": 0,
        "selected_drop_reasoning": "lowest risk active roster cut",
        "rejected_drop_reasoning": [],
        "market_type": "waiver",
        "acquisition_action": "submit_waiver_claim",
        "urgency": "medium",
        "bye_week_warnings": [],
        "faab": {
            "tier": "standard",
            "bid_pct": 9,
            "reasoning": "projects 4.00 points above the drop candidate",
        },
    }
    assert report["trade_opportunities"]["teams"][0]["team_name"] == "Opponent"


def test_render_decision_smoke_tables_returns_markdown_tables() -> None:
    report = build_decision_smoke_report(
        FakeSmokeRunner(),
        per_position_limit=2,
        targets_per_team=1,
        offers_per_team=1,
    )

    markdown = render_decision_smoke_tables(report)

    assert "## Current Lineup" in markdown
    assert "| Projected Total (Starters) | 42 |" in markdown
    assert "| Projected Roster Total | 50 |" in markdown
    assert "| Slot | Status | Player | Team | Pos | NFL Status | Injury | Actual | Projected | Active Spot | Stash |" in markdown
    assert "| RB | starter | Starter RB | DEN | RB | Active |  | 7.00 | 14.00 | true | false |" in markdown
    assert "## Waiver By Position" in markdown
    assert "| Pos | Add | Team | Market | Action | Urgency | Recommendation | Move Score | Reasoning | Drop | Drop Status | Projected | Gain | Week Delta | 3W Delta | Season Delta | Warnings |" in markdown
    assert "| RB | Free RB | DEN | waiver | submit_waiver_claim | medium | recommend | 22.50 | Free RB over Bench RB scores 22.50 | Bench RB | bench | 12.00 | 4.00 | 4.00 | 6.00 | 7.00 | none |" in markdown
    assert "## Trade Opportunities" in markdown
    assert "| Team | Needs | Surplus | Package Type | Ask | Offer | My Gain | Opponent Gain | Value Balance | Trade Score | Recommendation | Reasoning Summary |" in markdown
    assert "| Opponent | WR depth | RB depth | 1:1 | Target WR | Bench RB | 6.00 | 4.00 | 2.00 | 78.25 | pursue | 1:1 package; recommendation pursue |" in markdown


class FakeSmokeRunner:
    def __init__(self) -> None:
        self.calls = []

    def my_lineup(self, **_):
        self.calls.append("my_lineup")
        return {
            "team_name": "Me",
            "season": 2026,
            "week": 1,
            "current_total": 12,
            "projected_starter_total": 42,
            "projected_total": 42,
            "projected_active_roster_total": 50,
            "projected_roster_total": 50,
            "lineup_table": [
                {
                    "slot": "RB",
                    "lineup_status": "starter",
                    "name": "Starter RB",
                    "team": "DEN",
                    "position": "RB",
                    "status": "Active",
                    "injury_status": "",
                        "actual_points": 7,
                        "projected_points": 14,
                        "active_roster_spot": True,
                        "stash_value": False,
                        "ignored": "large field",
                    }
                ],
        }

    def waiver_wire_by_position(self, *, per_position_limit: int, **_):
        self.calls.append(("waiver_wire_by_position", per_position_limit))
        return {
            "week": 1,
            "per_position_limit": per_position_limit,
            "by_position": {
                "RB": [
                    {
                        "add_name": "Free RB",
                        "add_position": "RB",
                        "add_team": "DEN",
                        "status": "Active",
                        "add_projected_points": 12,
                        "drop_name": "Bench RB",
                        "drop_lineup_status": "bench",
                        "projected_gain_over_drop": 4,
                        "week_value_delta": 4,
                        "three_week_value_delta": 6,
                        "season_value_delta": 7,
                        "move_score": 22.5,
                        "recommendation": "recommend",
                        "reasoning_summary": "Free RB over Bench RB scores 22.50",
                        "starter_impact": 0,
                        "depth_impact": 3,
                        "positional_need_score": 2,
                        "positional_damage_score": 0,
                        "injury_coverage_impact": 0,
                        "bye_week_impact": 0,
                        "streamer_penalty": 0,
                        "stash_penalty": 0,
                        "selected_drop_reasoning": "lowest risk active roster cut",
                        "rejected_drop_reasoning": [],
                        "market_type": "waiver",
                        "acquisition_action": "submit_waiver_claim",
                        "urgency": "medium",
                        "faab_tier": "standard",
                        "faab_bid_pct": 9,
                        "faab_reasoning": "projects 4.00 points above the drop candidate",
                    }
                ]
            },
        }

    def trade_opportunities(self, *, targets_per_team: int, offers_per_team: int, **_):
        self.calls.append(("trade_opportunities", targets_per_team, offers_per_team))
        return {
            "week": 1,
            "teams": [
                {
                    "team_name": "Opponent",
                    "needs": [{"position": "WR"}],
                    "surplus": [{"position": "RB"}],
                    "offer_angles": [
                        {
                            "package_type": "1:1",
                            "ask": [{"name": "Target WR"}],
                            "offer": [{"name": "Bench RB"}],
                            "my_gain": 6,
                            "opponent_gain": 4,
                            "value_balance": 2,
                            "trade_score": 78.25,
                            "recommendation": "pursue",
                            "reasoning_summary": "1:1 package; recommendation pursue",
                        }
                    ],
                }
            ],
            "evidence": ["projection-based"],
        }
