# Component Guide

This guide documents the non-trivial frontend components, their responsibilities, data flow, and behavior.

For backend contracts, see `API_GUIDE.md` and `SYNC_WITH_BACKEND.md`.

## Top-Level Components

### `App.tsx`

Root application layout.

Responsibilities:

- render the header / high-level app shell
- render `AuthStrip`
- route between the main product surfaces through app state or tabs
- show `OnboardingForm` when auth context says onboarding is pending

Important behavior:

```tsx
const { isAuthenticated, onboardingPending } = useAuth();
if (isAuthenticated && onboardingPending) {
  return <OnboardingForm />;
}
```

Planning is now an active surface in the uploaded source set because `PlanningPanel.tsx` exists.

### `AuthStrip.tsx`

Login / registration / logout control in the header.

Uses:

```ts
useAuth()
```

Reads:

- email
- user
- auth status
- loading state

Calls:

- `login(email, password)`
- `register(email, password)`
- `logout()`

Unauthenticated render:

- email input
- password input
- register button
- login button
- local error block

Authenticated render:

- avatar initial
- signed-in label
- email
- logout button

Non-obvious behavior:

Registration is not only account creation. The auth context logs the user in and sets `onboardingPending`, which causes the onboarding gate to render.

### `OnboardingForm.tsx`

Post-registration athlete setup form.

Responsibilities:

- collect profile and baseline information
- collect optional equipment
- collect primary goal
- call `completeOnboarding()`

Fields visible in uploaded source:

- experience level
- years training
- squat 1RM
- deadlift 1RM
- bench 1RM
- bodyweight
- days per week
- primary goal
- equipment options

Equipment options include common tags such as:

- barbell
- dumbbells
- kettlebells
- pullup bar
- machines / cables depending on source list

Submit behavior:

```text
Start Training -> completeOnboarding(formState)
```

Skip behavior:

```text
Skip for now -> completeOnboarding({ experience_level: "intermediate" })
```

Backend caveat:

The backend identifies the user from the Bearer token. The uploaded backend onboarding schema does not need email, even if frontend type currently includes it.

### `DigitalTwinPanel.tsx`

Stateful parent for the digital twin loop.

Responsibilities:

- selected training goal
- workout log form state
- latest `UnifiedStateVector`
- latest `WorkoutPrescription`
- latest `StressDose`
- loading states
- API errors
- next-session refresh
- dose simulation
- workout logging
- today's planned session context if used

Core API calls:

- `getNextSession()`
- `simulateDose()`
- `logWorkout()`
- `getTodayPlannedSession()`

Typical flow:

```text
mount / goal change -> getNextSession
simulate -> simulateDose
log -> logWorkout -> getNextSession
refresh -> getNextSession
```

Data ownership:

This component should own API calls. Twin child components should stay presentational.

### `PlanningPanel.tsx`

Stateful parent for block planning and session calendar MVP.

Requires auth.

State owned:

- `loading`
- `error`
- `blocks`
- `sessions`
- selected block goal
- start date
- duration weeks
- sessions per week
- deload cadence
- benchmark cadence

API calls:

- `createPlanningBlock()`
- `listPlanningBlocks()`
- `listPlannedSessions()`
- `updatePlannedSession()`

On mount:

```text
if token exists -> load blocks + session window
```

Date window:

- 7 days back
- 28 days forward

Create block payload:

```ts
{
  goal,
  start_date,
  duration_weeks,
  sessions_per_week,
  deload_every_n_weeks,
  benchmark_every_n_weeks,
}
```

Session actions:

- Complete -> `status: "completed"`
- Skip -> `status: "skipped"`
- +1 day -> `status: "rescheduled"`, `scheduled_date + 1`

Render sections:

1. Create Planning Block card
2. Blocks card list
3. Session Calendar table

Session flags:

- `is_deload` renders amber deload badge
- `is_benchmark` renders violet benchmark badge

### `HeroFlowColumn.tsx`

Legacy field-test / VO2 surface.

Uses legacy non-v1 endpoints:

- `POST /compute-metrics`
- `GET /program/run`
- `GET /program/strength`

This surface is separate from the digital twin API loop.

### `EngineExplainer.tsx`

Static explainer component.

Purpose:

- explain the digital twin model
- describe `D(t)`, `S(t)`, and `u(t)` concepts
- orient users around engine behavior

Keep this component aligned with backend architecture docs.

### `PageSection.tsx`

Reusable layout wrapper for page sections.

Purpose:

- consistent spacing
- consistent section layout
- likely used for top-level page surfaces

## Auth Modules

### `AuthContext.tsx`

Owns auth state and auth actions.

State:

- token
- user
- email
- loading
- onboarding pending

Important actions:

- `login`
- `register`
- `logout`
- `completeOnboarding`

Important behavior:

- reads token/email from `sessionStorage` on initialization
- registers unauthorized handler
- clears session on unauthorized
- after register, sets onboarding pending

### `perfLabAuthContext.ts`

Defines auth context shape and creates React context.

Keep types here minimal and stable.

### `useAuth.ts`

Convenience hook around `useContext(AuthContext)`.

Use this instead of importing context directly in components.

### `tokenStorage.ts`

Session storage helpers.

