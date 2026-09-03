# Claude Instructions

Read `AI_AGENT_HANDOFF.md` first. It is the portable context file for using this Sleeper fantasy football tooling from this repo or from another repo.

Follow the same project rules as `AGENTS.md`:

- Use Docker Compose only.
- Do not install Python packages on the host.
- Keep tests mocked.
- Prefer JSON output for automation.
- Prefer decision/chained CLI commands `waiver-watch`, `injury-watch`, `best-week`, `best-by-team`, `weekly-briefing`, and `trending --enrich` before writing custom orchestration.
- Use the MCP server when available; it exposes curated decision tools instead of raw Sleeper endpoints.
- Use `--league-id` when rankings should reflect a league's custom scoring settings.
- Use the default SQLite cache; pass `--refresh-cache` only when live data is required.
- Use `make test` to verify changes.
