---
name: sleeper-te-evaluator
description: Evaluate fantasy football tight end data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging TE starts, streamers, elite holds, waiver adds, trade targets, target stability, TD dependency, injury risk, bye coverage, or whether a TE move improves roster balance.
---

# Sleeper TE Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `league_roster_analysis`, `my_lineup`,
`waiver_wire_by_position`, `trade_opportunities`, `player_stat_trends`, and
`position_stat_leaders`.

## Evaluation Order

1. Treat TE as a thin position. Replacement baselines are lower, so a true weekly starter can be materially more valuable than a similar raw point gap at WR.
2. Compare recent actual points, `decision_value`, `three_week_value`, `season_value`, and `value_above_replacement` before choosing a streamer.
3. For a named waiver claim or add/drop question, check `player_card` actuals for both the add and drop before making the recommendation.
4. Separate target stability from TD dependency. Targets and receptions are stickier than one-week TD production.
5. Check league scoring, TE premium rules if present, lineup slots, injury status, depth chart, bye week, and whether the roster has playable TE coverage.
6. For trades, value elite TE holds differently from replacement streamers; do not offer an elite TE unless the return fixes a major roster weakness.

## Stat Signals

Prioritize these signals when present:

- Opportunity: targets and receptions.
- Production: receiving yards and receiving TDs.
- Stability: recent three-week average, projection trend, points trend.
- Context: depth chart order, offensive team, opponent, injury status, bye week.
- Replacement gap: value above replacement and positional scarcity.

If route data or snap share is unavailable, do not infer it directly. Use
targets, receptions, depth chart position, and projection consistency as the
proxy.

## Decision Rules

- Protect elite or strong weekly TEs unless the trade return solves a larger roster problem.
- Do not reject a TE solely because his projection is low when recent actuals show a clear multi-week rise. Call out the projection/actuals disagreement and decide whether it is target stability, TD chase, streamer value, stash value, or avoid.
- Prefer TEs with repeatable targets over TD-only streamers when the projection gap is small.
- Downgrade injured or questionable TEs if the roster lacks a playable backup.
- Do not carry multiple replacement-level TEs unless a bye, injury, or matchup need justifies it.
- For waivers, add a TE when it creates a clear starter upgrade, bye cover, or stash value without damaging RB/WR depth.
- For trades, target opponents with TE weakness and surplus at positions you need.

## Output Guidance

When explaining a TE decision, include:

- recent actual points for the add and drop when evaluating a claim
- target/reception stability
- one-week, three-week, and season value deltas
- scarcity and replacement gap
- injury and bye coverage
- whether this is an elite hold, weekly starter, streamer, stash, backup cover, or avoid
