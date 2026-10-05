# Player Context Decision Engine Plan

## Problem

The current decision tools are useful, but add/drop and start/sit advice can overfit to either projections or actual points. A player can score well because of a durable role change, or because of a one-week injury fill-in, touchdown spike, broken play, defensive score, or tiny-sample usage. The recommendation engine should identify those cases deterministically before ranking a move.

This should not depend on an LLM to infer context from prose. The LLM can explain the output, but the tool should compute the role, usage, matchup, and volatility signals.

## Goals

- Separate repeatable opportunity from noisy fantasy points.
- Detect one-off production spikes by position.
- Detect durable role changes from usage and depth chart movement.
- Detect trend changes across week intervals so recent role changes are not washed out by stale season-long averages.
- Account for matchup strength in starts, streams, and waiver claims.
- Make add/drop decisions explainable with structured evidence.
- Keep broad waiver screens fast while enabling deeper analysis for named claims.

## Current Baseline

The repo already has useful pieces:

- Normalized weekly stats in `player_week_stat_values`.
- Weekly projections and actuals in `player_week_rows`.
- League matchups and roster context.
- Player depth chart fields from Sleeper player metadata.
- Deterministic move scoring in `score_waiver_move`.
- Roster protection and depth-impact logic.

The main missing layer is a player-context model that scores why a player's value changed.

Important current limitations:

- `players` contains current player metadata and depth chart fields, not reliable week-specific depth chart history.
- Existing league `matchups` are fantasy matchups, not NFL team opponent context.
- `player_week_rows` and stat values include player team, position, stats, projections, and points, but not an explicit NFL opponent join.
- Several desired signals, including routes, snaps, pressure, weather, air yards, and implied totals, are not guaranteed by Sleeper data.

Implementation must distinguish computable signals from missing signals instead of inferring unsupported context.

## Data Inputs

Use the data in this order:

1. Existing normalized Sleeper data: actual stats, projections, matchups, roster snapshots, injuries, player metadata, depth chart order and position.
2. Derived league context: opponent, starter/bench status, roster need, bye pressure, replacement baseline.
3. Optional enriched provider data behind a provider interface: snaps, routes, route participation, target share, carry share, red-zone usage, air yards, defensive pressure, offensive pace, implied team totals, weather.

If enriched data is unavailable, the engine should still work from Sleeper stats and label missing context explicitly.

Do not use fantasy league matchups as NFL matchup context. Before adding
opponent-adjusted scoring, add or import an NFL schedule source that maps
`season`, `week`, `team` to `opponent`, `home_away`, and game metadata.

## Advanced Metric Integration Contract

