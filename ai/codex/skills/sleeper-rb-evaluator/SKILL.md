---
name: sleeper-rb-evaluator
description: Evaluate fantasy football running back data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging RB starts, depth, handcuffs, injury stashes, waiver adds, trade targets, workload trends, bye pressure, or whether an RB move improves roster balance.
---

# Sleeper RB Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `league_roster_analysis`, `my_lineup`,
`waiver_wire_by_position`, `trade_opportunities`, `player_stat_trends`, and
`position_stat_leaders`.

## Evaluation Order

1. Start with roster construction. RB depth is fragile, so protect playable RBs unless the move clearly improves multi-week value or fixes a stronger need.
2. Compare `decision_value`, `three_week_value`, `season_value`, and `value_above_replacement` before chasing a one-week projection.
3. Separate workload from touchdown luck. Carries plus targets are more stable than TD-only production.
4. Check depth chart, injury status, reserve/IR status, bye week, and whether the player is a starter, depth piece, handcuff, or stash.
5. Account for scoring. Receptions and targets matter more in PPR; rushing volume and goal-line usage matter more in standard formats.
6. For trades and waivers, score the move against all roster positions and future coverage, not only RB versus RB.

## Stat Signals

Prioritize these signals when present:

- Rushing workload: carries, rushing yards, rushing TDs.
- Receiving role: targets, receptions, receiving yards, receiving TDs.
- Usage stability: recent three-week average, touch trend, points trend, projection trend.
- Ball security and risk: fumbles and injury tags.
- Context: depth chart order, team, opponent, bye week, starter status, reserve status.

Use available Sleeper keys from normalized stats or raw rows. If targets,
routes, or snap share are unavailable, do not invent them; use receptions,
carries, projections, and depth chart context as proxies.

## Decision Rules

- Protect RB starters, high-volume committee backs, valuable handcuffs, and IR stashes by default.
- Downgrade backs with poor workload signals, uncertain depth chart role, injury risk, or TD-only production.
- Prefer RB adds that improve three-week or season value, not only a small one-week `week_value_delta`.
- Avoid dropping the last playable RB backup unless the roster has clear RB surplus or the add fixes a bigger weakness.
- Treat questionable starters without bench coverage as a roster weakness that increases the value of playable RB depth.
- For trades, do not send away RB depth if the resulting roster creates bye, injury, or flex fragility.

## Output Guidance

When explaining an RB decision, include:

- workload signal: carries plus receiving role
- one-week, three-week, and season value deltas
- injury, depth chart, and stash context
- roster depth after the move
- whether this is a starter, flex, depth, handcuff, stash, streamer, or cut
