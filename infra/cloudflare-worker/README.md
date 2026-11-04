# Cloudflare Worker MCP Adapter

This package exposes the curated Sleeper MCP tools over HTTP for `sleeper-mcp.neilbhavsar.com`.

It is intentionally a remote adapter, not a replacement for the Python core. Local CLI and stdio MCP development stays in `python/src/sleeper_tooling/`; hosted MCP behavior lives here.

## Runtime Shape

- Worker entrypoint: `src/index.ts`
- MCP endpoint: `/mcp`
- Auth boundary: Cloudflare Access on the hostname
- Cache storage: Cloudflare D1 through the `SLEEPER_CACHE_DB` binding
- Large response handling: oversized payloads such as the full player map are not written to D1
- Default context: `SLEEPER_DEFAULT_LEAGUE_ID`, `SLEEPER_DEFAULT_ROSTER_ID`, and `SLEEPER_DEFAULT_OWNER_ID`
- Tool surface: mirrors the curated Python MCP tools, including `weekly_performance_backtest`, `my_lineup`, `lineup_recommendations`, and `waiver_wire_watch`
- Season default: current calendar year when `season` is omitted; Sleeper state is used for current week when `week` is omitted

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
5. Deploy with `make worker-deploy`.

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
