---
name: sleeper-def-evaluator
description: Evaluate fantasy football defense and special teams data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging DEF starts, streamers, elite holds, waiver adds, bye replacements, matchup-sensitive scoring, or whether a defense move is worth a roster spot.
---

# Sleeper DEF Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `my_lineup`, `waiver_wire_by_position`,
`player_stat_trends`, and `position_stat_leaders`.

## Evaluation Order

1. Check league scoring first. Sacks, turnovers, points allowed, yards allowed, return TDs, and big-play bonuses can change defense rankings.
2. Treat most defenses as streamable. Protect only elite holds or strong weekly plays with a meaningful multi-week edge.
3. Compare recent actual points, `week_value`, `three_week_value`, `decision_value`, and `value_above_replacement`; a one-week matchup bump is not enough to drop long-term RB, WR, or TE depth.
4. For a named waiver claim or add/drop question, check `player_card` actuals for both the add and drop before making the recommendation.
5. Check opponent, bye week, game lock, injury context, and recent defensive trend when present.
6. For waiver and lineup decisions, make the defense move only when the roster cost is low or the weekly edge is material.

## Stat Signals

Prioritize these signals when present:

- Sacks, interceptions, fumble recoveries, forced fumbles, safeties, blocks, and defensive TDs.
- Points allowed and yards allowed when the league scores them.
- Return TDs and special-teams scoring when included in scoring settings.
- Recent three-week average and projection trend.
- Opponent, matchup, and game context when returned by the tool.

Do not invent betting lines, offensive line weakness, weather, or QB pressure
context unless a tool output explicitly provides it.

## Decision Rules

- Stream defenses aggressively when replacement options are close and the drop is low value.
- Do not reject a defense solely because its projection is low when recent actuals show a clear multi-week scoring edge. Call out the projection/actuals disagreement and decide whether it is matchup-driven, turnover variance, sustainable pressure/scoring, or avoid.
- Hold an elite defense only when multi-week value remains clearly above replacement.
- Avoid carrying a second defense unless bye/game-lock coverage or an unusually strong future matchup justifies the spot.
- Downgrade defenses on bye, locked, or facing a bad matchup when a viable streamer is available.
- Avoid trading meaningful assets for a defense in ordinary roster formats.

## Output Guidance

When explaining a DEF decision, include:

- recent actual points for the add and drop when evaluating a claim
- scoring-rule sensitivity
- matchup and opponent context if present
- current week edge over replacement
- streamable versus elite-hold classification
- drop cost and roster-balance impact
