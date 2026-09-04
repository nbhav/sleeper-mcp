COMPOSE := docker compose -f infra/docker/docker-compose.yml

.PHONY: build test integration-test shell sleeper mcp worker-install worker-typecheck worker-dev worker-deploy clean

build:
	$(COMPOSE) build sleeper

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

worker-typecheck:
	$(COMPOSE) run --rm cloudflare-worker run typecheck

worker-dev:
	$(COMPOSE) run --rm --service-ports cloudflare-worker run dev

worker-deploy:
	$(COMPOSE) run --rm cloudflare-worker run deploy

clean:
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
