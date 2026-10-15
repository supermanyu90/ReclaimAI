# ReclaimAI API (Cloud Run). Build from the repository root:
#   docker build -f apps/api/Dockerfile -t reclaim-api .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api apps/api
COPY workers workers
RUN uv sync --frozen --no-dev --package reclaim-api --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home /app reclaim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations run as a Cloud Run job from this image (alembic upgrade head).
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/migrations apps/api/migrations
# Synthetic fixtures and the policy corpus (the local index and the seed read these).
COPY fixtures fixtures
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 FIXTURES_DIR=/app/fixtures APP_MODE=cloud API_HOST=0.0.0.0
USER reclaim
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn reclaim_api.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --proxy-headers"]
