# Development And Deployment

All development commands run through Docker or Docker Compose. Do not install Python, Node, package dependencies, or virtualenvs on the host.

## Commands

| Target | Action |
|---|---|
| `make build` | Build the Python Docker image. |
| `make test` | Run offline unit tests inside Docker. |
| `make integration-test` | Run live Sleeper API integration tests inside Docker. |
| `make decision-smoke` | Run a compact live lineup, waiver, and trade workflow smoke check inside Docker. |
| `make sleeper ARGS="..."` | Run the Sleeper CLI inside Docker. |
| `make mcp` | Run the stdio MCP server inside Docker. |
| `make worker-build` | Build the Cloudflare Worker Docker image. |
| `make worker-install` | Install Worker dependencies inside Docker with `npm ci`. |
| `make worker-typecheck` | Typecheck the Cloudflare Worker inside Docker. |
| `make worker-test` | Run the Cloudflare Worker tests inside Docker. |
| `make worker-dev` | Run Wrangler dev inside Docker, bound to `0.0.0.0` and published on `localhost:8787`. |
| `make worker-deploy` | Deploy the Cloudflare Worker through Docker. |
| `make lock` | Regenerate `python/uv.lock` inside the pinned uv container (`ARGS="--upgrade"` to bump). |
| `make audit` | Run `pip-audit` against the locked Python deps and `npm audit --audit-level=high` for the Worker; fails if either reports issues. |
| `make lint-workflows` | Lint `.github/workflows` with actionlint and zizmor (offline). |
| `make secrets-scan` | Scan the working tree for secrets with gitleaks (redacted output, config in `.gitleaks.toml`). |
| `make scan-images` | Build the Python and Worker images, then scan them with trivy for fixable HIGH/CRITICAL vulnerabilities. |
| `make sbom` | Build the images and write SPDX JSON SBOMs with syft to `dist/sbom/` (git-ignored). |
| `make worker-smoke URL=...` | POST JSON-RPC `initialize` and `tools/list` to an MCP URL from the Worker container; fails unless both return HTTP 200 with a valid result. Set `MCP_SMOKE_BEARER_TOKEN` to send a bearer token. |
| `make worker-rollback ARGS="..."` | Run `wrangler rollback` inside Docker (needs Cloudflare credentials in the environment). |
| `make resolve-context` | Print resolved `SLEEPER_DEFAULT_*` env lines for `SLEEPER_DEFAULT_LEAGUE_URL` and `SLEEPER_DEFAULT_USER_REF` from the environment (used by the deploy workflow). |
| `make local-up` | Prepare local Docker workflow dependencies by building Python images and installing Worker dependencies in Docker. |
| `make local-down` | Stop Compose resources and remove orphans without deleting named volumes. |
| `make docker-prune` | Prune stopped containers and dangling images. |
| `make teardown` | Run `local-down` plus `docker-prune` after workflow checks. |
| `make ci-teardown` | Stop Compose resources, remove volumes, and prune stopped containers plus dangling images in CI. |
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

Run Worker typecheck and tests:

```bash
make worker-install
make worker-typecheck
make worker-test
```

After local Docker workflow checks:

```bash
make teardown
```

After simple one-off commands that already use Compose `run --rm`, `make local-down`
is enough when only the temporary Compose network needs cleanup. Use
`make teardown` after builds or checks where pruning stopped containers and
dangling images is useful.

CI and deploy workflows use:

```bash
make ci-teardown
```

## CI

GitHub Actions runs these protected checks on pull requests and pushes to `main`:

- `Python unit tests`
- `Sleeper API integration tests`
- `Cloudflare Worker typecheck`

`main` is protected with required checks, admin enforcement enabled, and force-pushes/deletions disabled.

CI also runs a non-required `security` job that runs `make audit`,
`make lint-workflows`, and `make secrets-scan`. Each step runs even if an
earlier one fails, so one job shows all findings.

## Supply Chain And Security Tooling

- Python dependencies are locked in `python/uv.lock`. The image installs them
  with `uv sync --locked`, so the build fails if `pyproject.toml` and the lock
  drift apart. After editing dependencies, run `make lock` and commit the lock.
