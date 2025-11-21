# Performance Lab API

A unified, modality-agnostic engine for estimating an athlete’s internal state and generating algorithmic training prescriptions.

This service currently powers a tactical running “Performance Lab” (300m + 1.5-mile inputs → VO₂, categories, pace zones, and 10-week plans) and is being extended into a general framework that can handle endurance, strength, Olympic lifting, and hypertrophy.

---

## Overview

Most apps model sports (10K time, 1RM squat). This project models the athlete.

Under the hood, the API maintains a latent Unified Athlete State Vector `S(t)` that captures capacities, batteries, fatigues, and short-lived adaptation signals across interacting systems (metabolic–cardiovascular, neuromuscular, CNS, structural–skeletal). Training sessions and field tests are converted into a stress dose vector `D(t)` that updates `S(t)` over multiple time scales. A prescriptive engine then uses the current state and a goal to choose the next workout.

The API exposes simple JSON endpoints; the complexity is internal.

---

## Stack

- Language: Python 3.11+
- Framework: FastAPI
- Server: Uvicorn
- Config: pydantic-settings (dotenv `.env` supported)
- Database: SQLAlchemy (async) with `asyncpg` Postgres driver
- Migrations: Alembic (planned; no config in repo yet) — TODO: add Alembic setup
- Testing: pytest, pytest-asyncio, pytest-cov
- Tooling: ruff, black, isort, mypy

Package manager: `pip` via `requirements.txt`

---

## Current Status

Implemented (v0):
- FastAPI backend running locally via Uvicorn
- Swagger/OpenAPI docs at `/docs`
- Endpoints (see Endpoints section)

In Progress / Planned:
- Modality-aware, versioned APIs and persistent athlete state `S(t)`
- Frontend v2 (React + Vite + TypeScript + Tailwind) talking to this API — project name `perf-lab-web` — TODO: link when public

---

## Project Structure

At a glance:

```
app/
  api/
    v1/
      ingest.py         # ingest/logging endpoints (simulate dose, log workout)
      prescribe.py      # prescription endpoint (next-session)
  core/
    config.py           # environment settings via pydantic-settings
    db.py               # async SQLAlchemy engine/session
  logic/
    cross_talk.py       # interactions between systems
    dose_engine.py      # D(t) calculation from WorkoutLog
    prescriber.py       # recommendation logic
    state_update.py     # S(t) update rules
  main.py               # FastAPI app with routers + /ping
  models/
    athlete_state.py    # ORM model for persisted S(t)
    user.py             # placeholder for user model
  schemas/
    state.py            # UnifiedStateVector and related models
    workouts.py         # WorkoutLog, StressDose

main.py                 # Legacy/demo app with additional endpoints
requirements.txt        # Python dependencies
LICENSE                 # MIT License
README.md               # This file
```

Notes:
- There are two FastAPI entry points:
  - Preferred: `app.main:app` (includes versioned routers at prefix from `API_V1_STR`, default `/v1`)
  - Legacy/demo: `main:app` (exposes additional running/strength endpoints and utilities). Keep for reference; may be removed or moved behind `/v1` later. TODO: consolidate.

---

## Requirements

- Python 3.11+
- Postgres database (for async SQLAlchemy). A running Postgres instance is required for endpoints that persist/load state.
- `pip` recommended; virtual environment strongly recommended

---

## Setup

```bash
git clone https://github.com/<your-user>/perf-lab-api.git
cd perf-lab-api

python -m venv .venv
# Windows PowerShell
. .\.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in the project root to override defaults (see Environment Variables below).

---

## Running the API

Development (hot reload):

```bash
uvicorn app.main:app --reload
```

Legacy/demo server (alternate entry, includes extra running endpoints):

```bash
uvicorn main:app --reload
```

Then open:
- Docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/ping

Production-ish example:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Notes:
- CORS is permissive (`*`) by default; restrict in production. TODO: set proper `allow_origins`.

---

## Endpoints

Routers under API v1 (default prefix `/v1`, configurable via `API_V1_STR`):

- POST `/v1/simulate-dose` — Accepts `WorkoutLog`, returns computed `StressDose`.
- POST `/v1/log-workout` — Accepts `WorkoutLog`, updates/persists state, returns `UnifiedStateVector`.
- GET  `/v1/next-session` — Returns `WorkoutPrescription` based on latest state and `goal` query param.

Utility:
- GET `/ping` — Healthcheck.

Legacy/demo endpoints (only when running `main:app`):
- POST `/compute-metrics` — Running metrics from 300m + 1.5-mile.
- GET  `/program/run` — 10-week running program.
- GET  `/program/strength` — Strength track outline.

---

## Environment Variables

Defined in `app/core/config.py` (dotenv `.env` supported):

- `PROJECT_NAME` (str) — default: "Performance Lab API"
- `API_V1_STR` (str) — default: `/v1`
- `DATABASE_URL` (str) — default: `postgresql+asyncpg://user:password@localhost/dbname`
  - Format for async Postgres with SQLAlchemy 2.x and `asyncpg` driver.
  - Example: `postgresql+asyncpg://postgres:postgres@localhost/perf_lab`
- `DEBUG` (bool) — default: `True` (enables SQL echo)

TODOs:
- Provide docker-compose and a sane default `DATABASE_URL` for local dev.
- Add Alembic migrations and seed data.

---

## Database & Migrations

- Async engine/session configured in `app/core/db.py`.
- Alembic is listed in requirements but migration setup is not present in the repo.
  - TODO: initialize Alembic, create `alembic.ini` and versioned migrations for `AthleteState`.

---

## Tests

Tooling present: `pytest`, `pytest-asyncio`, `pytest-cov`.

Run tests (once tests are added):

```bash
pytest -q
pytest --cov=app
```

TODO: add unit tests for dose engine, state update, and prescriber logic; and API tests for v1 routes.

---

## Code Quality

Recommended commands (no preconfigured scripts):

```bash
ruff check .
black .
isort .
mypy app
```

---

## Conceptual Model (Detailed)

At a high level, the engine does:

1. Input (logs/tests) — athlete logs sessions/tests.
2. Stress Dose Calculation — convert to `D(t)` with impacts on metabolic, neuromuscular (central/peripheral), and structural systems.
3. State Update — `S(t-1)` → `S(t)` via multi-timescale decay/adaptation and cross-talk.
4. (Future) Data Assimilation — EKF to correct drift and individualize parameters.
5. Prescription — choose next workout given goal and constraints.

---

## License

MIT — see LICENSE.

---

## Changelog

- 2025-11-21: Documentation refreshed. Added stack, structure, setup/run, env vars, endpoints, and testing sections; marked migration and deployment items as TODO.
