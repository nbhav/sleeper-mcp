# Cloudflare Worker MCP Adapter

This package exposes the curated Sleeper MCP tools over HTTP for `sleeper-mcp.neilbhavsar.com`.

It is intentionally a remote adapter, not a replacement for the Python core. Local CLI and stdio MCP development stays in `src/sleeper_tooling/`; hosted MCP behavior lives here.

## Runtime Shape

- Worker entrypoint: `src/index.ts`
- MCP endpoint: `/mcp`
- Auth boundary: Cloudflare Access on the hostname
- Cache storage: Cloudflare D1 through the `SLEEPER_CACHE_DB` binding
- Default context: `SLEEPER_DEFAULT_LEAGUE_ID` and `SLEEPER_DEFAULT_ROSTER_ID`

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
2. Put the D1 `database_id` into `wrangler.toml`.
3. Set Worker vars for league and roster defaults.
4. Protect `sleeper-mcp.neilbhavsar.com` with Cloudflare Access.
5. Deploy with `make worker-deploy`.

## GitHub Actions Deploy

If you want GitHub to deploy the Worker after CI passes on `main`, add these repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

The deploy workflow runs after the `CI` workflow succeeds on `main`, or manually through `workflow_dispatch`.
