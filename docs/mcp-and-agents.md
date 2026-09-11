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
- for acquisition action, drop reasoning, and FAAB tier only when the player is known to require a waiver claim

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
- for multiple mutual-fit offer angles, trade score, backup risk, bye risk, and reasoning
- for projection-based upgrade targets by opposing roster

Use lower-level tools only when the user needs narrower context:

- `resolve_league_context`: setup-time league, owner, and roster ID resolution
- `my_lineup`: current starters, bench, slots, actual points, status, and projections
- `lineup_recommendations`: deterministic start/sit and add/drop comparisons
- `waiver_wire_by_position`: top waiver/free-agent options grouped by position with protected drop, acquisition, and market-aware FAAB context
- `trade_opportunities`: all opposing teams with needs, surplus, targets, mutual-fit offer scores, roster balance, and reasoning
- `decision_smoke_report`: display-ready Markdown tables for lineup, waiver, and trade smoke validation
- `waiver_watch`: trending unrostered players with projected value
- `free_agent_watch`: unrostered players ranked by projection
- `injury_watch`: rostered players with injury or status risk
- `opponent_watch`: weekly opponent starters and risks
- `league_team_watch`: completed adds, drops, trades, and other league movement
- `player_card`: player metadata and chart-ready weekly actual/projection rows

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
