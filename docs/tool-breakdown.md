# Tool Breakdown

This project has two main runtime surfaces:

- `python/`: the Python package, CLI, stdio MCP server, SQLite cache, and tests
- `infra/cloudflare-worker/`: the remote HTTP MCP adapter for hosted use behind Cloudflare Access

The Docker and Compose files live under `infra/docker/`. The main Makefile at the repo root wraps those Compose commands so normal usage stays short.

## Python Components

| Path | Purpose |
|---|---|
| `python/src/sleeper_tooling/client.py` | Thin Sleeper API wrapper. |
| `python/src/sleeper_tooling/cli.py` | Typer command-line interface. |
| `python/src/sleeper_tooling/db.py` | SQLite response cache. |
| `python/src/sleeper_tooling/decision_reports.py` | Fantasy decision reports built from multiple Sleeper calls. |
| `python/src/sleeper_tooling/league_context.py` | Resolve default league, owner, and roster IDs from a league URL plus team/user name. |
| `python/src/sleeper_tooling/mcp_server.py` | Stdio MCP protocol server. |
| `python/src/sleeper_tooling/mcp_tools.py` | MCP tool implementations over the decision engine. |
| `python/src/sleeper_tooling/reports.py` | Helpers that join raw API objects into fantasy-friendly rows. |
| `python/src/sleeper_tooling/scoring.py` | League-specific fantasy point calculation. |
| `python/src/sleeper_tooling/output.py` | JSON, CSV, and terminal table rendering. |

## Data Flow

Most decision workflows follow this shape:

1. Fetch state, league, roster, player, stats, projection, or transaction data from Sleeper.
2. Read from or write to the cache to avoid repeating the same API calls.
3. Normalize raw Sleeper payloads into compact fantasy rows.
4. Apply league scoring settings when a league ID is provided.
5. Return JSON-first output that an LLM or script can consume directly.

When a command or MCP tool omits `season`, the tooling uses the current calendar
year. When `week` is omitted, it asks Sleeper for the current NFL week.

## CLI Commands

| Command | Purpose |
|---|---|
| `state` | Fetch current NFL state from Sleeper. |
| `user` | Resolve a Sleeper username or user ID. |
| `leagues` | List a user's NFL leagues for a season. |
| `league` | Fetch league settings and metadata. |
| `rosters` | Fetch all rosters in a league. |
| `configure-context` | Resolve league/roster defaults from a league URL and team/user name, then write `./data/sleeper-mcp.env`. |
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

Use command help for details:

```bash
make sleeper ARGS="best-week --help"
make sleeper ARGS="waiver-watch --help"
```

## MCP Tools

| Tool | Purpose |
|---|---|
| `resolve_league_context` | Setup-time league, owner, and roster ID resolution from league URL plus team/user name. |
| `weekly_briefing` | Weekly leaders plus waiver signal. |
| `weekly_performance_backtest` | Back-test weekly leaders and deterministic week-over-week movers. |
| `waiver_watch` | Trending unrostered players with projected value. |
| `my_lineup` | Current starters and bench for the configured roster, with a unified lineup table, actual points, status, injuries, and league-scored projections. |
| `lineup_recommendations` | Start/sit changes plus available-player comparisons against protected drop candidates, acquisition action, and market-aware waiver hints. |
| `waiver_wire_watch` | Actionable waiver shortlist with availability, projections, trends, status, and recent actuals. |
| `waiver_wire_by_position` | Top waiver and free-agent options by position, with drop candidate, projected gain, status, acquisition action, and FAAB guidance only for known waiver claims. |
| `trade_opportunities` | Every opposing team with needs, surplus, targets, mutual-fit offer scores, roster-balance risk, and reasoning. |
| `free_agent_watch` | Unrostered players ranked by projection. |
| `injury_watch` | Rostered players with injury/status risk. |
| `opponent_watch` | Weekly opponent starters, projection, and injury flags. |
| `league_team_watch` | Completed league transactions for a week. |
| `player_card` | Player metadata and chart-ready actual vs projected points. |

The MCP surface is intentionally decision-shaped. Add new MCP tools when they answer a useful fantasy question, not when they merely expose another raw Sleeper endpoint.

## League Scoring

Pass `--league-id` or MCP `league_id` when rankings should reflect your league's scoring settings.

Use `configure-context` when you want local Docker CLI and MCP runs to pick up a default league and roster:

```bash
make sleeper ARGS='configure-context https://sleeper.com/leagues/<league_id>/matchup --user-ref your_username'
```

League-scored rows include:

- `points`: calculated from the league's scoring settings
- `sleeper_points`: Sleeper's default points field when present
- `scoring_rules_matched`: count of scoring keys that contributed non-zero points
- `scoring_breakdown`: JSON-only contribution details by stat key

Lineup rows include:

- `lineup_status`: starter, bench, or reserve
- `actual_points`: current week points for that player
- `projected_points`: league-scored projection
- `status` and `injury_status`: separate availability fields
- `active_roster_spot`: false for reserve/IR stashes
- `stash_value`: true for reserve/IR players that should not be treated as easy cuts
- `depth_chart_order`, `depth_chart_position`, `bye_week`, and `source_metadata` when Sleeper exposes that context

`my_lineup` also returns `current_total`, `projected_total`, `projected_starter_total`, `season`, and `week`.

Sleeper often omits zero-value stat fields. Scoring code treats missing fields as `0`.

## Caching

The Python CLI and stdio MCP server use SQLite:

```text
./data/sleeper.db
```

The player map is cached separately:

```text
./data/players.json
```

Default TTLs:

| Endpoint Type | TTL |
|---|---:|
| NFL state | 5 minutes |
| Trending players | 5 minutes |
| Stats, projections, matchups, transactions | 15 minutes |
| League, roster, user, draft metadata | 1 hour |
| Player map | 6 hours |

Cache commands:

```bash
make sleeper ARGS="cache-info"
make sleeper ARGS="cache-clear"
make sleeper ARGS="cache-clear --expired-only"
make sleeper ARGS="--refresh-cache best-week --source projections --output table"
make sleeper ARGS="--no-cache state"
```

The Cloudflare Worker uses D1 instead of SQLite because Workers do not have a persistent local filesystem. Very large responses, such as the full Sleeper player map, are served without D1 writes so they do not exceed D1 value limits.

## Output Formats

Most CLI commands support:

```bash
--output json
--output csv
--output table
```

Use JSON for automation and LLM context, CSV for spreadsheet export, and table output for terminal inspection.
