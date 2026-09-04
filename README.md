# Sleeper Fantasy Football Tooling

A Dockerized Python toolkit for using Sleeper fantasy football data with scripts, MCP-capable LLM assistants, and a remote Cloudflare Worker.

## Why This Exists

Sleeper already has a useful API. This project is not trying to mirror every endpoint. It exists to turn Sleeper data into decision-ready fantasy football context:

- league-aware player scoring
- cached API reads to reduce repeated calls and rate-limit risk
- weekly leaders and historical backtests
- waiver-wire shortlists that exclude rostered players
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
make build
make test
make sleeper ARGS="state"
```

The `state` command returns Sleeper's current NFL season and week. API responses are cached under `./data/`.

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
      "env": {
        "SLEEPER_DEFAULT_LEAGUE_ID": "<league_id>",
        "SLEEPER_DEFAULT_ROSTER_ID": "<roster_id>"
      }
    }
  }
}
```

The same config is saved at `ai/mcp.config.example.json`.

For assistant usage, prefer:

- `weekly_performance_backtest` for historical leaders and week-over-week movement
- `waiver_wire_watch` for actionable waiver recommendations
- `weekly_briefing`, `opponent_watch`, `league_team_watch`, and `player_card` for supporting context

## Remote MCP

The Cloudflare Worker under `infra/cloudflare-worker/` exposes the curated MCP tool surface over HTTP and uses Cloudflare D1 for response caching.

Run Worker tasks through Docker:

```bash
make worker-install
make worker-typecheck
make worker-dev
make worker-deploy
```

For GitHub Actions deploys, set these repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

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
```

`main` is protected by GitHub-required checks for Python unit tests, live Sleeper integration smoke tests, and Cloudflare Worker typechecking.