The [Fantasy Footballers advanced metrics glossary](https://www.thefantasyfootballers.com/analysis/fantasy-football-advanced-metrics-glossary/)
provides useful metric vocabulary, but the glossary is not a data source. A
metric may affect a decision only when a provider supplies the underlying weekly
inputs with player, team, season, week, and provenance fields.

### Metric priority and position mapping

Implement metrics in this order. Each metric must be calculated over the same
trend windows as the rest of the context engine: `last_2_weeks`,
`last_3_weeks`, `last_4_weeks`, `season_to_date`, and any detected pre/post role
change windows.

| Metric | Primary positions | Decision use | Required inputs |
| --- | --- | --- | --- |
| Route percentage | WR, TE | Participation and depth-chart role stability | team pass plays, player routes |
| Targets per route run (TPRR) | WR, TE | Target earning independent of playing time | targets, player routes |
| Yards per route run (YPRR) | WR, TE | Efficiency and breakout quality | receiving yards, player routes |
| First downs per route run (1D/RR) | WR, TE | Chain-moving and down-to-down quality | receiving first downs, player routes |
| Expected fantasy points (xFP) | QB, RB, WR, TE | Usage-based expectation versus actual points | targets, carries, air yards, red-zone usage, scoring rules |
| Explosive rush percentage | RB, QB | Big-play profile versus stable workload | rush attempts, explosive rushes |
| Explosive pass percentage | QB, WR, TE | Big-play profile versus sustainable volume | pass attempts or targets, explosive plays |
| Neutral situation pace | QB, RB, WR, TE | Team opportunity environment | neutral plays, game state, team pass/rush plays |
| EPA | QB, RB, WR, TE, DEF | Efficiency adjusted for game situation | play-level data and league EPA model |
| DVOA | QB, RB, WR, TE, DEF | Opponent and situation-adjusted efficiency | play-level data and provider DVOA model |
| Wins above replacement (WAR) | All fantasy positions | Cross-position replacement-aware value | actual production, consistency, league replacement baseline |

The glossary metrics should complement, not replace, direct opportunity signals.
For example, a high YPRR or TPRR with low route percentage is an efficiency
signal with limited playing-time certainty; it must not be treated as a durable
role change without route or snap growth. Likewise, high explosive-play rates
without touch or target growth should increase volatility and one-off risk.

### Position-specific use

- **QB:** combine dropbacks and rushing attempts with EPA per dropback,
  explosive pass rate, neutral pace, xFP, and opponent DVOA/pressure when
  available. Passing efficiency without volume growth is not a role change.
- **RB:** combine touches, target share, red-zone work, explosive rush rate,
  EPA per rush, and xFP. A high explosive rate on a small carry sample is a
  spike flag, not durable workload evidence.
- **WR:** require route percentage before interpreting TPRR, YPRR, 1D/RR, or
  target share. Compare route growth, target growth, and efficiency growth
  separately so a single long reception cannot masquerade as a role change.
- **TE:** use route percentage and target rate as the primary role signals;
  evaluate blocking-related route limitations, red-zone usage, TPRR, YPRR, and
  xFP before labeling a breakout or waiver upgrade.
- **K:** the glossary does not provide sufficient kicker-specific metrics. Keep
  kicker decisions based on attempts, makes, distance, team scoring environment,
  weather, and matchup sources, with missing-source labels.
- **DEF:** use opponent-adjusted defensive efficiency, pressure/sack rate,
  takeaways, explosive-play prevention, and opponent scoring environment only
  when the provider contract supplies them. Do not infer DVOA or EPA from fantasy
  points allowed.

### Provider and missingness rules

Add these fields to the provider interface when supported:

```text
routes
route_percentage
targets_per_route_run
yards_per_route_run
first_downs_per_route_run
expected_fantasy_points
explosive_rush_percentage
explosive_pass_percentage
neutral_situation_pace
epa
dvoa
wins_above_replacement
```

Each field must include its source, source timestamp, calculation version, and
sample size. Missing metrics must produce explicit reason codes such as
`not_evaluable_missing_routes`, `not_evaluable_missing_xfp`,
`not_evaluable_missing_epa`, or `not_evaluable_missing_dvoa`. Never substitute
fantasy points, projections, or current player metadata for an unavailable
advanced metric.

Advanced metrics are bounded modifiers or evidence fields. They cannot create a
recommendation by themselves, override roster protection, or turn a
projection-only player into a full-confidence add. Every advanced metric must
be backtested by position and window before it affects ranking.

## Computability Tiers

Every context reason code should declare one of these tiers:

- `computed`: enough local data exists to evaluate the signal.
- `partial`: related local data exists, but key context is missing.
- `not_evaluable_missing_source`: the signal requires a source that is not currently available.
- `current_metadata_only`: the signal uses current Sleeper player metadata because week-specific history is unavailable.

Sleeper-only v1 should avoid weak guesses. For example, do not emit
`low_route_big_points` unless route data exists; emit
`not_evaluable_missing_routes` instead.

## Core Derived Scores

Add a deterministic context profile per player/week:

- `opportunity_score`: How much real usage the player had.
- `role_stability_score`: Whether the usage appears repeatable.
- `production_quality_score`: Whether points came from sustainable volume or volatile events.
- `td_dependency_score`: How much value came from touchdowns or rare scoring events.
- `depth_chart_confidence`: Whether the player is high enough on the chart to sustain the role.
- `matchup_adjustment`: Whether the opponent meaningfully helps or hurts the role.
- `small_sample_risk`: Whether the sample is too thin to trust.
- `one_off_risk`: Whether production likely came from a temporary condition.
- `trend_change_score`: Whether recent weeks materially differ from earlier season baseline.
- `season_context_score`: How the player's season-to-date profile compares with recent interval performance.
- `context_confidence`: How complete the available data is.

These scores should be numeric and accompanied by reason codes.

Scores must be bounded. Context should be a capped modifier or gate on top of
existing value calculations, not an unbounded second copy of projection/actual
scoring. Preserve hard roster protection, no-drop penalties, injury/IR handling,
and last-playable-depth rules.

Example output shape:

```json
{
  "player_id": "11630",
  "week": 4,
  "opportunity_score": 71,
  "role_stability_score": 58,
  "production_quality_score": 63,
  "td_dependency_score": 22,
  "depth_chart_confidence": 45,
  "matchup_adjustment": -4,
  "one_off_risk": 31,
  "trend_change_score": 42,
  "season_context_score": 56,
  "context_confidence": 64,
  "role_label": "emerging_rotation",
  "risk_flags": ["low_depth_chart_certainty"],
  "positive_signals": ["multi_week_points_rise", "projection_beaten_in_3_of_4_weeks"]
}
```

## Trend Windows And Change Points

The engine should compare multiple windows, not just current-week projection or
full-season average:

- `current_week`: latest actual/projection context.
- `last_2_weeks`: short-term role and usage signal.
- `last_3_weeks`: near-term stability signal.
- `last_4_weeks`: early-season smoothing window when available.
- `season_to_date`: full-season baseline.
- `pre_change_baseline`: weeks before an identified role/depth/injury change.
- `post_change_window`: weeks after an identified role/depth/injury change.

For each window, compute points, opportunity, production quality, TD dependency,
and confidence. Then compare:

- short-term usage versus season usage
- short-term points versus season points
- projection trend versus actual trend
- pre-change baseline versus post-change window

The engine should emit change-point reason codes when recent weeks materially
change how the player should be valued:

- `role_change_recent`: recent opportunity is meaningfully higher than season baseline.
- `role_loss_recent`: recent opportunity is meaningfully lower than season baseline.
- `projection_lagging_role_change`: actual opportunity/points rose before projections caught up.
- `season_average_stale`: season-to-date average is less relevant because recent role changed.
- `recent_spike_against_stable_usage`: points rose without matching opportunity growth.
- `insufficient_post_change_sample`: possible role change but not enough weeks to trust yet.

Season-to-date should remain visible, but recent role/depth changes should be
able to re-weight the decision. For example, a Week 4 breakout should be
compared against Weeks 1-3 baseline and Weeks 3-4 trend, not flattened into a
single four-week average.

## Position Trend Rules v1

These rules are the first implementation contract for trend handling. The build
phase should translate these into deterministic functions and fixtures rather
than a generic "analyze position trends" prompt.

Shared rules:

- Compute trends from active, non-bye weeks only.
- Use `last_2_weeks` for fast role movement, `last_3_weeks` for confirmation,
  `last_4_weeks` for smoothing, and `season_to_date` as the visible baseline.
- A role trend requires opportunity movement, not points alone.
- A spike is points growth without matching opportunity growth.
- Missing matchup, route, snap, weather, schedule, pressure, air-yards, or
  betting-market inputs must emit `not_evaluable_missing_*` and reduce
  confidence. Do not invent the signal from fantasy points.
- No position may emit `role_change_recent` unless opportunity increased by
  that position's threshold. Points-only increases should emit
  `recent_spike_against_stable_usage`, `projection_disagreement`, or another
  more specific spike flag.
- Context modifier v1 is capped at `-6..+6`.
- Trend-change modifier is capped at `-4..+4`.
- Matchup modifier is capped at `-2..+2`, except DEF streaming may use
  `-3..+3`.
- Severe one-off flags can downgrade an add/start from `recommend` to `watch`
  even when the total score is positive.

### QB

Trend windows:

- Primary: `last_2_weeks` for role movement.
- Confirmation: `last_3_weeks`.
- Baseline: `season_to_date` after at least 3 starts.

Opportunity metrics:

- `dropbacks = pass_attempts + sacks`.
- `rush_attempts`.
- Designed-rush proxy, if an enriched provider supports it.

Production quality metrics:

- Yards per dropback.
- Completion rate.
- Fantasy points per dropback.
- Rush yards per attempt.
- Turnover rate.

Role-change flags:

- `qb_volume_rise`: last-2 dropbacks are at least 15% above season baseline.
- `qb_rushing_role_rise`: last-2 rush attempts are at least 2.5 attempts per
  game above season baseline.

Spike and one-off flags:

- `pass_td_spike_low_volume`: at least 3 passing TDs with fewer than 32 pass
  attempts, or passing TD rate at least 9%.
- `rushing_td_spike`: rushing TD with 4 or fewer rush attempts.
- `garbage_time_unconfirmed`: high points without opportunity growth and no
  supporting role signal.

Matchup inputs:

- Opponent pressure or sack rate.
- Opponent interception rate.
- QB fantasy points allowed.
- Implied team total, if available.

Fallback behavior:

- Sleeper-only mode may use attempts, sacks, rushes, yards, TDs, and turnovers.
- Missing matchup, pressure, schedule, or betting inputs must emit
  `not_evaluable_missing_*` codes.

Caps:

- Trend modifier: `-4..+4`.
- Passing TD spike penalty: up to `-4`.
- Rushing role boost: max `+2`.

### RB

Trend windows:

- Primary: `last_2_weeks` for workload change.
- Confirmation: `last_3_weeks` for durability.
- Change-point windows: compare pre/post injury or depth-chart movement when
  role context exists.

Opportunity metrics:

- `weighted_touches = carries + targets`.
- Receptions as receiving-floor evidence.
- Red-zone carries and targets, if available.
- Team RB touch share, if available.

Production quality metrics:

- Touches per game.
- Targets per game.
- Yards per touch.
- Fantasy points per touch.
- TD share.
- Reception floor.

Role-change flags:

- `rb_touch_share_rise`: last-2 touches are at least 30% above baseline or at
  least 5 touches per game above baseline.
- `rb_receiving_role_rise`: targets rise by at least 2 per game.
- `injury_replacement`: depth or availability context shows a player ahead of
  him is out or limited.

Spike and one-off flags:

- `low_touch_big_points`: at least 15 fantasy points on fewer than 10 touches.
- `td_only_low_usage`: TDs create at least 40% of points and total touches are
  fewer than 12.
- `long_run_spike_unconfirmed`: high yards on low carries without target or
  workload growth.

Matchup inputs:

- Opponent rushing points allowed.
- Opponent receiving points allowed to RBs.
- Goal-line allowance.
- Implied team total.

Fallback behavior:

- Sleeper-only mode may use carries, targets, receptions, yards, TDs, and
  current depth metadata.
- Missing touch share, red-zone, route, schedule, or implied-total inputs must
  emit `not_evaluable_missing_*` codes.

Caps:

- Opportunity/role boost: max `+4`.
- Receiving-role boost: max `+2`.
- Injury fill-in risk penalty: `-2..-5`, based on how clearly the role depends
  on another player's status.

### WR

Trend windows:

- Primary: `last_2_weeks` for target movement.
- Confirmation: `last_3_weeks`.
- Baseline: season target baseline after at least 3 active games.

Opportunity metrics:

- Targets.
- Receptions.
- Routes, route participation, target share, air yards, and red-zone targets,
  if available.

Production quality metrics:

- Targets per game.
- Catch rate.
- Yards per target.
- Fantasy points per target.
- Red-zone targets.

Role-change flags:

- `wr_target_role_rise`: last-2 targets are at least 30% above baseline or at
  least 2.5 targets per game above baseline.
- `wr_depth_chart_breakout`: production and targets rise while current depth
  chart remains low; confidence is capped because role evidence conflicts.

Spike and one-off flags:

- `low_target_big_points`: at least 14 fantasy points on fewer than 5 targets.
- `long_td_spike`: high points with yards per target at least 18 and fewer than
  6 targets.
- `multi_td_low_volume`: at least 2 TDs on fewer than 7 targets.

Matchup inputs:

- Opponent WR fantasy points allowed.
- Explosive pass plays allowed.
- Opponent pass-rate environment.
- CB matchup only when an enriched provider supports it.

Fallback behavior:

- Sleeper-only mode may use targets, receptions, yards, TDs, points, and current
  depth metadata.
- Missing routes, target share, air yards, schedule, or CB data must emit
  `not_evaluable_missing_*` codes.

Caps:

- Target-role trend boost: max `+4`.
- Low-target spike penalty: max `-5`.
- Depth-chart disagreement confidence cap: `70`.

### TE

Trend windows:

- Primary: `last_2_weeks` for target movement.
- Confirmation: `last_3_weeks` for trust.
- Baseline: season target baseline after at least 3 active games.

Opportunity metrics:

- Targets.
- Receptions.
- Routes and route participation, if available.
- Red-zone targets, if available.

Production quality metrics:

- Targets per game.
- Receptions per game.
- Red-zone involvement.
- Fantasy points per target.

Role-change flags:

- `te_target_role_rise`: last-2 targets are at least 2 per game above baseline
  or at least 30% above baseline.
- `te_red_zone_role_rise`: red-zone targets appear in 2 straight weeks.

Spike and one-off flags:

- `te_td_only_week`: TDs create at least 45% of points and targets are fewer
  than 5.
- `low_route_big_points`: only emit when route data exists; otherwise emit
  `not_evaluable_missing_routes`.

Matchup inputs:

- Opponent TE fantasy points allowed.
- Red-zone TE allowance.
- LB or safety matchup only when an enriched provider supports it.

Fallback behavior:

- Sleeper-only mode may use targets, receptions, yards, TDs, points, and current
  depth metadata.
- Blocking-heavy role cannot be inferred without routes or snaps.

Caps:

- TE trend boost: max `+3`.
- TD-only penalty: max `-5`.
- Missing route confidence penalty: `-10`.

### K

Trend windows:

- Primary: `last_3_weeks`.
- Smoothing: `last_4_weeks`.
- Do not upgrade or downgrade a kicker from `last_1_week` alone unless there is
  role or injury news.

Opportunity metrics:

- Field goal attempts.
- PAT attempts.
- Team scoring chances, if available.

Production quality metrics:

- Total kick attempts per game.
- PAT floor.
- Field-goal distance mix.
- Indoor/outdoor and wind/weather context, if available.

Role-change flags:

- `k_team_opportunity_rise`: last-3 total kick attempts are at least 25% above
  baseline.
- Kicker role changes should otherwise be rare and tied to job status, injury,
  team offense, or scoring environment.

Spike and one-off flags:

- `long_kick_spike`: at least 40% of points came from 50-plus-yard field goals.
- `low_attempt_kicker_spike`: high score on 2 or fewer total kick attempts.

Matchup inputs:

- Implied team total.
- Spread.
- Opponent red-zone stall tendency.
- Weather and wind.

Fallback behavior:

- Sleeper-only mode may use field goal attempts, PAT attempts, made kicks,
  missed kicks, and points.
- Missing weather, schedule, implied-total, or red-zone-stall inputs must emit
  `not_evaluable_missing_*` codes.

Caps:

- Kicker trend boost: max `+2`.
- Matchup modifier: max `+2`.
- One-week spike penalty: max `-4`.

### DEF

Trend windows:

- Primary: `last_3_weeks` for sacks and defensive performance.
- Smoothing: `last_4_weeks`.
- `last_1_week` should only drive movement when paired with injury, opponent,
  or depth news.

Opportunity metrics:

- Sacks.
- Takeaways.
- Points allowed.
- Yards allowed.
- Pressures, if available.

Production quality metrics:

- Sacks per game.
- Points allowed trend.
- Takeaways regressed toward baseline.
- Opponent-adjusted pressure or turnover creation, if available.

Role-change flags:

- `def_pressure_rise`: last-3 sacks are at least 30% above baseline.
- `def_points_allowed_improving`: last-3 points allowed improves by at least
  20% versus baseline.

Spike and one-off flags:

- `def_td_spike`: any defensive or special-teams TD.
- `turnover_spike_without_pressure`: at least 3 takeaways with fewer than 3
  sacks and no supporting pressure data.
- `score_dependent_def_week`: defensive or special-teams TDs create at least
  40% of points.

Matchup inputs:

- Opponent sacks allowed.
- Opponent turnover rate.
- Opponent implied points.
- Opposing QB injury or status.

Fallback behavior:

- Sleeper-only mode may use sacks, takeaways, defensive TDs, points allowed,
  yards allowed, and points.
- Missing pressure, NFL schedule, implied-total, or QB-status inputs must emit
  `not_evaluable_missing_*` codes.

Caps:

- DEF trend boost: max `+3`.
- DEF matchup modifier: max `+3` for streaming and max `+2` for season-long
  value.
- TD spike penalty: max `-5`.

## One-Off Detection

Create explicit flags:

- `td_spike_low_usage`: High points, low opportunity, TD-heavy.
- `injury_replacement`: Role likely tied to another player's injury/status.
- `low_route_big_points`: WR/TE production without enough routes.
- `low_touch_big_points`: RB production without enough touches.
- `turnover_td_spike`: DEF production from volatile defensive scores.
- `projection_disagreement`: Actuals rising while projections remain low, or projections high while actuals are weak.
- `depth_chart_disagreement`: Production rising while depth chart remains low.
- `season_average_stale`: Full-season average conflicts with recent role window.
- `change_point_unconfirmed`: Recent interval changed, but sample is too small or missing role evidence.

Each flag should include input evidence and affect `one_off_risk`.

V1 should also include exact formulas, thresholds, and caps before
implementation:

- opportunity denominator by position
- TD dependency formula
- recent-window slope formula
- trend-window and season-to-date weighting formula
- change-point detection thresholds
- minimum sample thresholds
- confidence penalties for missing inputs
- reason-code schema and severity levels

Until those formulas exist, context scores should not be wired into
`score_waiver_move`.

## Matchup Model

Add opponent-adjusted context by position:

- QB: opponent fantasy points allowed to QB, sacks, interceptions, pressure proxy.
- RB: rushing points allowed, receiving points allowed to RB, goal-line allowance.
- WR/TE: points allowed by receiver position, target efficiency allowed, explosive plays allowed.
- K: attempts allowed, points environment, weather when available.
- DEF: opponent sacks allowed, turnovers, implied points, offensive injuries when available.

Start with normalized league/NFL historical stats available in the local database. Add richer external inputs later through a provider interface.

Prerequisite: add an NFL schedule/opponent data source. The matchup model needs
team-opponent joins before it can compute points allowed by position or matchup
strength. If no NFL opponent source is available, return
`not_evaluable_missing_nfl_schedule` and do not apply matchup adjustment.

## Data Model Additions

Add normalized tables or materialized views:

- `player_week_context`
  - `season`, `week`, `player_id`, `position`
  - `model_version`
  - `league_id` or `scoring_profile_hash` when a score depends on league scoring
  - `input_sources`
  - `missing_inputs`
  - derived scores and JSON evidence
- score components JSON
  - trend window JSON, including current week, rolling intervals, season-to-date, pre-change, and post-change values
  - `context_confidence`
  - `updated_at`
- `team_week_context`
  - opponent, pace/scoring proxies, matchup strength by position
- `player_role_snapshots`
  - `season`, `week`, `team`, `player_id`
  - depth chart order, projected role label, injury/status, source, timestamp
  - `current_metadata_only` flag when historical role data is unavailable
- `player_week_availability`
  - `season`, `week`, `team`, `player_id`
  - status, injury status, reserve/IR/PUP tags, source, timestamp
- `team_week_schedule`
  - `season`, `week`, `team`, `opponent`, `home_away`, game timestamp, source
- Optional provider tables for snaps/routes/matchups once an external source is selected.

Keep raw provider payloads separate from derived context, like the existing raw cache versus normalized read model.

## API And Tool Changes

Add or extend MCP tools:

- `player_usage_context`
  - Returns role, usage, volatility, depth chart, matchup, and confidence for one player.
- `compare_player_usage_context`
  - Compares add/drop or start/sit pairs with actuals, projections, and context scores.
- `waiver_wire_by_position`
  - Include context scores, role labels, one-off flags, and confidence.
- `lineup_recommendations`
  - Include context-aware start/sit reasoning.
- `player_card`
  - Add optional context section with weekly actual/projection plus role evidence.

Avoid naming the new computed profile `player_context`; the codebase already has
a lightweight `player_context()` helper for basic metadata.

## Scoring Integration

Change waiver and lineup scoring from:

```text
move_score = value deltas + depth/need/injury/bye/trend adjustments
```

to:

```text
move_score =
  value deltas
  + capped opportunity/role modifier
  + capped trend-change modifier
  + capped matchup modifier
  + depth/need/injury/bye adjustments
  - one-off risk
  - role uncertainty
  - roster damage
```

Projection and actuals remain inputs, but neither is decisive alone.

Because `player_values` already blends projections and recent scoring, context
must avoid double-counting points. Recommended v1 behavior:

- use context as a bounded modifier, for example `-6` to `+6`
- split the context modifier into bounded subcomponents so recent trend changes do not double-count points already included in value deltas
- use severe one-off flags as gates that downgrade `recommend` to `watch`
- never let context override no-drop, protected-player, or roster-damage rules
- emit score components so every change is auditable

## Rollout Plan

1. Stat-key audit
   - Inventory Sleeper stat keys already synced for stats and projections.
   - Map keys by position into opportunity, production, and volatility groups.
   - Identify missing fields that require an enriched provider.
   - Classify every desired reason code as `computed`, `partial`, or `not_evaluable_missing_source`.

2. NFL schedule and temporal snapshot audit
   - Verify whether Sleeper weekly rows include opponent-like fields for any source.
   - Add `team_week_schedule` or a provider-backed equivalent before matchup scoring.
   - Add weekly role and availability snapshots, or explicitly mark current metadata as low-confidence.

3. Context profile v1 from existing data
   - Build `player_context.py`.
   - Use targets, receptions, carries, pass attempts, rush attempts, sacks, turnovers, field goals, defensive sacks/turnovers, depth chart fields, injuries, actuals, and projections.
   - Rename the module/tool if needed to avoid colliding with existing metadata helpers.
   - Define bounded formulas, reason codes, and confidence penalties before applying scores.
   - Add rolling trend windows and season-to-date comparisons before recommendations consume context output.
   - Add unit tests with mocked rows for spike versus sustainable cases.

4. Pairwise claim comparison
   - Add `compare_player_usage_context(add, drop)`.
   - Require this in named add/drop paths.
   - Surface projection/actual/context disagreement explicitly.

5. Waiver integration
   - Add context fields to `waiver_wire_by_position` and `lineup_recommendations`.
   - Adjust `score_waiver_move` with capped context modifiers only after v1 context fixtures pass.

6. Matchup model v1
   - Derive opponent fantasy points allowed by position from normalized stats.
   - Add matchup adjustment and confidence.

7. Enriched provider interface
   - Add provider abstraction for snaps, routes, target share, air yards, red-zone use, weather, and implied totals.
   - Keep the engine provider-neutral.
   - Continue returning useful lower-confidence output when enriched data is missing.

8. Backtest and calibration
   - Backtest waiver recommendations across prior weeks.
   - Measure how often spike flags prevent bad adds and how often role-stability flags catch breakouts.
   - Tune weights with deterministic fixtures, not ad hoc LLM judgment.

## Acceptance Criteria

- A named add/drop claim returns actuals, projections, context scores, and reason codes for both players.
- A player with high points but low usage is flagged as a spike risk.
- A player with rising usage and rising points is classified as a role-change candidate even when projections lag.
- A Week 4 role change can re-weight the decision versus Weeks 1-3 or season-to-date baselines without hiding the older baseline.
- A recent spike without opportunity growth is separated from a recent role change.
- A temporary injury starter is labeled as such when depth chart or injury context supports it.
- If the necessary role, route, snap, or matchup input is missing, the output says so with `not_evaluable_missing_*` rather than pretending.
- Context scoring is versioned and exposes score components.
- K and DEF spike detection have dedicated formulas, not reused skill-position logic.
- Waiver recommendations include enough structured evidence for the LLM to explain, not infer, the decision.
- Unit tests cover every position group and golden cases:
  - high points with low usage
  - rising usage with lagging projections
  - high projections with weak actuals
  - injury fill-in
  - buried depth-chart breakout
  - K long-kick spike
  - DEF touchdown or turnover spike
  - missing matchup source

## Immediate Next Step

Implement the stat-key and source audit first. Without knowing which opportunity,
volatility, role, and NFL opponent fields are currently present, any scoring
model would either underuse available data or assume fields that do not exist.
