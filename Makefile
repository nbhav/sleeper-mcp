.PHONY: build test shell sleeper mcp clean

build:
	docker compose build

test:
	docker compose run --rm sleeper-test

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
