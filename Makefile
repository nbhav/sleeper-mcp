COMPOSE := docker compose -f infra/docker/docker-compose.yml
WORKER_DEPLOY_ARGS ?=

.PHONY: build test integration-test shell sleeper mcp worker-build worker-install worker-typecheck worker-dev worker-deploy local-up local-down docker-prune teardown ci-teardown clean

build:
	$(COMPOSE) build sleeper sleeper-mcp

test:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m "not integration"

integration-test:
	$(COMPOSE) run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m integration

shell:
	$(COMPOSE) run --rm --entrypoint bash sleeper

sleeper:
	$(COMPOSE) run --rm sleeper $(ARGS)

mcp:
	$(COMPOSE) run --rm -i sleeper-mcp

worker-install:
	$(COMPOSE) run --rm cloudflare-worker ci --no-audit --no-fund --progress=false

worker-build:
	$(COMPOSE) build cloudflare-worker

worker-typecheck:
	$(COMPOSE) run --rm cloudflare-worker run typecheck

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
