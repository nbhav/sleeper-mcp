---
name: sleeper-weekly-scout
description: Back-test Sleeper weekly leaders, compare week-over-week changes, inspect current lineups, and filter cleaner waiver-wire targets from this repo's deterministic MCP tools and Dockerized CLI fallbacks.
---

# Sleeper Weekly Scout

Use this skill when a user wants historical top players, week-over-week movement, lineup decisions, or waiver-wire shortlists without raw Sleeper endpoint noise.

## Core Workflow

1. Prefer deterministic MCP tools when the MCP server is registered.
2. Use JSON output unless the user explicitly wants a table.
3. Fall back to the repo CLI through Docker Compose only when MCP is unavailable.
4. Do not install packages or create a virtualenv on the host.
5. Use `make local-up` before local Docker workflows when setup is needed.
6. After Docker workflow runs, use `make teardown` to stop Compose resources and prune stopped containers plus dangling images when appropriate.
7. Use `decision_smoke_report` for display-ready MCP smoke tables, or `make decision-smoke` for local CLI validation.

## Historical Leaders

For historical leaders or week-over-week movement, call:

```text
weekly_performance_backtest
```

Recommended inputs:

```json
{
  "season": 2025,
  "start_week": 1,
  "weeks": 2,
  "positions": "QB,RB,WR,TE,K,DEF",
  "source": "stats",
  "limit": 5,
  "movement_limit": 5
}
```

CLI fallback for a single week back-test:

```bash
make sleeper ARGS="best-week --season <season> --week <week> --source stats --limit 5 --output json"
```

Rules:

- Use `--source stats` for true back-tests.
- Use `--league-id <league_id>` when the ranking should reflect league scoring.
- Keep the default positions `QB,RB,WR,TE,K,DEF` unless the user narrows scope.

## Week-Over-Week Changes

Use `weekly_performance_backtest` for `x` weeks. It compares rows by `player_id`, `position`, and `points`.

The deterministic tool returns these deltas:

- `points_delta = current_week_points - previous_week_points`
- `rank_delta = previous_rank - current_rank`

Prefer reporting:

- top risers
- top fallers
- players that appear or disappear across the sampled weeks

If the user asks for a compact trend view, summarize only the top movers per position.

## Waiver Wire

For actionable waiver-wire filtering, call:

```text
waiver_wire_watch
```

For top waiver options by position, call:

```text
waiver_wire_by_position
```

Recommended inputs:

```json
{
  "league_id": "<league_id>",
  "season": 2026,
  "week": 1,
  "positions": "QB,RB,WR,TE,K,DEF",
  "lookback_hours": 24,
  "trend_limit": 100,
  "limit": 25,
  "recent_weeks": 3
}
```

Use these lower-level tools only when debugging or when the user explicitly wants the simpler view:

```text
waiver_watch
free_agent_watch
```

CLI fallback:

```bash
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --limit 25 --output json"
```

Rules:

- Use the default `QB,RB,WR,TE,K,DEF` when the user asks broadly for waiver or free-agent options.
- Narrow to `RB,WR,TE` when the user explicitly wants skill-position bench churn.
- Use available-player output, not raw trending lists, when the goal is actionable waiver suggestions.
- Use `free_agent_watch` when the user wants a cleaner available-player ranking without trend pressure.
- Keep the result focused on projected value and roster availability.
- Explain `acquisition_action`, `urgency`, add/drop reasoning, and FAAB fields only when `market_type` is `waiver`.

## Lineup Decisions

For the user's current starters and bench, call:

```text
my_lineup
```

Prefer `lineup_table` when presenting a roster because it includes starters, bench, and reserve/IR rows with `lineup_status`, `active_roster_spot`, `stash_value`, `status`, `injury_status`, `actual_points`, and `projected_points`. Mention `week`, `current_total`, `projected_starter_total`, `active_bench_count`, and `reserve_count`.

For start/sit changes, add/drop comparisons, watchlist priority, and FAAB hints, call:

```text
lineup_recommendations
```

Recommended inputs:

```json
{
  "league_id": "<league_id>",
  "roster_id": 1,
  "positions": "QB,RB,WR,TE,K,DEF",
  "trend_limit": 100,
  "lookback_hours": 24,
  "min_delta": 1,
  "limit": 10
}
```

Rules:

- Omit `league_id` and `roster_id` only when MCP default context is configured.
- Treat FAAB output as a deterministic range hint, not a final bid, and do not invent bids for free agents or unknown market state.
- Explain recommendations from `projected_gain`, add/drop trend counts, rostered percentage when present, injury status, and league scoring.
- Use `player_card` for chart-ready evidence when a recommendation needs weekly trajectory.

## Trade Opportunities

For league-wide trade scans, call:

```text
trade_opportunities
```

Rules:

- Report every opposing team, even when the tool finds no attractive offer angle.
- Summarize needs, surplus, top targets, offer angles, trade score, opponent fit, backup risk, bye risk, roster balance after the move, and reasoning.
- Treat output as a projection-based screen, not a definitive trade-value model.

## Smoke Tables

For pre-merge or reviewer validation of the live lineup, waiver, and trade workflow, call:

```text
decision_smoke_report
```

Recommended inputs:

```json
{
  "format": "markdown",
  "per_position_limit": 3,
  "targets_per_team": 2,
  "offers_per_team": 2
}
```

Use `format: "json"` only when the caller needs machine-readable shape validation instead of tables.

## Output Discipline

- Return compact JSON summaries by default.
- Do not dump full player maps.
- Keep explanations tied to the requested week range and positions.
- Do cleanup after containerized workflow checks with `make teardown`. After one-off commands that already use Compose `run --rm`, use `make local-down` when only the temporary Compose network needs cleanup.
