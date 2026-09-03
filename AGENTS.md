# Agent Instructions

This project is Docker-only Python tooling for the Sleeper fantasy football API.

Before changing code, read:

- `README.md`
- `AI_AGENT_HANDOFF.md`

Core constraints:

- Do not install packages on the host.
- Do not create a local virtualenv.
- Use Docker Compose for build, test, and runtime.
- Keep unit tests mocked; use live Sleeper calls only as integration smoke checks.
- Keep SQLite API response caching enabled by default for CLI commands.
- The large Sleeper player map should be cached under `./data`, which is gitignored.

Useful commands:

```bash
make build
make test
make integration-test
make sleeper ARGS="state"
make sleeper ARGS="players --position QB --active --limit 20 --output table"
make sleeper ARGS="best-by-team --source projections --position RB --output table"
make sleeper ARGS="weekly-briefing --source projections --output json"
make sleeper ARGS="best-week --league-id <league_id> --source projections --output table"
make sleeper ARGS="waiver-watch <league_id> --positions RB,WR,TE --output table"
make sleeper ARGS="injury-watch <league_id> --output table"
make sleeper ARGS="cache-info"
```

MCP server:

```bash
make mcp
```

When adding functionality:

- Add API wrapper methods in `src/sleeper_tooling/client.py`.
- Add CLI commands in `src/sleeper_tooling/cli.py`.
- Prefer decision-focused commands over exposing raw Sleeper endpoints.
- Keep decision report helpers in `src/sleeper_tooling/decision_reports.py`.
- Keep MCP registration compact in `src/sleeper_tooling/mcp_server.py`.
- Keep MCP orchestration in `src/sleeper_tooling/mcp_tools.py`.
- Add report helpers in `src/sleeper_tooling/reports.py`.
- Keep league scoring helpers in `src/sleeper_tooling/scoring.py`.
- Keep response cache helpers in `src/sleeper_tooling/db.py`.
- Add mocked tests in `tests/`.
- Add live Sleeper API checks in `tests/integration/` and mark them with `pytest.mark.integration`.
