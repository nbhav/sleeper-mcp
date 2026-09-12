# MCP And Agent Usage

Use MCP when Claude, Codex, or another LLM harness should query Sleeper data directly.

## Local Stdio MCP

Run the server:

```bash
make local-up
make mcp
```

Example registration:

```json
{
  "mcpServers": {
    "sleeper-fantasy": {
      "command": "docker",
      "args": [
        "compose",
        "-f",
        "/absolute/path/to/sleeper-mcp/infra/docker/docker-compose.yml",
        "run",
        "--rm",
        "-i",
        "sleeper-mcp"
      ],
      "env": {
        "SLEEPER_DEFAULT_LEAGUE_ID": "<league_id>",
        "SLEEPER_DEFAULT_ROSTER_ID": "<roster_id>"
      }
    }
  }
}
```

The same example lives at `ai/mcp.config.example.json`.

## Default Context

Configure local defaults once from a Sleeper league URL plus your Sleeper
username, display name, owner ID, roster ID, or team name:

```bash
make sleeper ARGS='configure-context https://sleeper.com/leagues/<league_id>/matchup --user-ref your_username'
```

The command resolves and writes:

```text
./data/sleeper-mcp.env
```

Tools that need league context use explicit arguments first, then environment defaults:

```text
SLEEPER_DEFAULT_LEAGUE_ID
SLEEPER_DEFAULT_ROSTER_ID
SLEEPER_DEFAULT_OWNER_ID
```

`opponent_watch` uses `SLEEPER_DEFAULT_ROSTER_ID` when `roster_id` is omitted. If required context is missing, the MCP server returns a clear protocol error.

For local Docker Compose runs, those defaults should live in `./data/sleeper-mcp.env`.
For hosted Worker runs, set the same values as Worker vars.
GitHub Actions deploys resolve those Worker vars from GitHub `production`
environment variables named `SLEEPER_DEFAULT_LEAGUE_URL` and
`SLEEPER_DEFAULT_USER_REF`.

Use a dry run when you want to inspect the values without writing the env file:

```bash
make sleeper ARGS='configure-context https://sleeper.com/leagues/<league_id>/matchup --user-ref your_username --no-write --output env'
```

The MCP surface also exposes `resolve_league_context` for hosted or agent-driven
setup. It returns the resolved IDs, `env_text` for local `.env` files, and
`cloudflare_vars` for Worker configuration. It does not persist settings by
itself.

## Recommended Tool Choices

Use `weekly_performance_backtest` when the user asks:

- who performed best in a historical week
- how players changed week over week
- which players rose or fell over a window
- for positional leaders across `QB,RB,WR,TE,K,DEF`

Use `waiver_wire_watch` when the user asks:

- who to target on waivers
- which trending adds are actually available
- which waiver players have projection plus recent actual evidence
- which candidates should be filtered through league scoring

Use `waiver_wire_by_position` when the user asks:

- for top waiver options by position
- to compare each option against a realistic drop candidate
- for recommendation, move score, value deltas, acquisition action, drop reasoning, and FAAB tier only when the player is known to require a waiver claim

Use `my_lineup` when the user asks:

- who is currently starting this week
- who is on the bench
- for a lineup table with starters and bench
- what the current lineup projects for under league scoring
- current total, projected total, and week number

Use `lineup_recommendations` when the user asks:

- who to start or sit
- whether a bench player should replace a starter
- whether a free agent or waiver player is better than the roster's weakest comparable player
- how add/drop momentum and rostered percentage should affect watch priority, urgency, or waiver range

Use `trade_opportunities` when the user asks:

- which teams are good trade partners
- what each opposing team needs or has in surplus
- for multiple mutual-fit offer angles, package type, ask, offer, gain, value balance, trade score, recommendation, backup risk, bye risk, and reasoning summary
- for projection-based upgrade targets by opposing roster

Use `player_values` when the user asks for deterministic player value rankings
across week, three-week, season, decision, or replacement-aware scores.

Use `roster_analysis` or `league_roster_analysis` when the user asks for roster
strengths, weaknesses, protected players, movable players, balance score, or
trade/waiver posture.

Use lower-level tools only when the user needs narrower context:

