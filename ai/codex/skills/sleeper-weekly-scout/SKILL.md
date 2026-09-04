---
name: sleeper-weekly-scout
description: Back-test Sleeper weekly leaders, compare week-over-week changes, and filter cleaner waiver-wire targets from this repo's deterministic MCP tools and Dockerized CLI fallbacks.
---

# Sleeper Weekly Scout

Use this skill when a user wants historical top players, week-over-week movement, or waiver-wire shortlists without raw Sleeper endpoint noise.

## Core Workflow

1. Prefer deterministic MCP tools when the MCP server is registered.
2. Use JSON output unless the user explicitly wants a table.
3. Fall back to the repo CLI through Docker Compose only when MCP is unavailable.

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

Recommended inputs:

```json
{
  "league_id": "<league_id>",
  "season": 2026,
  "week": 1,
  "positions": "RB,WR,TE",
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

- Prefer `RB,WR,TE` for standard waiver work.
- Expand to `QB,K,DEF` only when the user asks.
- Use available-player output, not raw trending lists, when the goal is actionable waiver suggestions.
- Use `free_agent_watch` when the user wants a cleaner available-player ranking without trend pressure.
- Keep the result focused on projected value and roster availability.

## Output Discipline

- Return compact JSON summaries by default.
- Do not dump full player maps.
- Keep explanations tied to the requested week range and positions.
