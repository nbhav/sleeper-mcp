---
name: sleeper-weekly-scout
description: Back-test Sleeper weekly leaders, compare week-over-week changes, and filter cleaner waiver-wire targets from this repo's Dockerized CLI/MCP commands.
---

# Sleeper Weekly Scout

Use this skill when a user wants historical top players, week-over-week movement, or waiver-wire shortlists without raw Sleeper endpoint noise.

## Core Workflow

1. Prefer the repo CLI through Docker Compose for historical back-tests.
2. Use JSON output unless the user explicitly wants a table.
3. Use MCP tools for assistant-driven waiver work when the MCP server is registered.

## Historical Leaders

For a single week back-test:

```bash
make sleeper ARGS="best-week --season <season> --week <week> --source stats --limit 5 --output json"
```

Rules:

- Use `--source stats` for true back-tests.
- Use `--league-id <league_id>` when the ranking should reflect league scoring.
- Keep the default positions `QB,RB,WR,TE,K,DEF` unless the user narrows scope.

## Week-Over-Week Changes

To compare `x` weeks, run `best-week` for each requested week and compare rows by `player_id`, `position`, and `points`.

Use these deltas:

- `points_delta = current_week_points - previous_week_points`
- `rank_delta = previous_rank - current_rank`

Prefer reporting:

- top risers
- top fallers
- players that appear or disappear across the sampled weeks

If the user asks for a compact trend view, summarize only the top movers per position.

## Waiver Wire

For CLI waiver-wire filtering, use `waiver-watch`:

```bash
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --limit 25 --output json"
```

For MCP waiver-wire filtering, prefer these tools:

```text
waiver_watch
free_agent_watch
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