- Base images in `infra/docker/Dockerfile` and `infra/cloudflare-worker/Dockerfile`,
  and every tool image in `infra/docker/docker-compose.yml`, are pinned as
  `image:tag@sha256:...`. Dependabot (`.github/dependabot.yml`) opens update PRs
  for uv, npm, Dockerfiles, Compose images, and GitHub Actions.
- The security tools are Compose services under the `security` profile (and
  `uv` under the `tools` profile), so `make build`, `make local-down`, and
  `docker compose up` ignore them. `scan-images` and `sbom` mount the Docker
  socket read-only.
- Report vulnerabilities as described in `SECURITY.md`.

## Cloudflare Deploy

The deploy workflow (`.github/workflows/deploy-cloudflare.yml`) runs manually
through `workflow_dispatch`, or after the `CI` workflow succeeds for a `push` to
`main` in this repository. CI runs from forks or pull requests never deploy. It
checks out the exact commit CI tested (`workflow_run.head_sha`).

All deploy inputs live in the GitHub `production` environment (Settings ->
Environments -> `production`), not in repository-level secrets.

`production` environment secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `MCP_SMOKE_BEARER_TOKEN` (optional; only needed once `/mcp` requires OAuth)

`production` environment variables:

- `CLOUDFLARE_D1_DATABASE_ID`
- `SLEEPER_DEFAULT_LEAGUE_URL`
- `SLEEPER_DEFAULT_USER_REF`

The deploy workflow resolves the Sleeper values through `make resolve-context`
into `SLEEPER_DEFAULT_LEAGUE_ID`, `SLEEPER_DEFAULT_ROSTER_ID`, and
`SLEEPER_DEFAULT_OWNER_ID`, then passes them to `wrangler deploy --var`. It
also writes `CLOUDFLARE_D1_DATABASE_ID` into the checked-out `wrangler.toml` on
the Actions runner before deployment, so the repository can keep a placeholder
database id. The Cloudflare credentials are scoped to the migrate and deploy
steps only; dependency installation never sees them. Before deploying code, the
workflow runs `make worker-d1-migrate`, which prints the D1 Time Travel bookmark
and then applies `infra/cloudflare-worker/migrations/` with
`wrangler d1 migrations apply --remote`. Migrations are additive
(expand/contract), so the previous Worker version keeps working against the new
schema and `make worker-rollback` stays safe. After deploying, the workflow runs
`make worker-smoke URL=https://sleeper-mcp.neilbhavsar.com/mcp`.

Owner checklist for a working Actions deploy:

1. In the `production` environment, add secrets `CLOUDFLARE_API_TOKEN` and
   `CLOUDFLARE_ACCOUNT_ID`.
2. In the `production` environment, add variables `CLOUDFLARE_D1_DATABASE_ID`,
   `SLEEPER_DEFAULT_LEAGUE_URL`, and `SLEEPER_DEFAULT_USER_REF`.
3. Restrict `production` deployment branches to `main`, so a manual dispatch
   from another branch cannot use the token.
4. Remove stale repository-level copies of the Cloudflare secrets.
5. Give `CLOUDFLARE_API_TOKEN` D1 edit permission (migrations and Time Travel
   need it in addition to Workers deploy).
6. Make sure the post-deploy smoke can reach `/mcp`. If Cloudflare Access
   protects the hostname, an unauthenticated POST from Actions is redirected
   and the smoke step fails. Exempt `/mcp` from Access, or add
   `MCP_SMOKE_BEARER_TOKEN` once OAuth lands.

`wrangler.toml` sets `workers_dev = false` and `preview_urls = false`, so the
Worker is only reachable on the custom domain, and enables Workers
observability logs.

Deploy, smoke test, or roll back locally through Docker:

```bash
make worker-deploy
make worker-smoke URL=https://sleeper-mcp.neilbhavsar.com/mcp
MCP_SMOKE_BEARER_TOKEN=<token> make worker-smoke URL=https://sleeper-mcp.neilbhavsar.com/mcp
make worker-rollback
make worker-rollback ARGS='<version-id> --message "reason" --yes'
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
  - includes `data/sleeper-mcp.env`, SQLite cache files, and cached player maps
- `infra/cloudflare-worker/node_modules/`
- `infra/cloudflare-worker/.wrangler/`
- `infra/cloudflare-worker/.dev.vars`
- Python cache directories and `.pyc` files
