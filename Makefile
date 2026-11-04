COMPOSE := docker compose -f infra/docker/docker-compose.yml
SECURITY_COMPOSE := $(COMPOSE) --profile security
COMPOSE_PROJECT ?= $(or $(COMPOSE_PROJECT_NAME),sleeper-mcp)
SCAN_IMAGES ?= $(COMPOSE_PROJECT)-sleeper $(COMPOSE_PROJECT)-cloudflare-worker
WORKER_DEPLOY_ARGS ?=

.PHONY: build test integration-test decision-smoke context-backtest shell sleeper sync-data sync-status mcp worker-build worker-install worker-typecheck worker-test worker-dev worker-deploy lock audit audit-python audit-worker lint-workflows secrets-scan scan-images sbom local-up local-down docker-prune teardown ci-teardown clean

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
	$(COMPOSE) run --rm --service-ports cloudflare-worker run dev -- --ip 0.0.0.0

worker-deploy:
	$(COMPOSE) run --rm cloudflare-worker run deploy $(if $(WORKER_DEPLOY_ARGS),-- $(WORKER_DEPLOY_ARGS),)

lock:
	$(COMPOSE) --profile tools run --rm uv lock $(ARGS)

# audit runs both halves and fails if either one reports vulnerabilities.
audit:
	@status=0; \
	$(MAKE) --no-print-directory audit-python || status=1; \
	$(MAKE) --no-print-directory audit-worker || status=1; \
	exit $$status

audit-python:
	$(SECURITY_COMPOSE) run --rm pip-audit

audit-worker:
	$(COMPOSE) run --rm cloudflare-worker audit --audit-level=high

lint-workflows:
	@status=0; \
	$(SECURITY_COMPOSE) run --rm actionlint || status=1; \
	$(SECURITY_COMPOSE) run --rm zizmor || status=1; \
	exit $$status

secrets-scan:
	$(SECURITY_COMPOSE) run --rm gitleaks

scan-images: build worker-build
	@status=0; for image in $(SCAN_IMAGES); do \
		$(SECURITY_COMPOSE) run --rm trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --table-mode detailed --exit-code 1 --no-progress $$image || status=1; \
	done; exit $$status

sbom: build worker-build
	mkdir -p dist/sbom
	for image in $(SCAN_IMAGES); do \
		$(SECURITY_COMPOSE) run --rm syft scan docker:$$image --quiet --output spdx-json=/out/$$image.spdx.json || exit 1; \
	done
	@ls -1 dist/sbom

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
