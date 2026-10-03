---
name: sleeper-qb-evaluator
description: Evaluate fantasy football quarterback data from Sleeper decision tools, player values, roster analysis, lineup, waiver, trade, projection, and stat trend outputs. Use when judging QB starts, backup value, waiver adds, trade targets, rushing upside, injury risk, bye coverage, or whether a QB move improves the roster.
---

# Sleeper QB Evaluator

Use this skill with the Sleeper decision tools, especially `player_values`,
`roster_analysis`, `league_roster_analysis`, `my_lineup`,
`waiver_wire_by_position`, `trade_opportunities`, `player_stat_trends`, and
`position_stat_leaders`.

## Evaluation Order

1. Confirm league format from roster slots and scoring settings before valuing a backup QB. In one-QB leagues, non-elite backups are usually movable unless they cover a bye, injury, or bad matchup.
2. Compare `decision_value`, `week_value`, `three_week_value`, and `value_above_replacement` before using raw projected points.
3. Separate floor from ceiling. Rushing attempts, rushing yards, and rushing TDs create a stronger weekly floor than passing TD dependency.
4. Check availability: `status`, `injury_status`, current game lock, `bye_week`, and whether the roster has a playable replacement.
5. Use opponent and matchup fields only when they are present in the returned data. Do not invent pressure, pace, or defensive weakness context.
6. For trade and waiver decisions, evaluate the move against the whole roster, not only against another QB.

## Stat Signals

Prioritize these signals when present:

- Passing volume: attempts, completions, passing yards.
- Efficiency and scoring: passing TDs, interceptions, sacks, fumbles.
- Rushing floor: carries, rushing yards, rushing TDs.
- Recent trend: three-week points, points delta, usage movement, and projection movement.
- Context: offensive team, opponent, depth chart position, injury status, and bye.

If a stat key is missing, treat it as missing or zero according to the tool
metadata. Do not treat missing rushing data as proof a QB has no rushing role
unless recent rows consistently support that.

## Decision Rules

- Prefer stable QB starters with rushing contribution over one-week passing-only spike projections when the value gap is small.
- Protect elite or strong weekly QBs, but avoid overvaluing a second QB in one-QB roster formats.
- Downgrade questionable, doubtful, out, benched, backup, or uncertain-depth-chart QBs unless the output shows a clear multi-week stash reason.
- Avoid dropping playable RB, WR, or TE depth for a marginal backup QB gain.
- For bye coverage, prefer short-term add/drop moves only when the dropped player is replacement level or easily replaceable.
- For trades, target teams with weak QB starter value or missing bye coverage and offer from real surplus, not protected RB/WR/TE depth.

## Output Guidance

When explaining a QB decision, include:

- one-week value and three-week value
- rushing floor or lack of rushing signal
- availability and bye coverage
- replacement or roster-balance impact
- whether this is a starter upgrade, backup cover, streamer, stash, or avoid
