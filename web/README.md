# Perf Lab Web

A modern React frontend for **Performance Lab** — a unified, modality-agnostic engine that models an athlete’s internal state (`S(t)`) and generates adaptive training prescriptions.

The app provides two complementary experiences:
- **Legacy tactical running flow** — 300 m + 1.5-mile field tests → VO₂max, running categories, pace zones, and sample programs.
- **Digital Twin / v1 flow** — full stateful interaction with the primary API (simulate stress dose, log workouts, view adaptive next-session recommendations).

The companion backend lives in the separate **`perf-lab-api`** repository.

---

## Stack

- **Framework:** React 19 + TypeScript
- **Build tool:** Vite (via rolldown-vite)
- **Styling:** Tailwind CSS 4 + PostCSS
- **State & Auth:** React Context + sessionStorage for JWT
- **HTTP:** Native fetch wrapped in a typed client

---

## Key Concepts & Backend Coupling

Performance Lab maintains a latent **Unified Athlete State Vector** `S(t)` (capacities, batteries, fatigues, adaptation signals). Workouts are turned into a stress-dose vector `D(t)` that updates the state across multiple timescales. The prescriber then uses current state + goal to recommend the next session.

The frontend interacts with **two different FastAPI entrypoints** from the backend repo:

| UI Panel                  | Backend Entrypoint          | Routes Used                                      | Auth Required? |
|---------------------------|-----------------------------|--------------------------------------------------|----------------|
| **Hero / Tactical Running** | Legacy (`main:app`)        | `POST /compute-metrics`, `GET /program/run`, `GET /program/strength` | No |
| **Digital Twin**          | Primary (`app.main:app`)   | `/ping`, `/v1/simulate-dose`, `/v1/log-workout`, `/v1/next-session`, auth routes | Yes for protected v1 routes |

> **Local development tip:** Run both `uvicorn main:app --reload` (legacy) **and** `uvicorn app.main:app --reload` (primary) if you want full functionality.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/<your-org>/perf-lab-web.git
cd perf-lab-web
npm install
2. Environment variables
Create .env in the project root:
envVITE_API_BASE_URL=http://localhost:8000

Use an absolute URL with no trailing slash.
The value is baked into production builds.

3. Run locally
Bashnpm run dev
Open http://localhost:5173 (or the port Vite reports).
4. Build for production
Bashnpm run build
npm run preview
The output lands in dist/. Production does not deploy this build standalone — see
Deployment below.

Authentication (Primary API only)

Public routes: /ping, POST /v1/simulate-dose
Protected routes: POST /v1/log-workout, GET /v1/next-session (and future v1 endpoints)

The app uses OAuth2 Password Flow + JWT:

Register / Login via the header strip
Token stored in sessionStorage (cleared on tab close)
Automatic 401 handling clears the session

Auth endpoints (no /v1 prefix):

POST /auth/register — JSON {email, password}
POST /auth/token — form data username (email) + password
GET /auth/me — Bearer token


Main Features
Tactical Running Column (HeroFlowColumn)

Enter 300 m and 1.5-mile field test times
Compute VO₂max, running economy estimates, pace zones, and training categories
View sample 10-week running program and strength track

Digital Twin Panel

Simulate Dose — preview what a workout would do to the athlete model (non-mutating)
Log Workout — record a real session → updates S(t) via process_new_workout
Next Session — adaptive prescription based on current state and chosen goal (Strength, Hypertrophy, Power, General, etc.)
Real-time state visualization and rationale display
First-run onboarding flow (baseline state auto-initialized)

The UI mirrors the backend control loop:
textSimulate D(t) → Log workout → Update S(t) → Get next u(t) → Repeat

Project Structure
textsrc/
├── main.tsx
├── App.tsx                 # Layout + grid of panels
├── App.css
├── index.css
├── types.ts                # Shared TypeScript definitions
├── api/
│   └── perfLabClient.ts    # Typed API helpers + error shaping
├── auth/
│   ├── AuthContext.tsx
│   ├── tokenStorage.ts
│   └── sessionBridge.ts
└── components/
    ├── HeroFlowColumn.tsx      # Legacy running calculators
    ├── DigitalTwinPanel.tsx    # v1 digital twin experience
    └── AuthStrip.tsx

Available Scripts
Bashnpm run dev       # Start dev server
npm run build     # Production build (tsc + Vite)
npm run preview   # Preview production build locally
npm run lint      # ESLint

Development Notes

CORS: The backend currently allows *. Tighten allow_origins in production.
Legacy vs Primary: Some legacy routes (/compute-metrics) are not mounted on app.main:app. Make sure the legacy server is running if you need the tactical column.
State Management: Currently lightweight (React Context). Future expansion may add TanStack Query or Zustand for richer caching of state history.
Testing: No test suite configured yet. Vitest + React Testing Library is a natural next step.


Roadmap Alignment
This frontend is kept in sync with the backend's Quickstart Flow and Roadmap:

Full support for the current v1 control loop (simulate-dose / log-workout / next-session)
Placeholder space for upcoming features: onboarding router, blocks/planned sessions, weak-point UI, richer exercise library, and coach/debug surfaces
Goal: evolve from “demo panels” into a complete athlete workflow (onboarding → daily session → history → rationale inspection)

See the backend QUICKSTART_FLOW.md, ROADMAP.md, and PRESCRIBER_LOGIC.md for deeper context on the engine.

Deployment

Production is a self-hosted **EC2** docker-compose stack — this frontend is built into
the backend's `backend-with-frontend` Docker image (embeds the SPA at /static,
same-origin), not deployed standalone. See `../docs/DEPLOY.md` for the full runbook.
(Netlify and Railway are no longer used by this project.)

For a standalone/local static build: set VITE_API_BASE_URL to your deployed API (must
expose both legacy and v1 routes, or adjust the UI accordingly), run npm run build, and
serve the dist/ folder as a static site.


License
No license file present yet (add one if publishing).

Acknowledgements
Built with React 19, Vite, TypeScript, and Tailwind CSS 4.
Backend powered by Performance Lab API (FastAPI + SQLAlchemy + custom athlete modeling engine).