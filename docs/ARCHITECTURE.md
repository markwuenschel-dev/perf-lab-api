# perf-lab-web Architecture

## Overview

`perf-lab-web` is a React 19 + TypeScript SPA that acts as a control console for the `perf-lab-api` training engine.

The frontend intentionally mirrors backend domain objects rather than hiding them behind generic UI labels.

Core backend concepts surfaced in the UI:

```text
D(t) = stress dose
S(t) = modeled athlete state
u(t) = next prescribed session
MesocycleBlock = training block
PlannedSession = scheduled slot
WorkoutLog = completed event
```

## Technology Stack

| Layer | Library / Tool |
|---|---|
| Framework | React 19 |
| Language | TypeScript ~5.9 |
| Build | Vite via `rolldown-vite` |
| Styling | Tailwind CSS 4 |
| UI primitives | shadcn / Radix-style components |
| Animation | Framer Motion |
| Icons | lucide-react |
| Font | Geist Variable |
| HTTP | native fetch wrapper |
| Auth state | React Context + sessionStorage |

`@tanstack/react-query` is installed but the uploaded client code still uses direct fetch helpers rather than React Query hooks.

## Directory Structure

Current source shape represented by uploaded files:

```text
src/
├── main.tsx
├── App.tsx
├── types.ts
├── trainingGoals.ts
│
├── api/
│   └── perfLabClient.ts
│
├── auth/
│   ├── AuthContext.tsx
│   ├── perfLabAuthContext.ts
│   ├── sessionBridge.ts
│   ├── tokenStorage.ts
│   └── useAuth.ts
│
└── components/
    ├── AuthStrip.tsx
    ├── DigitalTwinPanel.tsx
    ├── EngineExplainer.tsx
    ├── HeroFlowColumn.tsx
    ├── OnboardingForm.tsx
    ├── PageSection.tsx
    ├── PlanningPanel.tsx
    └── twin/
        └── presentational twin components
```

The latest upload did not include the `src/components/twin/*` presentational components, but `DigitalTwinPanel` and the existing docs establish their expected role.

## App-Level Surfaces

The app has several major surfaces:

1. auth strip
2. onboarding gate
3. digital twin panel
4. planning panel
5. legacy field-test / hero flow
6. engine explainer / page sections

Earlier docs described the app as a simple field/twin tab switcher. The source now includes `PlanningPanel.tsx`, so documentation should treat planning as an active frontend surface.

## Auth Model

Authentication uses JWT Bearer tokens stored in `sessionStorage`.

Flow:

```text
User registers or logs in
  -> POST /auth/token returns access_token
  -> token stored in sessionStorage
  -> authenticated API calls include Authorization: Bearer <token>
```

`AuthProvider` owns:

- `token`
- `user`
- `email`
- `isAuthenticated`
- `isLoading`
- `onboardingPending`
- `login`
- `register`
- `logout`
- `completeOnboarding`

`sessionBridge.ts` holds a singleton unauthorized callback. The API client calls `notifyUnauthorized()` on 401 when `sessionOn401` is enabled, and the auth context clears stored session state.

## Registration and Onboarding Gate

Registration flow:

```text
AuthStrip register button
  -> AuthContext.register()
  -> POST /auth/register
  -> POST /auth/token
  -> fetch /auth/me
  -> set onboardingPending = true
  -> App shows OnboardingForm
```

Onboarding flow:

```text
OnboardingForm submit
  -> completeOnboarding(formState)
  -> POST /v1/onboard
  -> onboardingPending cleared
  -> main app surfaces available
```

The backend identifies the athlete from the Bearer token. The current frontend `OnboardRequest` includes `email`, but the uploaded backend schema does not require it.

## API Layer

All HTTP calls go through:

```text
src/api/perfLabClient.ts
```

Base URL:

```ts
const RAW_BASE = import.meta.env.VITE_API_BASE_URL;
const API_ROOT = RAW_BASE ? RAW_BASE.replace(/\/$/, "") : "";
const API_V1_BASE = API_ROOT ? `${API_ROOT}/v1` : "";
```

Rules:

- do not include `/v1` in `VITE_API_BASE_URL`
- auth endpoints use `API_ROOT`
- modern training endpoints use `API_V1_BASE`
- API errors normalize to `ApiError`
- auth-required calls set `sessionOn401: true`

## API Client Functions

Current uploaded client includes:

Health:

```text
ping()
```

Auth:

```text
register(email, password)
login(email, password)
fetchMe(token)
```

Digital twin:

```text
getNextSession(goal, token)
logWorkout(log, token)
simulateDose(log)
onboard(request)
```

Planning:

