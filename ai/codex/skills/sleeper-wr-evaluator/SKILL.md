---
name: sleeper-wr-evaluator
description: Evaluate fantasy football wide receiver data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging WR starts, flex value, target trends, waiver adds, trade targets, injury risk, bye clustering, depth, or whether a WR move improves roster balance.
---

# Sleeper WR Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `league_roster_analysis`, `my_lineup`,
`waiver_wire_by_position`, `trade_opportunities`, `player_stat_trends`, and
`position_stat_leaders`.

## Evaluation Order

1. Start with league scoring and lineup slots. WR value rises in PPR, three-WR, and flex-heavy formats.
2. Compare `decision_value`, `three_week_value`, `season_value`, and `value_above_replacement` before relying on a one-week projection.
3. Prioritize target-earning ability and role stability over single-game TD spikes.
4. Check injury status, depth chart position, team context, opponent, bye week, and whether the player creates or solves WR depth pressure.
5. Evaluate WR moves against RB/TE/flex depth too; a WR add can be correct even when the drop is from another position, but only if roster balance survives.

## Stat Signals

Prioritize these signals when present:

- Opportunity: targets and receptions.
- Production: receiving yards, receiving TDs, explosive scoring if represented.
- Secondary usage: rushing attempts/yards for gadget or dual-role players.
- Reliability: recent three-week average, target trend, points trend, projection trend.
- Context: fantasy positions, depth chart order, team, opponent, injury status, and bye.

If air yards, route share, or snap data is unavailable, do not fake it. Use
targets, receptions, projections, and depth chart context as the stable proxy.

## Decision Rules

- Prefer WRs with target volume and multi-week value over TD-dependent one-week streamers.
- Protect startable WRs and usable flex depth when the roster has three-WR or flex pressure.
- Downgrade questionable WRs without clear replacement coverage, especially when bye clustering exists.
- Avoid dropping RB scarcity or TE coverage for a marginal WR unless WR is a clear roster weakness.
- Treat rookies or role-changing WRs as watchlist candidates when depth chart or target data is incomplete but trend signals are rising.
- For trades, target teams with WR surplus when your roster has WR weakness, and offer from your real surplus rather than repeating the same package.

## Output Guidance

When explaining a WR decision, include:

- target/reception signal
- one-week, three-week, and season value deltas
- flex and roster-balance impact
- injury and bye context
- whether this is a starter, flex, depth hold, upside stash, streamer, or avoid
