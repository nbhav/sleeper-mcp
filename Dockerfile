FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY src/ ./src/
COPY tests/ ./tests/
RUN uv sync --no-dev

ENTRYPOINT ["/app/.venv/bin/sleeper"]
