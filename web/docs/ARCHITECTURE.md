# perf-lab-web Architecture

## Overview

perf-lab-web is a React 19 + TypeScript SPA that acts as a control console for
the [perf-lab-api](https://github.com/Nalakram/perf-lab-api) training engine.

The UI expresses the backend's control loop directly:

```
planning      (block/session orchestration)
simulate-dose (preview)
log-workout   (state mutation)
next-session  (controller output)
```

There is intentionally no abstraction layer between the UI concepts and the
backend domain objects. What you see in the UI is what the engine calls it.

---

## Technology Stack

| Layer | Library | Version |
|---|---|---|
| Framework | React | 19 |
| Language | TypeScript | ~5.9 |
| Build | Vite (rolldown-vite) | 7 |
| Styling | Tailwind CSS | 4 |
| UI primitives | shadcn/ui + Radix UI | — |
| Animation | Framer Motion | 12 |
| HTTP | Native fetch (typed wrapper) | — |
| Auth state | React Context + sessionStorage | — |

---

## Directory Structure

```
src/
├── main.tsx                   # React DOM entry — mounts AuthProvider + App
├── App.tsx                    # Top-level layout: header, tabs, onboarding gate
├── types.ts                   # Central TypeScript types (mirrors backend DTOs)
├── trainingGoals.ts           # TrainingGoal enum — must stay in sync with backend
│
├── api/
│   └── perfLabClient.ts       # Typed fetch wrapper — all HTTP calls live here
│
├── auth/
│   ├── AuthContext.tsx        # AuthProvider implementation
│   ├── perfLabAuthContext.ts  # AuthContextValue type + createContext
│   ├── useAuth.ts             # useContext(AuthContext) hook
│   ├── tokenStorage.ts        # sessionStorage read/write helpers
│   └── sessionBridge.ts      # Global 401 handler (singleton callback)
│
└── components/
    ├── AuthStrip.tsx          # Login / register form
    ├── DigitalTwinPanel.tsx   # Main stateful orchestrator for the twin loop
    ├── EngineExplainer.tsx    # Static explainer text
    ├── HeroFlowColumn.tsx     # Legacy field-test / VO₂ calculator (non-v1)
    ├── OnboardingForm.tsx     # Post-registration profile setup form
    ├── PlanningPanel.tsx      # Block/session planning surface (v1/planning/*)
    ├── PageSection.tsx        # Reusable section wrapper
    └── twin/
        ├── LogWorkoutForm.tsx     # Workout input form
        ├── NextSessionCard.tsx    # Prescription display
        ├── StateSnapshot.tsx      # S(t) visualization
        ├── TwinConsoleHeader.tsx  # Goal selector + Refresh u(t) button
        ├── TwinSummaryStrip.tsx   # 3-card readiness / habit / next-session strip
        ├── PatternPreviewDemo.tsx # UI-only pattern preview (no API)
        ├── stateUtils.ts          # Pure helpers: readinessScore, toApiWorkoutLog, etc.
        └── widgets.tsx            # FatigueBar, DosePanel, SkillPanel
```

---

## No Router — Intentional

The app uses a **tab switcher**, not a URL router. There is no `react-router-dom`
dependency and no URL-based navigation.

```
App.tsx
└── mainTab: "field" | "twin" | "planning"
    ├── "field"    → HeroFlowColumn (legacy VO₂ / field test)
    ├── "twin"     → DigitalTwinPanel
    └── "planning" → PlanningPanel
```

**Why:** The project is still in a phase where the product shape is evolving.
Adding routing now would require committing to URL semantics before the page
structure is stable.

---

## Auth Model

Authentication uses **JWT Bearer tokens** stored in `sessionStorage`.

```
User registers / logs in
    ↓
POST /auth/token → { access_token }
    ↓
setStoredToken(token)   ← sessionStorage write
setToken(token)         ← React state write
    ↓
Subsequent API calls include Authorization: Bearer {token}
```

### Tab-scoped sessions

`sessionStorage` clears when the browser tab is closed. This is intentional —
the app does not persist auth across sessions. If a user opens a new tab, they
will need to log in again.

### 401 handling

`sessionBridge.ts` holds a singleton callback reference. `AuthContext` registers
a handler via `setUnauthorizedHandler()` that clears storage and resets state.
The API client calls `notifyUnauthorized()` on any 401 response.

This indirection avoids circular imports between the auth context and the API
client.

### Post-registration onboarding gate

After `register()` succeeds, `AuthContext` sets `onboardingPending = true`.
`App.tsx` renders `OnboardingForm` instead of the main panel until
`completeOnboarding()` is called. This is best-effort — even if `POST /v1/onboard`
fails, the gate clears so the user is never stuck.

---

## State Management

The app uses **React Context for auth state** and **local `useState` for
everything else**. There is no global state library (no Redux, no Zustand).

### AuthContext

Manages: `token`, `user`, `email`, `isAuthenticated`, `isLoading`,
`onboardingPending`.

Exposed via `useAuth()` hook. All components that need auth data call this hook.

### DigitalTwinPanel (local state)

`DigitalTwinPanel` is the single stateful parent for the training loop. It owns:

```typescript
dtGoal: string                       // selected training goal
dtLog: WorkoutLog                    // current workout form state
dtState: UnifiedStateVector | null   // latest athlete state from API
dtRx: WorkoutPrescription | null     // latest prescription from API
dtDose: StressDose | null            // latest simulated dose
todaySession: PlannedSessionRead|null // today planning slot context
dtLoading: boolean                   // log-workout in flight
dtRxLoading: boolean                 // next-session in flight
dtError: ApiError | null             // last API error
```

All child components (`NextSessionCard`, `StateSnapshot`, `LogWorkoutForm`, etc.)
are **presentational** — they receive data and callbacks as props; they do not
call the API themselves.

---

## API Layer

All HTTP calls go through `src/api/perfLabClient.ts`.

```typescript
// Example: all API functions follow this pattern
export async function getNextSession(goal: string, token: string): Promise<WorkoutPrescription>
export async function logWorkout(log: WorkoutLog, token: string): Promise<UnifiedStateVector>
export async function simulateDose(log: WorkoutLog): Promise<StressDose>
export async function onboard(request: OnboardRequest): Promise<OnboardResponse>
export async function createPlanningBlock(body: BlockCreateRequest, token: string): Promise<BlockRead>
export async function listPlanningBlocks(token: string): Promise<BlockRead[]>
export async function listPlannedSessions(token: string, params?): Promise<PlannedSessionRead[]>
export async function getTodayPlannedSession(goal: string, token: string): Promise<TodaySessionResponse>
export async function computeMetrics(req: ComputeMetricsRequest): Promise<MetricsResponse>
```

> The field-test endpoint (`computeMetrics` → `POST /compute-metrics`) hits the
> backend **legacy router, which has no `/v1` prefix**, so it uses `API_ROOT`
> directly. `HeroFlowColumn` calls it through this client like everything else.

**Key behaviors:**
- All functions throw an `ApiError` (typed `{ message, status, details }`) on
  non-2xx responses
- The `handleResponse()` helper extracts the `detail` field from FastAPI error
  bodies automatically
- Auth-required calls pass `sessionOn401: true` to trigger `notifyUnauthorized()`
  on 401
- `VITE_API_BASE_URL` is read once at module load; if absent, every call throws
  immediately with a clear message

---

## Data Flow: Control Loop

The standard training loop in `DigitalTwinPanel`:

```
1. Mount / goal change
   → GET /v1/next-session?goal={goal}
   → GET /v1/planning/today?goal={goal}
   → setDtRx(prescription), setTodaySession(session|null)

2. User fills LogWorkoutForm + clicks "Simulate D(t)"
   → POST /v1/simulate-dose
   → setDtDose(dose)              [no state mutation]

3. User clicks "Log & update S(t)"
   → POST /v1/log-workout
     (includes planned_session_id when today slot exists)
   → setDtState(newState)
   → GET /v1/next-session         [auto-refresh]
   → GET /v1/planning/today       [auto-refresh]
   → setDtRx(updatedPrescription), setTodaySession(updated)

4. User clicks "Refresh u(t)"
   → GET /v1/next-session
   → setDtRx(prescription)
```

This maps to the backend planning + twin design:
`planning/*` (orchestration), `simulate-dose` (pure),
`log-workout` (mutating), `next-session` (controller output).

### Field Test → Twin handoff

The Field Test and Digital Twin live in sibling tabs that share no state, so the
"Send to Twin" continuity is wired through `App.tsx`:

```
1. HeroFlowColumn computes metrics → user clicks "Send to Twin →"
   → builds a Partial<WorkoutLog> (Running, 1.5-mi distance, pace-derived duration)
   → onSendToTwin(handoff)  [App lifts it to state + switches to the Twin tab]

2. DigitalTwinPanel mounts with the handoff prop
   → useEffect merges handoff.log into dtLog once, shows a banner
   → onHandoffConsumed()  [App clears the handoff]

3. User reviews the prefilled form → "Log & update S(t)" runs the normal loop.
```

---

## Readiness Score Formula

`stateUtils.readinessScore()` computes the displayed readiness number:

```
readiness = 100 - (0.55 × mean_fatigue) - (0.45 × max_tissue_stress)

where:
  mean_fatigue = (cns + muscular + metabolic + structural + tendon + grip) / 6
  max_tissue_stress = max(shoulder, elbow, wrist, lumbar, hip, knee, ankle, finger)

result is clamped to [0, 100] and rounded to nearest integer
```

This uses the decomposed `fatigue_f` and `tissue_t` vectors, not the legacy
`f_met_systemic` / `f_nm_peripheral` scalars that `StateSnapshot` displays.

---

## Deployment

Production is a self-hosted **EC2** docker-compose stack — this frontend is built into
the backend's `backend-with-frontend` Docker image (embeds the SPA at `/static`,
same-origin), not deployed as a standalone static site. See
[`../../docs/DEPLOY.md`](../../docs/DEPLOY.md) for the full runbook. (Netlify and
Railway are no longer used by this project.)
