# Sleeper Tooling Agent Handoff

Use this document as portable context for Claude, Codex, or another coding agent in a different repository.

This describes how to use the `sleeper-mcp` Sleeper tooling project as an external Dockerized data source for fantasy football stats.

## Project Summary

`sleeper-mcp` is a Python CLI and client wrapper around the public Sleeper fantasy football API.

Primary use cases:

- Resolve Sleeper users.
- Find leagues for a user and season.
- Pull league metadata, rosters, users, transactions, and weekly matchups.
- Pull and cache the full NFL player map from `https://api.sleeper.app/v1/players/nfl`.
- Cache API responses in SQLite to reduce repeated calls and rate-limit risk.
- Pull weekly stats and projections.
- Chain common calls into higher-level reports, including weekly leaders, best player by team, and weekly briefing output.
- Prefer decision commands like `waiver-watch` and `injury-watch` over raw endpoint replication.
- Apply custom Sleeper league scoring settings with `--league-id`.
- Export data as JSON, CSV, or terminal tables.
- Expose curated decision tools through a stdio MCP server.
- Support default MCP league and roster context through environment variables.
- Provide a Cloudflare Worker HTTP MCP adapter for remote usage behind Cloudflare Access.
- Use the Python `SleeperClient` in scripts.

Sleeper API docs: https://docs.sleeper.com/

## Hard Constraints

- Do not install Python or Node packages on the host.
- Do not create or depend on a host virtualenv.
- Run all commands through Docker or Docker Compose.
- Treat live Sleeper calls as integration checks.
- Keep unit tests mocked.
- Use the SQLite API response cache by default.
- Use Cloudflare D1 for the Worker cache.
- Cache the large player map in `./data`, which is gitignored.

## Where The Tooling Lives

Expected source directory:

```text
sleeper-mcp/
```

Important files:

```text
sleeper-mcp/
├── Dockerfile
├── Makefile
├── cloudflare-worker/
│   ├── .dev.vars.example
│   ├── README.md
│   ├── package-lock.json
│   ├── package.json
│   ├── tsconfig.json
│   ├── wrangler.toml
│   └── src/
│       └── index.ts
├── docker-compose.yml
├── pyproject.toml
├── src/sleeper_tooling/
│   ├── cli.py
│   ├── client.py
│   ├── db.py
│   ├── decision_reports.py
│   ├── mcp_server.py
│   ├── mcp_tools.py
│   ├── output.py
│   ├── reports.py
│   └── scoring.py
└── tests/
```

## Setup In Another Repo

Preferred options:

1. Add this project as a sibling directory:

```text
parent/
├── your-other-repo/
└── sleeper-mcp/
```

2. Add this project as a submodule or vendored folder inside the repo that needs it:

```text
your-other-repo/
└── tools/
    └── sleeper-mcp/
```

Use the correct path when running commands. Examples below assume:

```text
./tools/sleeper-mcp
```

## Build And Test

```bash
cd ./tools/sleeper-mcp
make build
make test
make integration-test
make worker-typecheck
```

Equivalent without `make`:

```bash
cd ./tools/sleeper-mcp
docker compose build sleeper
docker compose run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m "not integration"
docker compose run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m integration
docker compose run --rm cloudflare-worker run typecheck
```

## Calling The CLI From Another Repo

From the repo root, call through Docker Compose with an explicit compose file:

```bash
docker compose -f ./tools/sleeper-mcp/docker-compose.yml run --rm sleeper state
```

Because the Compose project directory can matter for relative paths and volume mounts, the most reliable pattern is:

```bash
cd ./tools/sleeper-mcp
make sleeper ARGS="state"
```

If writing automation from another repo, use a small wrapper script that changes directory first:

```bash
#!/usr/bin/env bash
set -euo pipefail

TOOL_DIR="${TOOL_DIR:-./tools/sleeper-mcp}"
cd "$TOOL_DIR"
docker compose run --rm sleeper "$@"
```

Example usage:

```bash
./scripts/sleeper state
./scripts/sleeper user your_username
./scripts/sleeper leagues <user_id> --season 2026 --output json
```

## MCP Integration

Prefer the MCP server when an LLM harness needs tool calls.

Example config:

