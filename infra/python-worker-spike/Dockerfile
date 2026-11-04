FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM node:22-bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/root/.cache/uv/python
WORKDIR /worker
