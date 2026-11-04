COMPOSE := docker compose -f infra/docker/docker-compose.yml
WORKER_DEPLOY_ARGS ?=

.PHONY: build test integration-test decision-smoke context-backtest shell sleeper sync-data sync-status mcp worker-build worker-install worker-typecheck worker-test worker-dev worker-deploy local-up local-down docker-prune teardown ci-teardown clean

build:
	$(COMPOSE) build sleeper sleeper-mcp

test:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m "not integration"

integration-test:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m integration

decision-smoke:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/python sleeper -m sleeper_tooling.smoke $(ARGS)

context-backtest:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/python sleeper -m sleeper_tooling.context_backtest

shell:
	$(COMPOSE) run --rm --entrypoint bash sleeper

sleeper:
	$(COMPOSE) run --rm sleeper $(ARGS)

sync-data:
	$(COMPOSE) run --rm sleeper sync-data --output json $(ARGS)

sync-status:
	$(COMPOSE) run --rm sleeper sync-status --output json $(ARGS)

mcp:
	$(COMPOSE) run --rm -i sleeper-mcp

worker-install:
	$(COMPOSE) run --rm cloudflare-worker ci --no-audit --no-fund --progress=false

worker-build:
	$(COMPOSE) build cloudflare-worker

worker-typecheck:
	$(COMPOSE) run --rm cloudflare-worker run typecheck

worker-test:
	$(COMPOSE) run --rm cloudflare-worker run test

worker-dev:
	$(COMPOSE) run --rm --service-ports cloudflare-worker run dev

worker-deploy:
	$(COMPOSE) run --rm cloudflare-worker run deploy $(if $(WORKER_DEPLOY_ARGS),-- $(WORKER_DEPLOY_ARGS),)

local-up: build worker-build worker-install

local-down:
	$(COMPOSE) down --remove-orphans

docker-prune:
	docker container prune -f
	docker image prune -f

teardown: local-down docker-prune

ci-teardown:
	$(COMPOSE) down -v --remove-orphans
	docker container prune -f
	docker image prune -f

clean:
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

.PHONY: resolve-context worker-smoke worker-rollback

# Print resolved Sleeper context as env lines on stdout. Reads
# SLEEPER_DEFAULT_LEAGUE_URL and SLEEPER_DEFAULT_USER_REF from the environment.
resolve-context:
	@test -n "$$SLEEPER_DEFAULT_LEAGUE_URL" || { echo "SLEEPER_DEFAULT_LEAGUE_URL is required" >&2; exit 1; }
	@test -n "$$SLEEPER_DEFAULT_USER_REF" || { echo "SLEEPER_DEFAULT_USER_REF is required" >&2; exit 1; }
	@$(COMPOSE) run --rm -T sleeper configure-context "$$SLEEPER_DEFAULT_LEAGUE_URL" --user-ref "$$SLEEPER_DEFAULT_USER_REF" --output env

# POST JSON-RPC initialize + tools/list to URL (the /mcp endpoint) and fail
# unless both return HTTP 200 with a valid result. Set MCP_SMOKE_BEARER_TOKEN
# in the environment to send `Authorization: Bearer <token>` (for OAuth).
worker-smoke:
	@test -n "$(URL)" || { echo "Usage: make worker-smoke URL=https://<host>/mcp" >&2; exit 1; }
	$(COMPOSE) run --rm -T --no-deps -e MCP_SMOKE_BEARER_TOKEN -e WORKER_SMOKE_ATTEMPTS -e WORKER_SMOKE_DELAY_MS --entrypoint node cloudflare-worker scripts/worker-smoke.mjs "$(URL)"

# Roll back to a previous Worker version. Pass wrangler flags via ARGS, e.g.
# ARGS='<version-id> --message "reason"'. Needs CLOUDFLARE_API_TOKEN/ACCOUNT_ID.
worker-rollback:
	$(COMPOSE) run --rm -i cloudflare-worker exec -- wrangler rollback $(ARGS)
