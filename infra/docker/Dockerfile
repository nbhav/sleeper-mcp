FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY python/pyproject.toml python/uv.lock ./
RUN uv sync --locked --no-install-project --extra dev

COPY python/src/ ./src/
COPY python/tests/ ./tests/
COPY docs/player-context-source-audit.md ./docs/player-context-source-audit.md
RUN uv sync --locked --extra dev

ENTRYPOINT ["/app/.venv/bin/sleeper"]
