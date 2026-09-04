# Claude Instructions

Read `ai/AI_AGENT_HANDOFF.md` first. It is the portable context file for using this Sleeper fantasy football tooling from this repo or another repo. Use `docs/` for detailed tool, MCP, and development references.

Follow `ai/AGENTS.md` for contributor rules. The short version:

- Use Docker Compose only; do not install Python or Node packages on the host.
- Use the Python CLI and stdio MCP server for local workflows.
- Use the Cloudflare Worker under `infra/cloudflare-worker/` only for remote HTTP MCP on `sleeper-mcp.neilbhavsar.com`.
- Prefer JSON output for automation.
- Prefer decision/chained commands and MCP tools over raw endpoint replication.
- Prefer `weekly_performance_backtest` for historical leaders and week-over-week movement.
- Prefer `waiver_wire_watch` when waiver recommendations must exclude rostered players and include evidence.
- Set `SLEEPER_DEFAULT_LEAGUE_ID` and `SLEEPER_DEFAULT_ROSTER_ID` when registering MCP for a specific league/team.
- Use `--league-id` when rankings should reflect a league's custom scoring settings.
- Keep Python cache behavior on SQLite and Worker cache behavior on D1.
- Verify Python changes with `make test`; add `make integration-test` for Sleeper API assumptions.
- Verify Worker changes with `make worker-typecheck`.
