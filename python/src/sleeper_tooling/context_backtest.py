"""Deterministic fixture harness for calibrating context flags.

The harness intentionally consumes prepared historical rows rather than calling
providers. This keeps calibration reproducible and avoids coupling scoring to
optional credentials.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CalibrationRules:
    spike_score_threshold: float = 0.70
    role_stability_threshold: float = 0.70
    breakout_multiplier: float = 1.25


@dataclass(frozen=True)
class BacktestFixture:
    season: int
    week: int
    player_id: str
    projected_points: float
    actual_points: float
    spike_score: float
    role_stability_score: float
    role_change: float
    add_candidate: bool = False


@dataclass(frozen=True)
class BacktestMetrics:
    rows: int
    bad_adds: int
    spike_flags: int
    spike_flags_prevented_bad_adds: int
    spike_prevention_rate: float
    breakouts: int
    role_stability_flags: int
    role_stability_flags_caught_breakouts: int
    role_stability_capture_rate: float
    role_change_rows: int
    projection_lag_role_changes: int
    projection_lag_rate: float
    rules: CalibrationRules

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def run_backtest(
    fixtures: Iterable[BacktestFixture],
    *,
    rules: CalibrationRules = CalibrationRules(),
) -> BacktestMetrics:
    rows = tuple(sorted(fixtures, key=lambda row: (row.season, row.week, row.player_id)))
    bad_adds = sum(row.add_candidate and row.actual_points < row.projected_points for row in rows)
    spike_flags = sum(row.spike_score >= rules.spike_score_threshold for row in rows)
    prevented = sum(
        row.add_candidate
        and row.actual_points < row.projected_points
        and row.spike_score >= rules.spike_score_threshold
        for row in rows
    )
    breakouts = sum(
        row.role_change > 0
        and row.actual_points >= row.projected_points * rules.breakout_multiplier
        for row in rows
    )
    stability_flags = sum(
        row.role_stability_score >= rules.role_stability_threshold for row in rows
    )
    caught = sum(
        row.role_change > 0
        and row.actual_points >= row.projected_points * rules.breakout_multiplier
        and row.role_stability_score >= rules.role_stability_threshold
        for row in rows
    )
    role_changes = sum(row.role_change > 0 for row in rows)
    lagging = sum(
        row.role_change > 0 and row.actual_points > row.projected_points for row in rows
    )
    return BacktestMetrics(
        rows=len(rows),
        bad_adds=bad_adds,
        spike_flags=spike_flags,
        spike_flags_prevented_bad_adds=prevented,
        spike_prevention_rate=_rate(prevented, bad_adds),
        breakouts=breakouts,
        role_stability_flags=stability_flags,
        role_stability_flags_caught_breakouts=caught,
        role_stability_capture_rate=_rate(caught, breakouts),
        role_change_rows=role_changes,
        projection_lag_role_changes=lagging,
        projection_lag_rate=_rate(lagging, role_changes),
        rules=rules,
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


DEFAULT_FIXTURES: tuple[BacktestFixture, ...] = (
    BacktestFixture(2025, 1, "bad-add", 12.0, 5.0, 0.90, 0.20, 0.0, True),
    BacktestFixture(2025, 1, "good-add", 8.0, 14.0, 0.10, 0.80, 0.0, True),
    BacktestFixture(2025, 2, "breakout", 10.0, 14.0, 0.20, 0.90, 1.0),
    BacktestFixture(2025, 2, "lagging-projection", 10.0, 13.0, 0.30, 0.30, 1.0),
)


def main() -> None:
    print(json.dumps(run_backtest(DEFAULT_FIXTURES).to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
