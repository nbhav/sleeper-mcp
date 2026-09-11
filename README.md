# Sleeper Fantasy Football Tooling

A Dockerized Python toolkit for using Sleeper fantasy football data with scripts, MCP-capable LLM assistants, and a remote Cloudflare Worker.

## Why This Exists

Sleeper already has a useful API. This project is not trying to mirror every endpoint. It exists to turn Sleeper data into decision-ready fantasy football context:

- league-aware player scoring
- cached API reads to reduce repeated calls and rate-limit risk
- weekly leaders and historical backtests
- waiver-wire shortlists that exclude rostered players
- current lineup and start/sit recommendations for a configured roster
- injury, opponent, free-agent, and league activity context
- MCP tools that keep LLM usage focused instead of registering dozens of raw API calls

The project is Docker-first. Do not install Python or Node packages on the host.

Sleeper API docs: https://docs.sleeper.com/

## Requirements

- Docker
- Docker Compose
- `make`, optional but recommended

## Quick Start

From this repo:

```bash
make local-up
make test
make decision-smoke
make sleeper ARGS="state"
```

The `state` command returns Sleeper's current NFL season and week. API responses are cached under `./data/`; see `docs/tool-breakdown.md` for the cache data model, normalized decision-data workflow, query lifecycle, and TTLs.

Configure your default league and roster context from a Sleeper league URL plus
your Sleeper username, display name, owner ID, roster ID, or team name:

```bash
make sleeper ARGS='configure-context https://sleeper.com/leagues/<league_id>/matchup --user-ref your_username'
```

This writes `./data/sleeper-mcp.env`, which Docker Compose loads for the CLI and
local MCP server.

## Common Usage

Resolve your Sleeper user and leagues:

```bash
make sleeper ARGS="user your_username"
make sleeper ARGS="leagues <user_id> --season 2026"
```

Inspect league data:

```bash
make sleeper ARGS="league <league_id>"
make sleeper ARGS="rosters <league_id>"
make sleeper ARGS="matchups <league_id> 1 --enrich --full --output json"
```

Pull player performance:

```bash
make sleeper ARGS="best-week --season 2025 --week 1 --source stats --limit 5 --output table"
make sleeper ARGS="best-by-team --season 2026 --week 1 --source projections --position RB --output table"
```

Normalized decision-data sync commands are planned for trendable reads. Until
those commands land on the active branch, use the existing `stats`,
`projections`, `best-week`, and MCP decision tools as the production surface.
The planned workflow is:

```bash
make sleeper ARGS="sync-status --output json"
make sleeper ARGS="sync-data --season 2026 --output json"
```

When `--season` is omitted, scripts use the current calendar year. When `--week`
is omitted, scripts use Sleeper's current NFL week.

Use league scoring settings:

```bash
make sleeper ARGS="best-week --league-id <league_id> --season 2026 --week 1 --source projections --limit 5 --output table"
make sleeper ARGS="weekly-briefing --league-id <league_id> --source projections --output json"
```

Find waiver and injury context:

```bash
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --limit 25 --output table"
make sleeper ARGS="injury-watch <league_id> --output table"
```

## MCP Setup

Run the local stdio MCP server through Docker:

```bash
make mcp
```

If you ran `configure-context`, MCP tools that need league context can use those
defaults without passing `league_id` or `roster_id` every time.

Register it with your LLM harness using the Compose file under `infra/docker/`:

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
      "env": {}
    }
  }
}
```

The same config is saved at `ai/mcp.config.example.json`. For local runs, use
`configure-context` or pass `league_id` and `roster_id` directly to individual
MCP tools.

For assistant usage, prefer:

- `resolve_league_context` for setup-time league, owner, and roster ID resolution
- `weekly_performance_backtest` for historical leaders and week-over-week movement
- `waiver_wire_watch` for actionable waiver recommendations
- `my_lineup` for current starters, bench, actual points, status, and league-scored projections
- `lineup_recommendations` for start/sit and add/drop comparisons
- `waiver_wire_by_position` for top waiver/free-agent options grouped by position
- `trade_opportunities` for league-wide trade partner scans
- `decision_smoke_report` for display-ready lineup, waiver, and trade smoke tables before review or merge
- `weekly_briefing`, `opponent_watch`, `league_team_watch`, and `player_card` for supporting context

## Remote MCP

The Cloudflare Worker under `infra/cloudflare-worker/` exposes the curated MCP tool surface over HTTP and uses Cloudflare D1 for response caching.

Use `resolve_league_context` over MCP to get the `cloudflare_vars` values, then
set those vars in Cloudflare before deploy. The Worker cannot persist runtime
environment changes from a tool call.

Run Worker tasks through Docker:

```bash
make local-up
make worker-typecheck
make worker-dev
make worker-deploy
```

For GitHub Actions deploys, set these repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

Set these GitHub `production` environment variables so deploys can resolve the
Worker's default league context:

- `SLEEPER_DEFAULT_LEAGUE_URL`
- `SLEEPER_DEFAULT_USER_REF`

## Project Layout

```text
.
├── ai/                     # Agent handoff docs, MCP config, Codex skill
├── docs/                   # Human-readable tool, MCP, and development docs
├── infra/
│   ├── cloudflare-worker/  # Remote HTTP MCP adapter
│   └── docker/             # Dockerfile and Compose file
├── python/                 # Python package, CLI, MCP server, tests
├── Makefile
├── README.md
├── AGENTS.md
└── CLAUDE.md
```

## More Docs

- [Docs Index](docs/README.md)
- [Tool Breakdown](docs/tool-breakdown.md)
- [MCP And Agent Usage](docs/mcp-and-agents.md)
- [Development And Deployment](docs/development.md)

## Testing

```bash
make test
make integration-test
make worker-typecheck
make teardown
```

`main` is protected by GitHub-required checks for Python unit tests, live Sleeper integration smoke tests, and Cloudflare Worker typechecking.
