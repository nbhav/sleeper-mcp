# Sleeper Fantasy Football Tooling

A Dockerized Python toolkit for pulling fantasy football data from the Sleeper API.

This project is meant for local scripts, league analysis, weekly reporting, and quick command-line pulls without installing Python packages on your machine. The host only needs Docker.

## What It Does

- Resolves Sleeper users by username or user ID.
- Lists a user's fantasy football leagues for a season.
- Pulls league metadata, rosters, users, transactions, and weekly matchups.
- Pulls the full NFL player map from `https://api.sleeper.app/v1/players/nfl`.
- Caches API responses in SQLite under `./data` to avoid repeated Sleeper calls.
- Caches player metadata under `./data` so enriched matchup reports do not repeatedly download the large player map.
- Pulls trending adds and drops.
- Pulls player stats and projections.
- Chains common calls into higher-level reports, like weekly leaders and top players by NFL team.
- Answers decision-focused questions with `waiver-watch`, `injury-watch`, and MCP-first weekly scout tools.
- Exposes MCP-only assistant tools for historical backtests, actionable waiver shortlists, free-agent watch, opponent watch, league team movement, and player cards.
- Supports default MCP league and roster context through environment variables.
- Applies custom league scoring settings with `--league-id`.
- Includes a Cloudflare Worker HTTP MCP adapter for running behind Cloudflare Access on a custom domain.
- Outputs data as JSON, CSV, or terminal tables.
- Provides a small Python client for programmatic use.

Sleeper API docs: https://docs.sleeper.com/

## Requirements

- Docker
- Docker Compose
- `make`, optional but recommended

No local Python install, virtualenv, or package installation is required.

## Quick Start

From this directory:

```bash
make build
make test
make sleeper ARGS="state"
```

The `state` command should return the current NFL season and week according to Sleeper. By default, API responses are cached in `./data/sleeper.db`.

`make test` runs the offline unit suite. Use `make integration-test` when you want to run the live Sleeper API checks.

## Common Workflows

Resolve your Sleeper user:

```bash
make sleeper ARGS="user your_username"
```

Use the returned `user_id` to list leagues:

```bash
make sleeper ARGS="leagues <user_id> --season 2026"
```

Inspect one league:

```bash
make sleeper ARGS="league <league_id>"
make sleeper ARGS="rosters <league_id>"
```

Pull weekly matchups:

```bash
make sleeper ARGS="matchups <league_id> 1"
```

Pull enriched weekly standings-style output:

```bash
make sleeper ARGS="matchups <league_id> 1 --enrich --output table"
```

Pull a full enriched matchup report, including starters and bench:

```bash
make sleeper ARGS="matchups <league_id> 1 --enrich --full --output json"
```

Pull active QBs from the player endpoint:

```bash
make sleeper ARGS="players --position QB --active --limit 20 --output table"
```

Pull weekly QB stats:

```bash
make sleeper ARGS="stats --season 2025 --week 1 --position QB --order-by pts_ppr --limit 20 --output table"
```

Pull projections:

```bash
make sleeper ARGS="projections --season 2026 --week 1 --position RB --order-by pts_ppr --limit 20 --output table"
```

Pull trending waiver adds:

```bash
make sleeper ARGS="trending add --lookback-hours 24 --limit 25 --output table"
```

Pull enriched trending waiver adds:

```bash
make sleeper ARGS="trending add --lookback-hours 24 --limit 25 --enrich --output table"
```

Pull top weekly players by position:

```bash
make sleeper ARGS="best-week --season 2026 --week 1 --source projections --positions QB,RB,WR,TE --limit 5 --output table"
```

Pull top weekly players using your league's scoring settings:

```bash
make sleeper ARGS="best-week --league-id <league_id> --season 2026 --week 1 --source projections --limit 5 --output table"
```

Pull the top projected RB for each NFL team:

```bash
make sleeper ARGS="best-by-team --season 2026 --week 1 --source projections --position RB --output table"
```

Pull one JSON report with current state, leaders, and trending adds:

```bash
make sleeper ARGS="weekly-briefing --source projections --output json"
```

Find available waiver targets in your league:

```bash
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --limit 25 --output table"
```

Check rostered players with injury risk:

```bash
make sleeper ARGS="injury-watch <league_id> --output table"
```

By default, `best-week` and `weekly-briefing` include `QB,RB,WR,TE,K,DEF`.

## MCP Server

