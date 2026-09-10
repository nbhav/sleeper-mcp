from __future__ import annotations

from sleeper_tooling.smoke import build_decision_smoke_report


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
        "projected_total": 50,
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


class FakeSmokeRunner:
    def __init__(self) -> None:
        self.calls = []

    def my_lineup(self):
        self.calls.append("my_lineup")
        return {
            "team_name": "Me",
            "season": 2026,
            "week": 1,
            "current_total": 12,
            "projected_starter_total": 42,
            "projected_total": 50,
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

    def waiver_wire_by_position(self, *, per_position_limit: int):
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

    def trade_opportunities(self, *, targets_per_team: int, offers_per_team: int):
        self.calls.append(("trade_opportunities", targets_per_team, offers_per_team))
        return {
            "week": 1,
            "teams": [{"team_name": "Opponent"}],
            "evidence": ["projection-based"],
        }
