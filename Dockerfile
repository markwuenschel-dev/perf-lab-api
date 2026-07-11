# perf-lab-api/Dockerfile
# Python API image. Default target `backend` is API-only (split Railway deploy).
# Target `backend-with-frontend` embeds the Vite SPA at /static for monolith deploy.
#
# Split deploy (default):  docker build -t perf-lab-api .
# Monolith deploy:         docker build --target backend-with-frontend \
#                            --build-arg VITE_API_BASE_URL=https://api.example.com \
#                            -t perf-lab-api .

# ── Stage: build frontend (monolith only) ─────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /frontend

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN corepack enable && corepack prepare pnpm@10.12.1 --activate

ENV CI=true

COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm fetch

COPY web/ .
RUN pnpm install --frozen-lockfile --offline && pnpm run build

# ── Stage: Python backend ─────────────────────────────────────────────────────
FROM python:3.12-slim AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrate (must succeed), then seed the catalog (idempotent + fault-tolerant, so a `;` — a
# seed hiccup must never block boot), then serve.
CMD alembic upgrade head && python -m app.scripts.seed_catalog ; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2

# ── Target: monolith (API + embedded SPA) ─────────────────────────────────────
FROM backend AS backend-with-frontend
USER root
COPY --from=frontend /frontend/dist ./static
RUN chown -R appuser:appuser /app/static
USER appuser
