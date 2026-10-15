# ReclaimAI worker (Cloud Run, Pub/Sub push target). Build from the repository root:
#   docker build -f workers/Dockerfile -t reclaim-worker .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api apps/api
COPY workers workers
RUN uv sync --frozen --no-dev --package reclaim-worker --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home /app reclaim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY fixtures fixtures
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 FIXTURES_DIR=/app/fixtures APP_MODE=cloud OUTBOX_LOOP=false
USER reclaim
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn reclaim_worker.app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --proxy-headers"]