```json
{
  "mcpServers": {
    "sleeper-fantasy": {
      "command": "docker",
      "args": [
        "compose",
        "-f",
        "/absolute/path/to/sleeper-mcp/docker-compose.yml",
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

Tools that need league context use explicit arguments first, then `SLEEPER_DEFAULT_LEAGUE_ID`. `opponent_watch` also uses `SLEEPER_DEFAULT_ROSTER_ID` when `roster_id` is omitted.

Registered tools:

```text
weekly_briefing
waiver_watch
free_agent_watch
injury_watch
opponent_watch
league_team_watch
player_card
```

Keep this tool list curated to avoid token creep.

## Remote MCP On Cloudflare

The `cloudflare-worker/` package exposes the same curated MCP tool names over HTTP at:

```text
https://sleeper-mcp.neilbhavsar.com/mcp
```

Run all Worker commands through Docker Compose:

```bash
make worker-install
make worker-typecheck
make worker-dev
make worker-deploy
```

The Worker expects Cloudflare Access to protect the hostname and D1 to cache Sleeper API responses. Set `SLEEPER_DEFAULT_LEAGUE_ID` and `SLEEPER_DEFAULT_ROSTER_ID` as Worker vars before deployment.
GitHub deploys use the `Deploy Cloudflare Worker` workflow and require `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` repository secrets.

## Common Commands

Current NFL state:

```bash
make sleeper ARGS="state"
```

Resolve a user:

```bash
make sleeper ARGS="user <username-or-user-id>"
```

List leagues:

```bash
make sleeper ARGS="leagues <user_id> --season 2026 --output json"
```

League metadata:

```bash
make sleeper ARGS="league <league_id> --output json"
```

Rosters:

```bash
make sleeper ARGS="rosters <league_id> --output json"
```

Weekly matchups:

```bash
make sleeper ARGS="matchups <league_id> 1 --output json"
```

Enriched matchup summary:

```bash
make sleeper ARGS="matchups <league_id> 1 --enrich --output table"
```

Full enriched matchup report:

```bash
make sleeper ARGS="matchups <league_id> 1 --enrich --full --output json"
```

Players endpoint:

```bash
make sleeper ARGS="players --position QB --active --limit 20 --output table"
```

Weekly stats:

```bash
make sleeper ARGS="stats --season 2025 --week 1 --position QB --order-by pts_ppr --limit 20 --output json"
```

Weekly projections:

```bash
make sleeper ARGS="projections --season 2026 --week 1 --position RB --order-by pts_ppr --limit 20 --output json"
```

Trending adds:

```bash
make sleeper ARGS="trending add --lookback-hours 24 --limit 25 --output json"
```

Enriched trending adds:

```bash
make sleeper ARGS="trending add --lookback-hours 24 --limit 25 --enrich --output json"
```

Top players by position:

```bash
make sleeper ARGS="best-week --season 2026 --week 1 --source projections --positions QB,RB,WR,TE --limit 5 --output json"
```

Top players by league scoring:

```bash
make sleeper ARGS="best-week --league-id <league_id> --season 2026 --week 1 --source projections --limit 5 --output json"
```

Top player per NFL team:

```bash
make sleeper ARGS="best-by-team --season 2026 --week 1 --source projections --position RB --output json"
```

One-call weekly briefing:

```bash
make sleeper ARGS="weekly-briefing --source projections --output json"
```

Available waiver targets:

```bash
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --limit 25 --output json"
```

Rostered injury risk:

```bash
make sleeper ARGS="injury-watch <league_id> --output json"
```

By default, `best-week` and `weekly-briefing` include `QB,RB,WR,TE,K,DEF`.

## League Scoring

Use `--league-id` when rankings should reflect a league's custom scoring settings:

```bash
make sleeper ARGS="stats --league-id <league_id> --season 2025 --week 1 --position RB --output json"
make sleeper ARGS="projections --league-id <league_id> --season 2026 --week 1 --position TE --output json"
make sleeper ARGS="weekly-briefing --league-id <league_id> --source projections --output json"
```

When `--league-id` is present, the tooling fetches the league object, reads `scoring_settings`, and calculates points by multiplying matching stat keys by league scoring multipliers.

League-scored rows include `points`, `sleeper_points`, `scoring_rules_matched`, and `scoring_breakdown`.

## Output Formats

Most commands accept:

```text
--output json
--output csv
--output table
```

For automation, prefer `--output json`.

For spreadsheet exports, use `--output csv`.

For quick manual inspection, use `--output table`.

## Data Caching

The CLI uses SQLite for response caching:

```text
sleeper-mcp/data/sleeper.db
```

Global cache controls:

```bash
make sleeper ARGS="cache-info"
make sleeper ARGS="cache-clear"
make sleeper ARGS="cache-clear --expired-only"
make sleeper ARGS="--refresh-cache state"
make sleeper ARGS="--no-cache state"
make sleeper ARGS="--cache-db /data/custom.db state"
```

Default TTLs:

```text
state/trending: 5 minutes
stats/projections/matchups/transactions: 15 minutes
league/user/roster/draft metadata: 1 hour
players: 6 hours
```

The full Sleeper players response is large. The default cache path inside the container is:

```text
/data/players.json
```

This maps to:

```text
sleeper-mcp/data/players.json
```

That directory is ignored by git.

Delete this file to force a refresh:

```bash
rm -f ./tools/sleeper-mcp/data/players.json
```

## Python Client Usage

Use this only inside the Docker container unless the consuming repo has explicitly installed the package.

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
```

