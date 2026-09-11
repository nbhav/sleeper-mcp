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

The HTTP response cache and the normalized decision database have separate
responsibilities:

- `api_cache` stores raw Sleeper HTTP responses by request URL. It is an
  endpoint cache for rate-limit and latency control, not an analytical model.
- Normalized decision tables are the planned durable read model for trendable
  fantasy questions. They should store typed players, teams, weekly stats,
  projections, matchups, roster snapshots, and tall stat rows derived from raw
  Sleeper payloads.
- Decision tools should read normalized tables when they are fresh enough, then
  fall back to live Sleeper reads plus `api_cache` when normalized data is
  missing or stale.

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
| `sync-data` | Planned: populate normalized decision tables for the current/default season window. |
| `sync-status` | Planned: report normalized table coverage, newest synced week, and stale/missing sources. |

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
| `decision_smoke_report` | Compact lineup, waiver, and trade smoke workflow output as display-ready Markdown tables or JSON. |
| `free_agent_watch` | Unrostered players ranked by projection. |
| `injury_watch` | Rostered players with injury/status risk. |
| `opponent_watch` | Weekly opponent starters, projection, and injury flags. |
| `league_team_watch` | Completed league transactions for a week. |
| `player_card` | Player metadata and chart-ready actual vs projected points. |
| `decision_data_status` | Planned: MCP freshness check for normalized decision data before lineup, waiver, trade, or trend reads. |
| `sync_decision_data` | Planned: MCP-triggered normalized sync for missing or stale decision data. |
| `player_stat_trends` | Planned: trendable player stat reads backed by normalized weekly stat rows. |
| `position_stat_leaders` | Planned: position leader reads backed by normalized weekly stat rows. |

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

## Data Model And Caching

The tools mostly use Sleeper as the source of truth and keep a small local cache
so repeated CLI, MCP, and Worker calls do not refetch the same endpoint over and
over.

### Normalized Decision Data

Normalized decision data is the planned analytical layer above raw Sleeper
responses. It should be generated from Sleeper state, players, stats,
projections, league, roster, matchup, transaction, and trending endpoints, then
stored in SQLite locally and D1 remotely as queryable tables.

Planned responsibilities:

- Convert Sleeper numeric stat values automatically to numeric database fields.
  Sleeper may omit zero stats or encode values inconsistently across payloads;
  normalization should coerce parseable stat values to numbers and treat
  missing stat keys as `0` for scoring and comparisons.
- Store weekly player stats in a tall trend model: one row per `season`, `week`,
  `source`, `player_id`, and `stat_key`, with the numeric stat value in a
  single value column. This makes week-over-week deltas, rolling windows, and
  arbitrary stat-key leaderboards possible without schema changes for every new
  Sleeper stat.
- Keep typed dimension/snapshot tables for players, teams, leagues, rosters,
  matchups, projections, and source freshness metadata so decision tools can
  answer without reparsing raw JSON on every call.
- Retain two seasons by default. Sync jobs should keep the current season plus
  one prior season unless the caller explicitly requests a wider or narrower
  retention window.
- Track freshness at the source/table/window level so agents can know whether a
  decision read is fresh, stale, missing, or falling back.

Planned CLI workflow:

```bash
make sleeper ARGS="sync-status --output json"
make sleeper ARGS="sync-data --season <season> --output json"
```

Planned MCP workflow:

```text
decision_data_status
sync_decision_data
player_stat_trends
position_stat_leaders
```

Agents should call `decision_data_status` before trendable lineup, waiver,
trade, or historical decisions. If normalized data is missing or stale, they
should call `sync_decision_data` when available, then retry the decision read.
If sync is unavailable or still stale, tools should return fallback metadata and
use the existing live Sleeper plus `api_cache` path rather than failing a
decision outright.

As of this docs update, these normalized sync and trend tools may still be in
flight on other issue branches. Treat this section as the target workflow until
the corresponding CLI commands, MCP registrations, SQLite tables, and Worker D1
parity are present in the active branch.

### Python And Stdio MCP

The Python CLI and stdio MCP server use SQLite for HTTP response caching:

```text
./data/sleeper.db
```

The cache database contains:

| Table | Purpose | Used Today |
|---|---|---:|
| `api_cache` | Cached Sleeper HTTP responses keyed by the full request URL, including query parameters. Stores `cache_key`, `url`, `response_json`, `fetched_at`, and `ttl_seconds`. | Yes |
| `player_context_overrides` | Local/manual player context overrides for future provider or manual enrichment. Stores `player_id`, `context_json`, `source`, and `updated_at`. | Reserved |
| `team_schedule_context` | Local/manual team schedule and bye-week context. Stores `season`, `team`, `bye_week`, `schedule_json`, `source`, and `updated_at`. | Reserved |
| `context_source_timestamps` | Source freshness metadata for local context providers. Stores `source`, `fetched_at`, and `metadata_json`. | Reserved |

On each cached Sleeper request, the Python client builds the request URL, uses it
as the cache key, and checks `api_cache` unless caching is disabled or
`--refresh-cache` is set. A fresh row is returned directly. A missing or expired
row causes a live Sleeper request, then the response is written back with the
endpoint TTL.

Decision reports enrich player rows from Sleeper player/projection/roster data
first. The reserved context tables are schema support for future or manual
context, not the primary source for current recommendations.

The player map is cached separately:

```text
./data/players.json
```

The player map file is loaded directly when present. The HTTP endpoint used to
create or refresh it still uses the 6-hour player-map TTL, but the file itself is
not currently evicted by age.

The cache path is resolved as `--cache-db`, then `SLEEPER_CACHE_DB`, then
`${SLEEPER_CACHE_DIR:-/data}/sleeper.db` inside Docker.

Default TTLs:

| URL Pattern Or Data | TTL |
|---|---:|
| `/state/nfl` | 5 minutes |
| `/trending/*` | 5 minutes |
| `/stats/*`, `/projections/*`, `/matchups/*`, `/transactions/*` | 15 minutes |
| `/players/nfl` HTTP response | 6 hours |
| League, roster, user, draft, and other metadata endpoints | 1 hour |
| `./data/players.json` file | No age-based eviction |

Cache commands:

```bash
make sleeper ARGS="cache-info"
make sleeper ARGS="cache-clear"
make sleeper ARGS="cache-clear --expired-only"
make sleeper ARGS="--refresh-cache best-week --source projections --output table"
make sleeper ARGS="--no-cache state"
```

### Cloudflare Worker

The Cloudflare Worker uses D1 instead of SQLite because Workers do not have a
persistent local filesystem. D1 stores the `api_response_cache` table with
`cache_key`, `url`, `response_json`, `expires_at`, and `created_at`.

Worker requests check D1 first and return a row only when `expires_at` is still
in the future. Cache misses and expired rows hit Sleeper and write a replacement
row. Very large responses, such as the full Sleeper player map, are served
without D1 writes so they do not exceed D1 value limits.

## Output Formats

Most CLI commands support:

```bash
--output json
--output csv
--output table
```

Use JSON for automation and LLM context, CSV for spreadsheet export, and table output for terminal inspection.
