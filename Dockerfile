FROM docker.io/library/python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --package weave-api
EXPOSE 8000
CMD ["uvicorn", "weave_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
