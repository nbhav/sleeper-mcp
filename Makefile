.PHONY: build test integration-test shell sleeper mcp clean

build:
	docker compose build sleeper

test:
	docker compose run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m "not integration"

integration-test:
	docker compose run --rm --entrypoint /app/.venv/bin/pytest sleeper -q -m integration

shell:
	docker compose run --rm --entrypoint bash sleeper

sleeper:
	docker compose run --rm sleeper $(ARGS)

mcp:
	docker compose run --rm -i sleeper-mcp

clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