- `resolve_league_context`: setup-time league, owner, and roster ID resolution
- `decision_data_status`: normalized-data freshness check before trendable decisions
- `sync_decision_data`: normalized sync for stale or missing decision data
- `player_stat_trends`: week-over-week player stat trends from normalized tall stat rows
- `position_stat_leaders`: position leaderboards from normalized tall stat rows
- `my_lineup`: current starters, bench, slots, actual points, status, and projections
- `lineup_recommendations`: deterministic start/sit and add/drop comparisons
- `waiver_wire_by_position`: top waiver/free-agent options grouped by position with recommendation, move score, protected drop, acquisition, and market-aware FAAB context
- `trade_opportunities`: all opposing teams with needs, surplus, targets, mutual-fit package scores, roster balance, and reasoning summary
- `player_values`: deterministic player values with week, three-week, season, decision, and replacement-aware scores
- `roster_analysis`: one-roster needs, surplus, protected players, movable players, and posture
- `league_roster_analysis`: league-wide roster needs, surplus, risk, and posture
- `decision_smoke_report`: display-ready Markdown tables for lineup, waiver move matrix, and trade package validation
- `waiver_watch`: trending unrostered players with projected value
- `free_agent_watch`: unrostered players ranked by projection
- `injury_watch`: rostered players with injury or status risk
- `opponent_watch`: weekly opponent starters and risks
- `league_team_watch`: completed adds, drops, trades, and other league movement
- `player_card`: player metadata and chart-ready weekly actual/projection rows

The normalized tools above are available in the Python stdio MCP runtime. Worker
D1 parity is planned separately.

## Normalized Data Freshness

For trendable reads, agents should check normalized decision-data freshness
before making a recommendation:

```json
{
  "name": "decision_data_status",
  "arguments": {
    "season": 2026
  }
}
```

If the status says data is missing or stale, sync first when the tool exists:

```json
{
  "name": "sync_decision_data",
  "arguments": {
    "season": 2026
  }
}
```

Then use normalized trend reads for week-over-week analysis:

```text
player_stat_trends
position_stat_leaders
```

The planned retention default is two seasons: current season plus one prior
season. The normalized model should automatically coerce numeric Sleeper stat
values and store weekly stats as tall rows keyed by season, week, source,
player, and stat key. If normalized data is unavailable after sync, decision
tools should fall back to live Sleeper reads through the existing HTTP cache and
include fallback/staleness metadata in their output.

## Token Discipline

The project avoids registering one MCP tool per Sleeper endpoint. That reduces tool-list token overhead and makes agent behavior more predictable.

Prefer adding deterministic, decision-shaped tools when:

- the call chains multiple Sleeper requests
- league scoring changes the answer
- raw output needs filtering to be useful
- the result should be chart-ready or LLM-ready

Avoid adding tools that only return unshaped raw endpoint payloads.

## Local Lifecycle

Prepare local Docker workflow dependencies before a session when needed:

```bash
make local-up
```

After containerized workflow checks, stop Compose resources and prune stopped
containers plus dangling images:

```bash
make teardown
```

For quick live validation of lineup, waiver, and trade workflows:

```bash
make decision-smoke
```

For MCP clients that need the same validation in a human-readable table, call:

```json
{
  "name": "decision_smoke_report",
  "arguments": {
    "format": "markdown",
    "per_position_limit": 3,
    "targets_per_team": 2,
    "offers_per_team": 2
  }
}
```

Smoke Markdown tables expose the same deterministic fields that MCP JSON
returns. Waiver rows include `recommendation`, `move_score`,
`reasoning_summary`, and week/three-week/season deltas when available. Trade
rows are package-angle rows with `package_type`, `ask`, `offer`, `my_gain`,
`opponent_gain`, `value_balance`, `trade_score`, `recommendation`, and
`reasoning_summary`.

## Shareable Skill

The Codex skill lives at:

```text
ai/codex/skills/sleeper-weekly-scout/
```

It tells Codex to prefer deterministic MCP tools for historical weekly scouting and waiver-wire workflows, with Dockerized CLI fallbacks when MCP is unavailable.

## Remote MCP On Cloudflare

The Worker package lives at:

```text
infra/cloudflare-worker/
```

It exposes the same curated MCP tools over HTTP at:

```text
https://sleeper-mcp.neilbhavsar.com/mcp
```

Deploy requirements:

1. Create a D1 database named `sleeper-mcp-cache`.
2. Put the D1 `database_id` into `infra/cloudflare-worker/wrangler.toml`.
3. Set Worker vars for default league and roster context if desired.
4. Protect the hostname with Cloudflare Access.
5. Deploy with `make worker-deploy`.
