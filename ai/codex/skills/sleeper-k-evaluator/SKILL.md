---
name: sleeper-k-evaluator
description: Evaluate fantasy football kicker data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging K starts, streamers, elite holds, waiver adds, bye replacements, scoring-rule effects, or whether a kicker move is worth a roster spot.
---

# Sleeper K Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `my_lineup`, `waiver_wire_by_position`,
`player_stat_trends`, and `position_stat_leaders`.

## Evaluation Order

1. Check league scoring first. Distance bonuses, missed-kick penalties, and PAT scoring can change kicker rankings.
2. Treat most kickers as streamable. Only protect kickers classified as elite holds or strong weekly plays by the deterministic model.
3. Compare `week_value`, `three_week_value`, `decision_value`, and `value_above_replacement`; small one-week gaps rarely justify dropping long-term RB, WR, or TE depth.
4. Check bye week, game lock, injury status, weather or game environment only if present in the data.
5. For waivers, prefer low-cost add/drop moves with a clearly replacement-level drop.

## Stat Signals

Prioritize these signals when present:

- Field goal attempts and makes.
- PAT attempts and makes.
- Distance buckets when the scoring settings reward longer field goals.
- Missed field goals or missed PATs when the league penalizes them.
- Recent three-week average and projection trend.
- Team context and opponent only when present.

Do not invent implied team totals, weather, stadium, or betting context unless a
tool output explicitly provides it.

## Decision Rules

- Keep an elite kicker only when the model shows a meaningful multi-week edge over replacement.
- Stream replacement-level kickers for the current week without sacrificing long-term skill-position depth.
- Avoid carrying a second kicker except in rare cases where game lock or bye logistics force it.
- Prefer kicker changes with positive `week_value_delta` and no meaningful `three_week_value_delta` or `season_value_delta` damage.
- Avoid FAAB precision for free agents or unknown market state.

## Output Guidance

When explaining a kicker decision, include:

- scoring-rule sensitivity
- current week edge over replacement
- streamable versus elite-hold classification
- drop cost and roster-balance impact
- bye or lock context
