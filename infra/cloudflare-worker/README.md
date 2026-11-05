# Cloudflare Worker MCP Adapter

This package exposes the curated Sleeper MCP tools over HTTP for `sleeper-mcp.neilbhavsar.com`.

It is intentionally a remote adapter, not a replacement for the Python core. Local CLI and stdio MCP development stays in `python/src/sleeper_tooling/`; hosted MCP behavior lives here.

## Runtime Shape

- Worker entrypoint: `src/index.ts`
- MCP endpoint: `/mcp`
- Auth boundary: Cloudflare Access on the hostname
- Cache storage: Cloudflare D1 through the `SLEEPER_CACHE_DB` binding
  (`src/response-cache.ts`)
- Large response handling: oversized payloads such as the full player map are
  not written to D1; they go to the Workers Cache API
- Normalized read model: D1 tables filled by the `scheduled()` handler
  (`src/sync.ts`, read in `src/d1-store.ts`); see below
- Default context: `SLEEPER_DEFAULT_LEAGUE_ID`, `SLEEPER_DEFAULT_ROSTER_ID`, and `SLEEPER_DEFAULT_OWNER_ID`
- Tool surface: mirrors the curated Python MCP tools, including `weekly_performance_backtest`, `my_lineup`, `lineup_recommendations`, and `waiver_wire_watch`
- Season default: current calendar year when `season` is omitted; Sleeper state is used for current week when `week` is omitted

## Normalized D1 Read Model And Scheduled Sync

Schema lives in `migrations/` (`wrangler d1 migrations`):

| Table | Contents |
| --- | --- |
| `api_response_cache` | Raw Sleeper responses (fetch/provenance fallback). |
| `sync_datasets` | Freshness ledger: one row per dataset/slice with `fetched_at`, row count, payload hash, and `complete`. |
| `player_week_rows` | One row per Sleeper stats/projections row per `(source, season, week, position)`; raw row JSON kept for output parity. |
| `player_snapshot_chunks` | Slim player map (fields the Worker reads) in a few JSON chunks. |
| `league_settings`, `league_users`, `rosters` | Default league (`SLEEPER_DEFAULT_LEAGUE_ID`) only. |
| `sync_runs` | One row per cron tick (14-day retention). |

Cron Triggers (`wrangler.toml [triggers]`):

- `*/15 * * * *`: rosters (30 min), league settings/users (daily), current-week
  projections (4 h) and stats (2 h), the latest completed week's stats (daily,
  for stat corrections), then backfill of completed weeks newest first. Each
  tick takes at most `SYNC_MAX_FETCHES_PER_TICK` Sleeper fetches (default 6,
  clamped to 20).
- `23 */6 * * *`: slim player snapshot and the response-cache row cap.

Per-tick subrequests (Free plan limit: 50 per invocation, and D1 queries count;
see <https://developers.cloudflare.com/workers/platform/limits/>): 1 ledger
read + up to 1 `/state/nfl` fetch + N fetches + up to N D1 batch writes + 1
final batch. Default N=6 gives at most 15; N=20 gives at most 43, and a guard
stops a tick at 45. Unchanged slices (same payload hash) only rewrite their
ledger row, which keeps D1 Free's 100k rows-written/day budget.

Reads. Tools read normalized rows when the ledger entry is fresh (projections
8 h, stats 3 h, rosters 1 h, players 8 h, league settings/users 36 h, completed
weeks 14 days) and fall back to the raw cache and Sleeper otherwise, including
when tables are missing. Usage context reads only the evaluated players' rows
in one batched query per pass. Decision tools add `data_freshness`
(`read_model`, `normalized_datasets`, `oldest_normalized_sync_at`,
`sleeper_fallback_reads`).

The scheduled path is not reachable over HTTP. To exercise it locally:

```bash
make worker-d1-migrate D1_MIGRATE_TARGET=--local
docker compose -f infra/docker/docker-compose.yml run --rm --service-ports cloudflare-worker \
  exec -- wrangler dev --ip 0.0.0.0 --test-scheduled
# then, from inside that container: GET /__scheduled?cron=*/15+*+*+*+*
```

`/__scheduled` exists only under `wrangler dev --test-scheduled`.

### Migrations

`make worker-d1-migrate` prints the D1 Time Travel bookmark, then runs
`wrangler d1 migrations apply sleeper-mcp-cache --remote`. The deploy workflow
runs it before `wrangler deploy`. Migrations must be expand/contract only: add
tables, defaulted columns, and indexes; never drop or rename anything the
previous Worker version reads, so `make worker-rollback` stays safe. To undo a
bad migration, restore the printed bookmark:

```bash
docker compose -f infra/docker/docker-compose.yml run --rm cloudflare-worker \
  exec -- wrangler d1 time-travel restore sleeper-mcp-cache --bookmark=<bookmark>
```

## League Context

The hosted Worker cannot write environment variables at request time. Use
`resolve_league_context` over MCP, then set the returned `cloudflare_vars` as
Worker vars before deploy or through the Cloudflare dashboard.

Example MCP tool call arguments:

```json
{
  "league_ref": "https://sleeper.com/leagues/<league_id>/matchup",
  "user_ref": "your_username"
}
```

## Docker-Only Commands

Run from the repo root:

```bash
make worker-install
make worker-typecheck
make worker-dev
make worker-deploy
```

Do not run `npm install` on the host.

## Deployment Checklist

1. Create a D1 database named `sleeper-mcp-cache`.
2. Put the D1 `database_id` into `wrangler.toml` for local deploys, or set
   `CLOUDFLARE_D1_DATABASE_ID` in the GitHub `production` environment for
   Actions deploys.
3. Set Worker vars for league, roster, and owner defaults.
4. Protect `sleeper-mcp.neilbhavsar.com` with Cloudflare Access.
5. Apply schema with `make worker-d1-migrate` (always before deploying code).
6. Deploy with `make worker-deploy`.

## GitHub Actions Deploy

GitHub deploys the Worker after CI passes for a push to `main` in this
repository (never for forks or pull requests), or manually through
`workflow_dispatch`. All inputs live in the GitHub `production` environment,
not repository-level secrets.

`production` environment secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `MCP_SMOKE_BEARER_TOKEN` (optional, for when `/mcp` requires OAuth)

`production` environment variables:

- `CLOUDFLARE_D1_DATABASE_ID`
- `SLEEPER_DEFAULT_LEAGUE_URL`
- `SLEEPER_DEFAULT_USER_REF`

The deploy workflow resolves the URL/user pair through `make resolve-context`,
passes the resulting IDs to Wrangler as deploy-time vars, patches the D1
`database_id` into `wrangler.toml` on the Actions runner, deploys, and then
runs `make worker-smoke` against `https://sleeper-mcp.neilbhavsar.com/mcp`.
See `docs/development.md` for the full owner checklist.

## Smoke Test And Rollback

```bash
make worker-smoke URL=https://sleeper-mcp.neilbhavsar.com/mcp
make worker-rollback ARGS='<version-id> --message "reason" --yes'
```

`worker-smoke` runs `scripts/worker-smoke.mjs` in the Worker container. It
POSTs JSON-RPC `initialize` and `tools/list` and fails unless both return HTTP
200 with a valid JSON-RPC result. It does not follow redirects, so a
Cloudflare Access login redirect fails the check. When `/mcp` moves to OAuth,
set `MCP_SMOKE_BEARER_TOKEN` and it is sent as `Authorization: Bearer <token>`.

`wrangler.toml` disables the `workers.dev` route and preview URLs, so the
Worker is only served on the custom domain, and enables Workers observability.