```text
createPlanningBlock(body, token)
listPlanningBlocks(token)
updatePlanningBlock(blockId, body, token)
listPlannedSessions(token, params)
updatePlannedSession(sessionId, body, token)
getTodayPlannedSession(goal, token)
```

Missing from uploaded client but implemented in backend:

```text
benchmark definitions
benchmark observations
derived KPI recompute
dashboard KPIs
domain summary
readiness payload
```

## Type System

`src/types.ts` is the central frontend DTO mirror.

It includes:

- API error shape
- auth response types
- modality union
- capacity/fatigue/tissue vectors
- dose vector
- state vector
- prescription/explanation types
- workout log type
- stress dose type
- onboarding request/response
- planning block/session types

`src/trainingGoals.ts` contains the frontend training goal dropdown options and should match the backend `TrainingGoal` literal union.

## Digital Twin Control Loop

The digital twin panel orchestrates the core loop:

```text
mount / goal change
  -> GET /v1/next-session
  -> set prescription

simulate button
  -> POST /v1/simulate-dose
  -> set dose only

log button
  -> POST /v1/log-workout
  -> set state
  -> refresh GET /v1/next-session

refresh button
  -> GET /v1/next-session
```

The panel also interacts with today's planned session context through `getTodayPlannedSession()` in the uploaded client.

## Planning Flow

`PlanningPanel.tsx` owns the current planning MVP.

State owned:

- loading
- error
- blocks
- sessions
- goal
- start date
- duration weeks
- sessions per week
- deload cadence
- benchmark cadence

On mount, when authenticated, it loads:

- planning blocks
- planned sessions in a window from 7 days ago to 28 days ahead

Create block flow:

```text
form values
  -> createPlanningBlock()
  -> reload blocks and sessions
```

Session actions:

- complete -> patch status completed
- skip -> patch status skipped
- +1 day -> patch status rescheduled and scheduled_date +1

The planning UI displays:

- block cards
- session table
- status badges
- deload badges
- benchmark badges

## Legacy Field-Test Flow

`HeroFlowColumn.tsx` remains the legacy v0.1 surface.

It uses non-v1 endpoints:

```text
POST /compute-metrics
GET /program/run
GET /program/strength
```

This is separate from the digital twin engine loop.

## State Management

The app uses:

- React Context for auth
- local component state for workflow panels
- no Redux/Zustand in the uploaded source

State ownership rule:

- stateful panel owns API calls
- child/presentational components receive data and callbacks

Examples:

- `AuthContext` owns authentication
- `DigitalTwinPanel` owns digital twin data
- `PlanningPanel` owns block/session list state
- `AuthStrip` renders auth controls
- `OnboardingForm` renders setup form

## Error Handling

`handleResponse()` in the API client:

1. triggers unauthorized handler on 401 if enabled
2. checks JSON content type
3. extracts JSON or text error detail
4. throws `ApiError`
5. returns JSON for successful JSON responses

Components should normalize unknown caught values using helper logic such as `toApiError()` where present.

## Styling Architecture

Tailwind config defines neon tokens:

```text
neon-cyan
neon-magenta
neon-violet
```

Vite config defines alias:

```text
@ -> ./src
```

The UI uses dark zinc panels, neon accents, gradients, and Framer Motion transitions.

## Build and Dev Commands

From `package.json`:

```bash
npm run dev      # vite
npm run build    # tsc -b && vite build
npm run lint     # eslint .
npm run preview  # vite preview
```

## Backend Coupling Points

The frontend is tightly coupled to these backend decisions:

- auth routes are not under `/v1`
- modern training routes are under `/v1`
- `TrainingGoal` values must match exactly
- `Modality` values must match exactly
- `BlockGoal` differs from `TrainingGoal`
- planning endpoints require Bearer token
- `simulate-dose` does not require token in uploaded backend route
- `log-workout`, `next-session`, planning, benchmarks, and dashboard require auth

## Current Gaps

Backend implemented but frontend client/types not yet uploaded/confirmed for:

- benchmarks
- dashboard KPIs
- readiness endpoint
- weak-point management
- exercise library management

Frontend type caveats:

- `StressDose` lacks backend `adaptation_contribution`
- `WorkoutLog` lacks backend `exercises: ExerciseEntry[]`
- `OnboardRequest` includes `email`, which uploaded backend schema does not require

## Recommended Next Frontend Architecture Steps

1. Add benchmark/dashboard API client functions.
2. Add benchmark/dashboard DTOs to `src/types.ts`.
3. Add a dashboard/readiness panel.
4. Add benchmark observation form.
5. Add weak-point display/management surface.
6. Decide when to introduce URL routing.
7. Consider React Query for server-state caching if the app grows.
