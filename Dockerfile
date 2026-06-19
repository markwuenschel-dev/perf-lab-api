# perf-lab-api/Dockerfile
# Production image for the perf-lab-api FastAPI app.
# Targets Railway (which injects $PORT) but works on any container host.
FROM python:3.12-slim

# Don't buffer stdout/stderr (logs show up immediately) and don't write .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# OS build deps required to build/run asyncpg + psycopg against PostgreSQL.
# Clean the apt lists afterwards to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from the pyproject CORE dependencies only
# ([project].dependencies). The optional groups (dev/llm/tasks/observability)
# are intentionally excluded from the production image.
# Copy the project metadata + source first, then `pip install .` so the package
# (and its core runtime deps) are resolved from pyproject.toml.
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# Run as a non-root user for security.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Document the default port. Railway overrides this at runtime via $PORT.
EXPOSE 8000

# Shell-form CMD so ${PORT} expands at container start.
#   - `alembic upgrade head` applies pending DB migrations before the app boots,
#     so the schema is always current on deploy.
#   - `${PORT:-8000}` honors Railway's injected $PORT, falling back to 8000
#     for hosts that don't set it.
# No --reload in production; run multiple uvicorn workers instead.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
