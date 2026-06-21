---
status: accepted
date: 2026-06-20
---
# Use Alembic as the only schema manager

App startup checks database connectivity and the Alembic head but does **not** call
`create_all`. Auto-creating tables at startup hides migration problems and creates
schema drift; requiring `alembic upgrade head` keeps the schema's history explicit and
reviewable.

**Guardrail:** run `alembic upgrade head` before running against a real database; never
reintroduce `create_all` on the modern app path.
