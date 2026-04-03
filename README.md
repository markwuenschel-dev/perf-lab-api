# Performance Lab API

A unified, modality-agnostic engine for estimating an athlete’s internal state and generating algorithmic training prescriptions.

This service powers a tactical running “Performance Lab” (300m + 1.5-mile inputs → VO₂, categories, pace zones, and programs via a legacy app entrypoint) and a versioned v1 API with per-user JWT auth, persisted state, and digital-twin ingest/prescription flows. It is being extended into a general framework for endurance, strength, Olympic lifting, and hypertrophy.

The companion frontend lives in the separate **`perf-lab-web`** repository (React + Vite + TypeScript + Tailwind).

---

## Overview

Most apps model sports (10K time, 1RM squat). This project models the athlete.

Under the hood, the API maintains a latent Unified Athlete State Vector `S(t)` that captures capacities, batteries, fatigues, and short-lived adaptation signals across interacting systems (metabolic–cardiovascular, neuromuscular, CNS, structural–skeletal). Training sessions and field tests are converted into a stress dose vector `D(t)` that updates `S(t)` over multiple time scales. A prescriptive engine then uses the current state and a goal to choose the next workout.

The API exposes JSON endpoints; the complexity is internal.

---

## Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI (primary app version **0.2.0** in `app.main:app`)
- **Server:** Uvicorn
- **Config:** pydantic-settings (dotenv `.env` supported)
- **Database:** SQLAlchemy 2.x (async) with `asyncpg` for the running API
- **Migrations:** Alembic — `alembic.ini` and `alembic/env.py` are configured and import all models via `app.models`; **no revision scripts are checked in under `alembic/versions/` yet**, so `alembic upgrade head` is not usable until an initial migration is added
- **Auth:** JWT (`python-jose`), bcrypt hashing via passlib, OAuth2 password flow for `/auth/token`
- **Testing (tooling):** pytest, pytest-asyncio, pytest-cov
- **Lint / format / types:** ruff, black, isort, mypy

**Package manager:** `pip` via [`requirements.txt`](requirements.txt).

### Dependencies in `requirements.txt`

- **Core:** FastAPI, Uvicorn, pydantic-settings, python-dotenv
- **Database:** SQLAlchemy (asyncio), asyncpg, psycopg2-binary (for Alembic’s sync migration path), alembic, greenlet
- **Auth & uploads:** pyjwt, passlib, python-jose, python-multipart, email-validator, bcrypt
- **HTTP / JSON:** httpx, orjson
- **Declared for upcoming features (not imported under `app/` today):** LLM SDKs (anthropic, openai, google-generativeai, mistralai, tiktoken, tenacity), Celery + Redis, slowapi, structlog. Local development does **not** require Redis or provider API keys unless you start wiring these layers in.

---

## Current status

**Implemented (v0.2-style `app.main:app`):**

- FastAPI with `/ping`, versioned ingest/prescription under `/v1`, and auth under `/auth`
- JWT-protected workout logging and next-session prescription tied to the authenticated user
- Public stress-dose simulation (`POST /v1/simulate-dose`)
- Async Postgres persistence; ORM models for users, profiles, athlete state, exercises, mesocycles, weak points, workout logs (see **Project structure**)
- [`app/services/state_service.py`](app/services/state_service.py) for state initialization and workout processing

**Legacy entrypoint (`main:app`, version 0.1.0 in repo root [`main.py`](main.py)):**

- Still exposes running calculators and programs (`/compute-metrics`, `/program/run`, `/program/strength`) and may run `Base.metadata.create_all` on startup — useful for VO₂ demo flows and bootstrapping tables when no Alembic revisions exist yet

**In progress / planned:**

- Alembic revision files and seed/migration workflow as the source of truth for schema
- Additional v1 routers (blocks, weak points, onboarding) — see comments in [`app/main.py`](app/main.py)

---

## Two FastAPI entry points

| Entry | Command | Role |
|--------|---------|------|
| **Primary** | `uvicorn app.main:app --reload` | Auth, `/v1` digital twin (JWT on protected routes), `/ping` |
| **Legacy** | `uvicorn main:app --reload` | Running metrics + program endpoints; older app shell; may create DB tables on startup |

Use **both** locally if you need VO₂ `/compute-metrics` from the legacy app while testing v1 + auth on `app.main:app`.

---

## Project structure