Responsibilities:

- get token
- set token
- get email
- set email
- clear stored session

The app uses `sessionStorage`, not `localStorage`.

### `sessionBridge.ts`

Global 401 callback bridge.

Why it exists:

The API client needs to notify auth state on 401 without importing React context and creating circular dependencies.

## Twin Components

The latest upload did not include the individual `src/components/twin/*` files, but existing architecture establishes the intended component roles.

### `TwinConsoleHeader.tsx`

Expected role:

- selected goal display/control
- refresh recommendation button
- call `onGoalChange`
- call `onRefreshRx`
- disable refresh when no token

Should source goal values from `TRAINING_GOALS`.

### `TwinSummaryStrip.tsx`

Expected role:

- compact summary cards for readiness, habit, and next session
- read from latest state and prescription
- show fallback dashes when null

### `NextSessionCard.tsx`

Expected role:

- display current prescription
- handle no-token, loading, no-prescription, and prescription states
- render duration and model version
- render type/focus/rationale
- render exercise list if present
- render explanation details from `why`
- render weak-point constraints as amber chips
- render warnings clearly

Backend response supports:

- `model_version`
- `exercises`
- `why.state_drivers`
- `why.goal_alignment`
- `why.constraints_applied`
- `why.source_alignment`
- `why.validation`
- `why.warnings`
- `why.score`
- `why.structured_template_name`

### `StateSnapshot.tsx`

Expected role:

- display current `S(t)`
- render null state message before first logged session
- show capacities, habit/signal/skill, and fatigue channels
- optionally display engine version

Important distinction:

- legacy scalar mirrors are display-friendly
- decomposed vectors are richer and should power readiness/tissue views

### `LogWorkoutForm.tsx`

Expected role:

- collect workout log inputs
- call simulation callback
- call logging callback
- display latest simulated dose

Backend-supported fields include:

- modality
- duration
- RPE
- RIR
- sleep quality
- life stress inverse
- distance
- volume load
- dominant movement pattern
- novelty
- estimated sets
- planned session ID
- benchmark flags/results
- future exercise entries

When exercise-level logging is added, this component should support `ExerciseEntry[]` or delegate to an exercise-entry subcomponent.

### `widgets.tsx`

Expected widgets:

- fatigue bars
- dose panel
- skill panel

Update `DosePanel` to include `dose_six` and eventually `adaptation_contribution` if those are user-facing.

### `stateUtils.ts`

Expected helpers:

- `nowIso()`
- `toApiWorkoutLog()`
- `readinessScore()`
- error normalization helpers

Keep helpers pure and side-effect free.

## API Client Usage by Component

| Component | Should call API? | Notes |
|---|---:|---|
| `AuthContext` | yes | auth/session/onboard |
| `AuthStrip` | indirectly | via auth context |
| `OnboardingForm` | indirectly | via `completeOnboarding` |
| `DigitalTwinPanel` | yes | owns twin loop |
| `PlanningPanel` | yes | owns planning loop |
| `NextSessionCard` | no | presentational |
| `StateSnapshot` | no | presentational |
| `LogWorkoutForm` | no | raises callbacks |
| `TwinSummaryStrip` | no | presentational |

## Data Flow Details

### Auth

```text
AuthStrip -> AuthContext -> perfLabClient -> backend
```

### Onboarding

```text
OnboardingForm -> AuthContext.completeOnboarding -> perfLabClient.onboard -> backend
```

### Twin

```text
DigitalTwinPanel -> perfLabClient -> backend
DigitalTwinPanel -> child props -> presentational twin components
```

### Planning

```text
PlanningPanel -> perfLabClient planning functions -> backend
PlanningPanel local state -> cards/table
```

## Rendering Guidelines

### Loading states

Use clear text or skeletons:

- `Saving...`
- animated skeleton for next session
- disabled buttons during in-flight operations

### Error states

Use rose/amber panels with the normalized `ApiError.message`.

### Auth-required states

Show explicit sign-in prompts rather than empty panels.

Examples:

- PlanningPanel: "Sign in to access block planning and session calendar."
- NextSessionCard: "Sign in to load your personalized next session."

### Empty states

Use domain-specific empty messages:

- no blocks yet
- no sessions in current window
- state will appear after first logged session
- no prescription loaded yet

## Component Update Checklist

When backend fields change:

1. update `src/types.ts`
2. update `perfLabClient.ts` if route/payload changes
3. update stateful parent component
4. update presentational render component
5. update docs
6. run typecheck/build/lint

When adding a new product surface:

1. put API calls in `perfLabClient.ts`
2. put DTOs in `types.ts`
3. keep state in one owning panel
4. keep child components presentational
5. use design-system card/badge/input patterns

## Current Component Gaps

Backend supports more than the uploaded frontend currently exposes:

- benchmark definition list
- benchmark observation form
- dashboard KPI panel
- readiness flags panel
- weak-point management surface
- exercise-entry workout logging
- exercise-library management

These are natural future components.

Suggested future component names:

```text
BenchmarkPanel.tsx
BenchmarkObservationForm.tsx
DashboardPanel.tsx
ReadinessPanel.tsx
WeakPointPanel.tsx
ExerciseEntryEditor.tsx
```
