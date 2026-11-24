# CLAUDE.md

## Commands (repo root)
- `uv sync --extra dev` (never `pip install -r requirements.txt`)
- `uv run python -m app.scripts.export_openapi --check` — committed openapi.json must be current
- `uv run ruff check .` · `uv run pyright` · `uv run pytest -q -n auto` (needs Postgres 16; CI sets REQUIRE_DB=1)

## Architecture
`app/` FastAPI · `alembic/` schema (Alembic only, never create_all) · `tests/` pytest + Postgres · `web/` React console (see web/CLAUDE.md). Read CONTEXT.md before engine changes.

## Workflow
@web/AGENTS.md
