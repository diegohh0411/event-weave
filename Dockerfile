FROM docker.io/library/python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages ./packages
# All workspace packages, so workers can load provider adapters via entry points.
RUN uv sync --frozen --no-dev --all-packages

# Crawl container: adds crawl4ai and its browser (weave-providers[crawl]); not used by the stack's workers.
FROM base AS crawler
RUN uv sync --frozen --no-dev --all-packages --extra crawl && crawl4ai-setup
COPY outlets.example.yaml /app/outlets.yaml

FROM base AS app
EXPOSE 8000
CMD ["uvicorn", "weave_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
