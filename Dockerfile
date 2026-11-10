FROM docker.io/library/python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages ./packages
# All workspace packages, so workers can load provider adapters via entry points.
RUN uv sync --frozen --no-dev --all-packages

# Crawl container: adds crawl4ai and its browser (weave-providers[crawl]); runs the long-lived crawl queue worker.
FROM base AS crawler
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN uv sync --frozen --no-dev --all-packages --extra crawl && crawl4ai-setup
COPY outlets.example.yaml /app/outlets.yaml
CMD ["weave", "crawl-worker"]

# No application credentials or writable application files in the renderer.
# Headful Chromium on a virtual X display: headless mode leaks "HeadlessChrome" in client hints.
FROM crawler AS crawl-browser
RUN apt-get update && apt-get install -y --no-install-recommends xvfb fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp CRAWL4_AI_BASE_DIRECTORY=/tmp
USER 10001:10001
COPY --chmod=0755 deploy/crawl-browser/entrypoint.sh /usr/local/bin/crawl-browser-entrypoint
ENTRYPOINT ["crawl-browser-entrypoint"]
CMD ["uvicorn", "weave_providers.crawl.browser_service:app", "--host", "0.0.0.0", "--port", "8081", "--limit-concurrency", "8", "--timeout-keep-alive", "5"]

# Stanza service: pinned stanza==1.10.1 (weave-extractors[stanza]); models live in the stanza-models volume.
FROM base AS stanza
RUN uv sync --frozen --no-dev --all-packages --extra stanza
ENV STANZA_MODEL_DIR=/models
EXPOSE 8001
CMD ["uvicorn", "weave_extractors.stanza_service:app", "--factory", "--host", "0.0.0.0", "--port", "8001"]

FROM base AS app
EXPOSE 8000
CMD ["uvicorn", "weave_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
