# Claude Instructions

Read `AI_AGENT_HANDOFF.md` first. It is the portable context file for using this Sleeper fantasy football tooling from this repo or another repo.

Follow `AGENTS.md` for contributor rules. The short version:

- Use Docker Compose only; do not install Python or Node packages on the host.
- Use the Python CLI and stdio MCP server for local workflows.
- Use the Cloudflare Worker only for remote HTTP MCP on `sleeper-mcp.neilbhavsar.com`.
- Prefer JSON output for automation.
- Prefer decision/chained commands and MCP tools over raw endpoint replication.
- Set `SLEEPER_DEFAULT_LEAGUE_ID` and `SLEEPER_DEFAULT_ROSTER_ID` when registering MCP for a specific league/team.
- Use `--league-id` when rankings should reflect a league's custom scoring settings.
- Keep Python cache behavior on SQLite and Worker cache behavior on D1.
- Verify Python changes with `make test`; add `make integration-test` for Sleeper API assumptions.
- Verify Worker changes with `make worker-typecheck`.