The project includes a stdio MCP server for Claude, Codex, or another MCP-capable LLM harness.

Run it through Docker:

```bash
make mcp
```

Set default context when you want assistant tools to use your league/team without repeating IDs in every call:

```bash
SLEEPER_DEFAULT_LEAGUE_ID=<league_id> \
SLEEPER_DEFAULT_ROSTER_ID=<roster_id> \
make mcp
```

Example MCP config:

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

The same config is saved in `ai/mcp.config.example.json`.

Registered MCP tools:

| Tool | Purpose |
|---|---|
| `weekly_briefing` | Weekly leaders plus waiver signal. |
| `weekly_performance_backtest` | Back-test weekly leaders and deterministic week-over-week movers. |
| `waiver_watch` | Trending unrostered players with projected value. |
| `waiver_wire_watch` | Actionable waiver shortlist with availability, projections, trends, injury/status, and recent actuals. |
| `free_agent_watch` | Unrostered players ranked by projection. |
| `injury_watch` | Rostered players with injury/status risk. |
| `opponent_watch` | Weekly opponent starters, projection, and injury flags. |
| `league_team_watch` | Completed league transactions for a week. |
| `player_card` | Player metadata and chart-ready actual vs projected points. |

This MCP layer intentionally exposes decision-shaped tools instead of raw Sleeper endpoints. Prefer `weekly_performance_backtest` for historical player performance questions and `waiver_wire_watch` for waiver recommendations that should exclude rostered players.

Tools that need league context use the explicit `league_id` argument first, then `SLEEPER_DEFAULT_LEAGUE_ID`. `opponent_watch` also uses `SLEEPER_DEFAULT_ROSTER_ID` when `roster_id` is omitted. If required context is missing, the MCP server returns a clear protocol error.

## Cloudflare Worker MCP

The `infra/cloudflare-worker/` package is a remote HTTP MCP adapter for `sleeper-mcp.neilbhavsar.com`. It is intended to run behind Cloudflare Access so you can use the assistant tools without your laptop.

The Worker mirrors the curated MCP tool surface and uses Cloudflare D1 for API response caching. It does not use the local SQLite cache because Workers do not provide a persistent local filesystem.

Run Worker commands through Docker:

```bash
make worker-install
make worker-typecheck
make worker-dev
make worker-deploy
```

Before deploying:

1. Create a D1 database named `sleeper-mcp-cache`.
2. Replace `database_id` in `infra/cloudflare-worker/wrangler.toml`.
3. Set `SLEEPER_DEFAULT_LEAGUE_ID` and `SLEEPER_DEFAULT_ROSTER_ID` in Cloudflare Worker vars or `infra/cloudflare-worker/.dev.vars`.
4. Configure Cloudflare Access for `sleeper-mcp.neilbhavsar.com`.
5. Deploy with `make worker-deploy`.

For GitHub Actions deploys, add these repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

The deploy workflow runs only after the `CI` workflow succeeds on `main`, or when triggered manually.

The remote MCP endpoint is:

```text
https://sleeper-mcp.neilbhavsar.com/mcp
```

## League Scoring

Sleeper leagues can assign different point values to receptions, passing touchdowns, rushing yards, kicker stats, defense stats, bonuses, and other actions.

Pass `--league-id` to use that league's `scoring_settings` instead of generic Sleeper PPR fields:

```bash
make sleeper ARGS="stats --league-id <league_id> --season 2025 --week 1 --position RB --limit 20 --output table"
make sleeper ARGS="projections --league-id <league_id> --season 2026 --week 1 --position TE --limit 20 --output table"
make sleeper ARGS="best-week --league-id <league_id> --season 2026 --week 1 --source projections --limit 5 --output table"
make sleeper ARGS="best-by-team --league-id <league_id> --season 2026 --week 1 --source projections --position RB --output table"
make sleeper ARGS="weekly-briefing --league-id <league_id> --source projections --output json"
```

League-scored rows include:

- `points`: calculated from the league's scoring settings.
- `sleeper_points`: Sleeper's default points field when present.
- `scoring_rules_matched`: count of scoring keys that contributed non-zero points.
- `scoring_breakdown`: JSON-only contribution details by stat key.

## Output Formats

Most commands support:

```bash
--output json
--output csv
--output table
```

Use JSON when piping into scripts, CSV when exporting to a spreadsheet, and table output for quick terminal inspection.

Example:

