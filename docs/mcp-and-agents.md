# MCP And Agent Usage

Use MCP when Claude, Codex, or another LLM harness should query Sleeper data directly.

## Local Stdio MCP

Run the server:

```bash
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

Tools that need league context use explicit arguments first, then environment defaults:

```text
SLEEPER_DEFAULT_LEAGUE_ID
SLEEPER_DEFAULT_ROSTER_ID
```

`opponent_watch` uses `SLEEPER_DEFAULT_ROSTER_ID` when `roster_id` is omitted. If required context is missing, the MCP server returns a clear protocol error.

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

Use lower-level tools only when the user needs narrower context:

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
