# Performance Lab API

A unified, modality-agnostic engine for estimating an athlete’s internal state and generating algorithmic training prescriptions.

This service currently powers a tactical running “Performance Lab” (300m + 1.5-mile inputs → VO₂, categories, pace zones, and 10-week plans) and is being extended into a general framework that can handle endurance, strength, Olympic lifting, and hypertrophy.

---

## High-Level Idea

Most apps model **sports** (10K time, 1RM squat). This project models the **athlete**.

Under the hood, the API maintains a latent **Unified Athlete State Vector** \(S(t)\) that captures:

- **Capacities** (slow-adapting “ceiling” variables)
- **Batteries** (finite, fast-recharging work capacities)
- **Fatigues** (fast-decaying negative state)
- **Signals** (short-lived adaptation triggers)

across four interacting systems:

- **Metabolic–Cardiovascular** (aerobic engine, anaerobic work capacity, systemic fatigue)
- **Neuromuscular (NM)** (force, power, central vs peripheral fatigue)
- **Central Nervous System (CNS)** (perception of effort, neural drive)
- **Structural–Skeletal** (muscle CSA, tendon stiffness, local damage, hypertrophy signal)

Training sessions and field tests are converted into a **stress dose vector** \(D(t)\) that updates \(S(t)\) over multiple time scales. A prescriptive engine then uses the current state and a goal (e.g., “maximize strength”, “peak 1.5-mile performance”, “maximize hypertrophy”) to choose the next workout type and parameters.

In practice, the API exposes simple JSON endpoints; the complexity is internal.

---

## Current Status

### Implemented (v0)

- FastAPI backend running locally via Uvicorn
- Swagger/OpenAPI docs at `/docs`
- Endpoints:
  - `GET /ping` – healthcheck
  - `POST /compute-metrics` – running metrics from 300m + 1.5-mile
  - `GET /program/run` – 10-week running program
  - `GET /program/strength` – 10-week strength track (base → build → power/recovery)
- Deployed static v1 front-end at:  
  `https://nalakram.github.io` (GitHub Pages)

### In Progress / Planned

- React + Vite + Tailwind front-end (`perf-lab-web`) that talks to this API
- Versioned, modality-aware endpoints:
  - `POST /api/v1/run/compute-metrics`
  - `GET  /api/v1/run/program`
  - `POST /api/v1/strength/compute-metrics`
  - `GET  /api/v1/strength/program`
- Persistent athlete state \(S(t)\) and basic closed-loop adaptation

---

## Architecture

### Components

- **Backend:** FastAPI (`perf-lab-api`)
  - Encodes the unified athlete model
  - Exposes JSON endpoints for metrics, programs, and (later) stateful prescriptions
- **Frontend v1:** Static HTML/JS (GitHub Pages)
  - Pure browser computations, no backend
  - Reference implementation of the running logic
- **Frontend v2:** React + Vite + TypeScript + Tailwind (`perf-lab-web`)
  - Calls this API for metrics and programs
  - Will eventually expose multi-modality dashboards

### Conceptual Model

At a high level, the engine does:

1. **Input (logs/tests)**  
   The athlete logs sessions and tests (e.g., 300m + 1.5-mile, 5×5 squats @ RIR 2, 10km run, etc.).

2. **Stress Dose Calculation**  
   Each session is converted into a **stress dose vector** \(D(t)\), with separate impacts for:
   - Metabolic systemic fatigue (`F_met_systemic`)
   - Anaerobic work battery (`B_met_anaerobic`)
   - Neuromuscular fatigue (central vs peripheral)
   - Structural damage (`F_struct_damage`)
   - Hypertrophy signal (`S_struct_signal`)
   …and, later, additional capacities/batteries.

3. **State Update**  
   The previous state \(S(t-1)\) is updated using:
   - Additive effects from \(D(t)\)
   - Multi-timescale decay/adaptation functions
   - Cross-talk rules (e.g., high metabolic fatigue reduces realized force, soreness penalizes quad-dominant tasks)

4. **(Future) Data Assimilation**  
   Periodic tests (e.g., updated F–V profile, 3-min all-out test, CMJ) will be used with an Extended Kalman Filter to correct model drift and individualize parameters.

5. **Prescription**  
   Given a goal (e.g., “maximize \(C_{met\_aerobic}\)” or “maximize \(C_{nm\_force}\)” under fatigue limits), the prescriptive engine picks the next workout type and parameters from a library of templates.

---

## Getting Started (Local Development)

### Prerequisites

- Python 3.11+
- `pip` or `uv` / `pipenv` / `poetry`
- (Optional) `uvicorn` installed globally for convenience

### Setup

```bash
git clone https://github.com/<your-user>/perf-lab-api.git
cd perf-lab-api

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