Run a snippet inside Docker:

```bash
cd ./tools/sleeper-mcp
docker compose run --rm --entrypoint /app/.venv/bin/python sleeper - <<'PY'
from sleeper_tooling import SleeperClient

with SleeperClient() as sleeper:
    print(sleeper.get_nfl_state())
PY
```

## Programmatic Integration Pattern

For another repo, prefer shelling out to the CLI and reading JSON. This keeps dependency isolation clean.

Example Python caller in another repo:

```python
import json
import subprocess
from pathlib import Path

TOOL_DIR = Path("tools/sleeper-mcp")


def sleeper_json(*args: str):
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "sleeper", *args, "--output", "json"],
        cwd=TOOL_DIR,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


state = sleeper_json("state")
print(state)
```

For commands where `--output json` is already the default, passing it is still fine.

## API Details

Base endpoints used by the tooling:

```text
https://api.sleeper.app/v1
https://api.sleeper.com
```

Known client methods:

```python
get_user(username_or_id)
get_user_leagues(user_id, season)
get_user_drafts(user_id, season)
get_league(league_id)
get_league_users(league_id)
get_rosters(league_id)
get_matchups(league_id, week)
get_transactions(league_id, week)
get_traded_picks(league_id)
get_draft(draft_id)
get_draft_picks(draft_id)
get_draft_traded_picks(draft_id)
get_nfl_state()
get_players(position=None, active=None)
get_trending_players("add" | "drop")
get_stats(season, week=None, position=None, order_by=None)
get_projections(season, week=None, position=None, order_by=None)
```

Chained CLI-only reports:

```text
best-week
best-by-team
weekly-briefing
waiver-watch
injury-watch
trending --enrich
```

## Extension Instructions For Agents

When modifying this tooling:

- Keep the Docker-only workflow intact.
- Keep SQLite response caching on by default for CLI commands.
- Keep Worker caching on D1; do not try to use the local SQLite cache inside Cloudflare Workers.
- Do not add instructions that require local `pip install`, local `uv sync`, or a host virtualenv.
- Add new API methods in `src/sleeper_tooling/client.py`.
- Add CLI commands in `src/sleeper_tooling/cli.py`.
- Add fantasy decision logic in `src/sleeper_tooling/decision_reports.py`.
- Keep MCP registration in `src/sleeper_tooling/mcp_server.py`.
- Keep MCP tool orchestration in `src/sleeper_tooling/mcp_tools.py`.
- Keep the remote HTTP MCP adapter in `cloudflare-worker/src/index.ts`.
- Add data shaping helpers in `src/sleeper_tooling/reports.py` when raw Sleeper responses are awkward for downstream use.
- Keep custom scoring logic in `src/sleeper_tooling/scoring.py`.
- Keep SQLite cache logic in `src/sleeper_tooling/db.py`.
- Keep output handling in `src/sleeper_tooling/output.py`.
- Add mocked tests under `tests/`.
- Add live API smoke checks under `tests/integration/` and mark them with `pytest.mark.integration`.
- Verify with `make test` from inside `sleeper-mcp`.
- Verify with `make integration-test` when API behavior or live response assumptions changed.

## Notes For Claude Or Codex

When using this handoff in another repo:

- Prefer JSON output and parse it.
- Avoid live API calls in unit tests.
- Use live API calls only for smoke or integration checks.
- The Sleeper API is public and read-only for these endpoints.
- Some stat keys may be omitted when their value is zero, so downstream scoring code must default missing stat values to `0`.
- The current NFL season/week should be discovered with `state` instead of hard-coded.
