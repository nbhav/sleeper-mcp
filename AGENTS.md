# Agent Instructions

Read `ai/AGENTS.md` before changing behavior.

## Always-On Repo Defaults

- Start by reading the repo instructions in this file and `ai/AGENTS.md`.
- Use Docker Compose for all build, test, CLI, MCP, Worker, and Python execution.
- Never install Python or Node packages on the host, and never create a local virtualenv.
- Prefer decision-focused workflows and MCP tools over raw Sleeper endpoint work.
- Prefer JSON output for automation unless the user asks for a table.
- After Docker workflow runs, tear down Compose resources with `docker compose -f infra/docker/docker-compose.yml down --remove-orphans` and prune stopped containers plus dangling images when appropriate. Do not remove named volumes or `data/` unless explicitly asked.
