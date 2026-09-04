# Development And Deployment

All development commands run through Docker or Docker Compose. Do not install Python, Node, package dependencies, or virtualenvs on the host.

## Commands

| Target | Action |
|---|---|
| `make build` | Build the Python Docker image. |
| `make test` | Run offline unit tests inside Docker. |
| `make integration-test` | Run live Sleeper API integration tests inside Docker. |
| `make sleeper ARGS="..."` | Run the Sleeper CLI inside Docker. |
| `make mcp` | Run the stdio MCP server inside Docker. |
| `make worker-install` | Install Worker dependencies inside Docker with `npm ci`. |
| `make worker-typecheck` | Typecheck the Cloudflare Worker inside Docker. |
| `make worker-dev` | Run Wrangler dev inside Docker. |
| `make worker-deploy` | Deploy the Cloudflare Worker through Docker. |
| `make shell` | Open a shell inside the Python app container. |
| `make clean` | Stop containers and remove generated Python cache files. |

The root Makefile wraps:

```text
docker compose -f infra/docker/docker-compose.yml
```

## Tests

Run mocked unit tests:

```bash
make test
```

Run live Sleeper API smoke tests:

```bash
make integration-test
```

Run Worker typecheck:

```bash
make worker-install
make worker-typecheck
```

## CI

GitHub Actions runs these protected checks on pull requests and pushes to `main`:

- `Python unit tests`
- `Sleeper API integration tests`
- `Cloudflare Worker typecheck`

`main` is protected with required checks, admin enforcement enabled, and force-pushes/deletions disabled.

## Cloudflare Deploy

The deploy workflow runs after the `CI` workflow succeeds on `main`, or manually through `workflow_dispatch`.

Required repository secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

Deploy locally through Docker:

```bash
make worker-deploy
```

## Adding Behavior

When adding or changing Sleeper behavior:

1. Add API wrappers in `python/src/sleeper_tooling/client.py`.
2. Add CLI commands in `python/src/sleeper_tooling/cli.py` if terminal usage matters.
3. Add decision reports in `python/src/sleeper_tooling/decision_reports.py` when the behavior answers a fantasy-management question.
4. Add MCP registration in `python/src/sleeper_tooling/mcp_server.py` for assistant-facing tools.
5. Add MCP orchestration in `python/src/sleeper_tooling/mcp_tools.py`.
6. Mirror hosted MCP behavior in `infra/cloudflare-worker/src/index.ts` when the remote adapter should support it.
7. Add mocked tests under `python/tests/`.
8. Add live API smoke checks under `python/tests/integration/` when new Sleeper API assumptions matter.

Keep the MCP surface curated and decision-focused. Do not add one tool per raw Sleeper endpoint unless there is a clear product reason.

## Generated Files

These are intentionally ignored:

- `data/`
- `infra/cloudflare-worker/node_modules/`
- `infra/cloudflare-worker/.wrangler/`
- `infra/cloudflare-worker/.dev.vars`
- Python cache directories and `.pyc` files