```bash
make sleeper ARGS="stats --season 2025 --week 1 --position WR --limit 50 --output csv"
```

## Python Usage

The package exposes a small synchronous client:

```python
from sleeper_tooling import SleeperClient

with SleeperClient() as sleeper:
    user = sleeper.get_user("your_username")
    leagues = sleeper.get_user_leagues(user["user_id"], 2026)
    stats = sleeper.get_stats(
        2025,
        week=1,
        position="QB",
        order_by="pts_ppr",
    )

print(leagues)
print(stats[:5])
```

Run Python snippets inside Docker:

```bash
docker compose -f infra/docker/docker-compose.yml run --rm --entrypoint /app/.venv/bin/python sleeper - <<'PY'
from sleeper_tooling import SleeperClient

with SleeperClient() as sleeper:
    print(sleeper.get_nfl_state())
PY
```

## Make Targets

| Target | Action |
|---|---|
| `make build` | Build the Docker image. |
| `make test` | Run offline unit tests inside Docker. |
| `make integration-test` | Run live Sleeper API integration tests inside Docker. |
| `make sleeper ARGS="..."` | Run the Sleeper CLI inside Docker. |
| `make mcp` | Run the stdio MCP server inside Docker. |
| `make worker-install` | Install Worker dependencies inside Docker. |
| `make worker-typecheck` | Typecheck the Cloudflare Worker inside Docker. |
| `make worker-dev` | Run Wrangler dev inside Docker. |
| `make worker-deploy` | Deploy the Cloudflare Worker through Docker. |
| `make shell` | Open a shell inside the app container. |
| `make clean` | Stop containers and remove generated Python cache files. |

## CLI Commands

| Command | Purpose |
|---|---|
| `state` | Fetch current NFL state from Sleeper. |
| `user` | Resolve a Sleeper username or user ID. |
| `leagues` | List a user's NFL leagues for a season. |
| `league` | Fetch league settings and metadata. |
| `rosters` | Fetch all rosters in a league. |
| `matchups` | Fetch weekly matchups, optionally enriched with team and player names. |
| `players` | Fetch or cache the NFL player map. |
| `trending` | Fetch players trending by adds or drops. |
| `stats` | Fetch season or weekly player stats. |
| `projections` | Fetch season or weekly player projections. |
| `best-week` | Chain state plus stat/projection calls into leaders by position. |
| `best-by-team` | Chain state plus stat/projection calls into one leader per NFL team. |
| `weekly-briefing` | Chain state, leaders, and enriched trending adds into one JSON report. |
| `waiver-watch` | Find trending available players with projected value. |
| `injury-watch` | Show injury-relevant players currently rostered in a league. |
| `cache-info` | Show SQLite API response cache stats. |
| `cache-clear` | Clear SQLite API response cache rows. |

To see command-specific options:

```bash
make sleeper ARGS="matchups --help"
make sleeper ARGS="stats --help"
```

## Project Structure

```text
.
├── AGENTS.md
├── CLAUDE.md
├── Makefile
├── ai/
│   ├── AGENTS.md
│   ├── AI_AGENT_HANDOFF.md
│   ├── CLAUDE.md
│   ├── mcp.config.example.json
│   └── codex/
│       └── skills/
├── infra/
│   ├── cloudflare-worker/
│   │   ├── README.md
│   │   ├── package-lock.json
│   │   ├── package.json
│   │   ├── src/
│   │   │   └── index.ts
│   │   ├── tsconfig.json
│   │   └── wrangler.toml
│   └── docker/
│       ├── Dockerfile
│       └── docker-compose.yml
└── python/
    ├── pyproject.toml
    ├── src/
    │   └── sleeper_tooling/
    └── tests/
        └── integration/
```

Key files:

