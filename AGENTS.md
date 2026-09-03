# Agent Instructions

Read `README.md` and `AI_AGENT_HANDOFF.md` before changing behavior.

## Project Shape

This repo has two runtime surfaces:

- Python core under `src/sleeper_tooling/`: local CLI, stdio MCP server, Sleeper client, SQLite cache, report shaping, and league scoring.
- TypeScript Worker under `cloudflare-worker/`: remote HTTP MCP adapter for `sleeper-mcp.neilbhavsar.com`, intended to run behind Cloudflare Access with D1 caching.

Keep the MCP tool surface curated and decision-focused. Do not expose raw Sleeper API endpoints as individual MCP tools unless there is a clear product reason.

## Constraints

- Do not install packages on the host.
- Do not create a local virtualenv.
- Use Docker Compose for build, test, local MCP, Worker install, Worker typecheck, and Worker deploy.
- Keep unit tests mocked.
- Use live Sleeper calls only as integration smoke checks.
- Keep Python CLI response caching on SQLite by default.
- Keep Worker response caching on D1; Workers cannot use the local SQLite file cache.
- Keep generated `data/`, `cloudflare-worker/node_modules/`, `.wrangler/`, and `.dev.vars` out of git.

## Commands

```bash
make build
make test
make integration-test
make sleeper ARGS="state"
make mcp
make worker-install
make worker-typecheck
make worker-dev
make worker-deploy
```

Useful fantasy checks:

```bash
make sleeper ARGS="best-by-team --source projections --position RB --output table"
make sleeper ARGS="weekly-briefing --source projections --output json"
make sleeper ARGS="best-week --league-id <league_id> --source projections --output table"
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --output table"
make sleeper ARGS="injury-watch <league_id> --output table"
```

For MCP registration with built-in context, set:

```text
SLEEPER_DEFAULT_LEAGUE_ID
SLEEPER_DEFAULT_ROSTER_ID
```

## Change Guidelines

- Add Sleeper API wrapper methods in `src/sleeper_tooling/client.py`.
- Add CLI commands in `src/sleeper_tooling/cli.py`.
- Add fantasy decision logic in `src/sleeper_tooling/decision_reports.py`.
- Keep stdio MCP registration in `src/sleeper_tooling/mcp_server.py`.
- Keep local MCP orchestration in `src/sleeper_tooling/mcp_tools.py`.
- Keep remote HTTP MCP behavior in `cloudflare-worker/src/index.ts`.
- Add mocked tests under `tests/`.
- Add live API checks under `tests/integration/` and mark them with `pytest.mark.integration`.
- Run `make test`; also run `make integration-test` when live API behavior changes and `make worker-typecheck` when Worker code changes.
