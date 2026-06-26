# perf-lab-api/Dockerfile
# Single-service build: Node compiles the React frontend, then Python serves
# everything — static files at / and the FastAPI API at /v1, /auth, etc.

# ── Stage 1: build frontend ───────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /frontend

# Bake the backend URL into the JS bundle at build time.
# Set VITE_API_BASE_URL in Railway's Variables tab (it's the same service URL).
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY web/package*.json ./
RUN npm ci --prefer-offline
COPY web/ .
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# Frontend build output — served by FastAPI StaticFiles at /
COPY --from=frontend /frontend/dist ./static

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