- `python/src/sleeper_tooling/client.py`: Thin Sleeper API wrapper.
- `python/src/sleeper_tooling/cli.py`: Typer command-line interface.
- `python/src/sleeper_tooling/db.py`: SQLite response cache.
- `python/src/sleeper_tooling/decision_reports.py`: Fantasy decision reports built from multiple Sleeper calls.
- `python/src/sleeper_tooling/mcp_server.py`: Stdio MCP protocol server.
- `python/src/sleeper_tooling/mcp_tools.py`: MCP tool implementations over the decision engine.
- `python/src/sleeper_tooling/reports.py`: Helpers that join raw API objects into fantasy-friendly report rows.
- `python/src/sleeper_tooling/scoring.py`: League-specific fantasy point calculation.
- `python/src/sleeper_tooling/output.py`: JSON, CSV, and rich table rendering.
- `python/tests/`: Offline tests that use mocked HTTP responses.
- `python/tests/integration/`: Live Sleeper API smoke tests marked with `pytest.mark.integration`.
- `infra/docker/docker-compose.yml`: Docker Compose services for local CLI, stdio MCP, tests, and Worker tasks.
- `infra/docker/Dockerfile`: Python app image build.
- `infra/cloudflare-worker/src/index.ts`: Remote HTTP MCP adapter for Cloudflare Workers.
- `infra/cloudflare-worker/wrangler.toml`: Worker route, vars, and D1 binding config.
- `ai/`: Shareable agent instructions, handoff context, and Codex skill files.
- `.github/workflows/`: GitHub-required CI/deploy workflow location.

## Caching

The CLI uses a SQLite response cache by default:

```text
./data/sleeper.db
```

The cache is keyed by full request URL, including query params. Repeated calls to the same Sleeper endpoint reuse stored JSON until the endpoint TTL expires.

Default TTLs:

| Endpoint Type | TTL |
|---|---:|
| NFL state | 5 minutes |
| Trending players | 5 minutes |
| Stats, projections, matchups, transactions | 15 minutes |
| League, roster, user, draft metadata | 1 hour |
| Player map | 6 hours |

Inspect cache state:

```bash
make sleeper ARGS="cache-info"
```

Clear all cached API responses:

```bash
make sleeper ARGS="cache-clear"
```

Clear only expired rows:

```bash
make sleeper ARGS="cache-clear --expired-only"
```

Force a live refresh while still updating the cache:

```bash
make sleeper ARGS="--refresh-cache best-week --source projections --output table"
```

Bypass the SQLite cache completely:

```bash
make sleeper ARGS="--no-cache state"
```

Use a custom SQLite cache path:

```bash
make sleeper ARGS="--cache-db /data/my-sleeper-cache.db state"
```

The Sleeper player endpoint returns a large ID-keyed map. By default, commands that need player names cache this file at:

```text
./data/players.json
```

The `data` directory is mounted into the container and ignored by git.

Refresh the cache by deleting the file:

```bash
rm -f data/players.json
```

Or use a one-off cache path:

```bash
make sleeper ARGS="players --position QB --active --cache-path /data/qbs.json"
```

## API Notes

- Sleeper's public fantasy API does not require authentication for these reads.
- `https://api.sleeper.app/v1` is used for documented user, league, roster, matchup, player, draft, and trending endpoints.
- `https://api.sleeper.com` is used for stats and projections.
- Sleeper often omits zero-value stat fields, so scoring code should default missing fields to `0`.
- League scoring is calculated by multiplying stat keys by the matching keys in `league.scoring_settings`.
- CLI API responses are cached in SQLite by default; use `--refresh-cache` for live data and `--no-cache` to bypass storage.
- Treat live API pulls as integration checks. The unit tests use mocked HTTP responses.

## Development

Run offline unit tests:

```bash
make test
```

Run live Sleeper API integration tests:

```bash
make integration-test
```

GitHub Actions exposes separate protected checks for Python unit tests, live Sleeper API integration tests, and Cloudflare Worker typechecking. Each check runs through Docker Compose.

`main` is protected on GitHub with required CI checks, admin enforcement enabled, and force-pushes/deletions disabled. This is a personal public repository, so GitHub does not support user/team push restrictions through branch protection; repo access is limited by collaborators instead. At the time this was configured, `nbhav` was the only collaborator with write access.

Open a container shell:

```bash
make shell
```

Run the CLI directly through Compose:

```bash
docker compose -f infra/docker/docker-compose.yml run --rm sleeper --help
```

When adding a new API method:

1. Add the method to `python/src/sleeper_tooling/client.py`.
2. Add a CLI command in `python/src/sleeper_tooling/cli.py` if it should be callable from the terminal.
3. Add decision reports in `python/src/sleeper_tooling/decision_reports.py` when the command answers a fantasy management question.
4. Add report shaping in `python/src/sleeper_tooling/reports.py` if raw API output is awkward to consume.
5. Add mocked tests under `python/tests/`.
6. Add live checks under `python/tests/integration/` for new Sleeper API assumptions.
7. Run `make test`; run `make integration-test` when API behavior changed.