```
app/
  api/
    v1/
      auth.py           # /auth/register, /auth/token, /auth/me (router prefix /auth)
      ingest.py         # /v1/simulate-dose (public), /v1/log-workout (JWT)
      prescribe.py      # /v1/next-session (JWT)
  core/
    config.py           # Settings + .env
    db.py               # async engine, session, get_db
    auth.py             # JWT, password hash, get_current_user
  logic/
    cross_talk.py
    dose_engine.py
    prescriber.py
    state_update.py
  models/
    __init__.py         # exports all ORM models for Alembic
    user.py             # User, AthleteProfile
    athlete_state.py
    exercise.py
    mesocycle.py
    weak_point.py
    workout_log.py
  schemas/
    state.py
    workouts.py
  services/
    state_service.py    # initialize state, process_new_workout
  scripts/
    seed_exercises.py   # optional exercise library seed (see below)
  main.py               # FastAPI 0.2.0 — preferred app

main.py                 # Legacy FastAPI 0.1.0 — running + program endpoints
alembic.ini
alembic/
  env.py
  README
requirements.txt
LICENSE
README.md
```

**Seed exercises (optional):** after the database schema exists, from the repo root:

```bash
python -m app.scripts.seed_exercises
```

---

## Requirements

- Python 3.11+
- Postgres for async SQLAlchemy (required for auth, state persistence, and JWT-protected v1 routes)
- `pip` and a virtual environment (recommended)

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

Create a `.env` file in the project root (see **Environment variables**). For anything beyond `simulate-dose`, ensure Postgres is running and `DATABASE_URL` points at your database.

---

## Running the API

**Primary (auth + v1):**

```bash
uvicorn app.main:app --reload
```

**Legacy (VO₂ + programs):**

```bash
uvicorn main:app --reload
```

Then open:

- Docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/ping (on `app.main:app` only)

**Production-style:**

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

CORS is currently permissive (`*`); tighten `allow_origins` for production.

---

## Endpoints

### `app.main:app`

**Public**

- `GET /ping` — Healthcheck
- `POST /v1/simulate-dose` — Body: `WorkoutLog` → `StressDose` (no JWT)

**Auth (no `/v1` prefix; OAuth2-compatible token URL)**

- `POST /auth/register` — JSON: `email`, `password` → creates user + empty `AthleteProfile`
- `POST /auth/token` — Form: `username` (email), `password` → `access_token` + `token_type: bearer`
- `GET /auth/me` — Header: `Authorization: Bearer <token>` → current user

**JWT required** (`Authorization: Bearer <token>`)

- `POST /v1/log-workout` — Body: `WorkoutLog` → updates state for current user → `UnifiedStateVector`
- `GET /v1/next-session?goal=...` — Query `goal`: `Strength` | `Hypertrophy` | `Power` | `General` (default `Strength`) → `WorkoutPrescription`

### `main:app` (legacy only)

- `POST /compute-metrics` — Running metrics from 300m + 1.5-mile inputs
- `GET /program/run` — 10-week running program
- `GET /program/strength` — Strength track outline

---

## Environment variables

Defined in [`app/core/config.py`](app/core/config.py) (`.env` supported):

| Variable | Purpose |
|----------|---------|
| `PROJECT_NAME` | API title (default: `Performance Lab API`) |
| `API_V1_STR` | v1 prefix (default: `/v1`) |
| `DATABASE_URL` | Async Postgres URL, e.g. `postgresql+asyncpg://postgres:postgres@localhost/perf_lab` |
| `DEBUG` | SQL echo etc. (default: `True`) |
| `SECRET_KEY` | JWT signing secret — **override in production** (generate e.g. `openssl rand -hex 32`) |
| `ALGORITHM` | JWT algorithm (default: `HS256`) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime (default: 7 days) |

---

## Database and migrations

- Async engine and sessions: [`app/core/db.py`](app/core/db.py)
- Alembic targets `Base.metadata` with all models loaded from [`app/models/__init__.py`](app/models/__init__.py)
- Until revision files exist under `alembic/versions/`, you can bootstrap schema using the legacy app’s startup path (`main:app`) or add an initial Alembic migration and use `alembic upgrade head`

---

## Tests

Pytest and plugins are listed in `requirements.txt`. There is no committed test suite layout yet; when tests exist:

```bash
pytest -q
pytest --cov=app
```

---

## Code quality

```bash
ruff check .
black .
isort .
mypy app
```

---

## Conceptual model (detailed)

1. **Input** — Athlete logs sessions/tests.
2. **Stress dose** — Map logs to `D(t)` (metabolic, neuromuscular, structural).
3. **State update** — `S(t-1)` → `S(t)` with multi-timescale decay/adaptation and cross-talk.
4. **(Future)** Data assimilation — e.g. EKF for drift and individualization.
5. **Prescription** — Next workout from goal and constraints.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Changelog

- **2026-04-03:** README aligned with JWT auth, dual entrypoints, Alembic layout, expanded models/services, and sibling `perf-lab-web` repo.
- **2025-11-21:** Earlier documentation pass (stack, structure, setup, env, endpoints).
