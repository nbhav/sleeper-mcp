FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY python/pyproject.toml ./
RUN uv sync --no-install-project --extra dev

COPY python/src/ ./src/
COPY python/tests/ ./tests/
RUN uv sync --extra dev

ENTRYPOINT ["/app/.venv/bin/sleeper"]
