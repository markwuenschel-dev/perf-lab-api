# Component Guide

This document covers every non-trivial component: what it does, what props it
expects, what data it renders, and anything non-obvious about its behavior.

For the overall component tree and data flow see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Top-Level Components

### `App.tsx`

The root layout. Renders the header, tab switcher, and either the onboarding
gate or the main content.

**Onboarding gate:**
```tsx
const { isAuthenticated, onboardingPending } = useAuth();
if (isAuthenticated && onboardingPending) {
  return <OnboardingForm />;
}
```
This fires after `register()` succeeds. Once `completeOnboarding()` resolves
(success or failure), `onboardingPending` is cleared and the main app renders.

**Tab model:**
- `"field"` → `HeroFlowColumn` (VO₂ field test via `computeMetrics`)
- `"twin"` → `DigitalTwinPanel` (live training engine)
- `"planning"` → `PlanningPanel` (block/session planning surface)

**Field Test → Twin handoff:** `App` holds `fieldTestHandoff` state. `HeroFlowColumn`
calls `onSendToTwin(handoff)`, which stores it and switches to the Twin tab;
`DigitalTwinPanel` receives `handoff` + `onHandoffConsumed` to prefill its log form
once. See ARCHITECTURE.md → Data Flow.

---

### `DigitalTwinPanel.tsx`

The single stateful parent for the entire training loop. Owns all API calls
and all data state for the twin UI.

**Does not call the API directly from JSX** — all API calls happen in async
handlers defined in this component and passed to children as callbacks.

**Key state:**

| State var | Type | Purpose |
|---|---|---|
| `dtGoal` | `TrainingGoalValue` | Selected training goal |
| `dtLog` | `WorkoutLog` | Workout form current values |
| `handoffSummary` | `string \| null` | Banner text after a Field Test handoff |
| `dtState` | `UnifiedStateVector \| null` | Latest athlete state |
| `dtRx` | `WorkoutPrescription \| null` | Latest prescription |
| `dtDose` | `StressDose \| null` | Latest simulated dose |
| `todaySession` | `PlannedSessionRead \| null` | Today planning slot context |
| `dtLoading` | `boolean` | `log-workout` in flight |
| `dtRxLoading` | `boolean` | `next-session` in flight |
| `dtError` | `ApiError \| null` | Last error |

**Auto-refresh behavior:** On mount (when `signedIn`), on goal change, and
after any `logWorkout` call, the panel automatically fetches both:
- `GET /v1/next-session`
- `GET /v1/planning/today`

There is a `prevModalityRef` that tracks modality changes to reset
`dominant_movement_pattern` when the user switches workout type.

---

### `OnboardingForm.tsx`

Post-registration profile setup form. Displayed via the `onboardingPending`
gate in `App.tsx`.

**Behavior:**
- All fields except email are optional
- "Start Training" calls `completeOnboarding(formState)` with the filled values
- "Skip for now" calls `completeOnboarding({ experience_level: 'intermediate' })`
- `completeOnboarding()` is best-effort — it clears `onboardingPending` even
  if `POST /v1/onboard` fails, so the user never gets stuck on this screen

**Fields collected:**

| Field | Maps to | Notes |
|---|---|---|
| Experience level | `experience_level` | Drives 4-tier baseline seeding |
| Years training | `experience_years` | Informational |
| Squat 1RM (kg) | `squat_1rm_kg` | Overrides `c_nm_force` calculation |
| Deadlift 1RM (kg) | `deadlift_1rm_kg` | Stored in profile |
| Bench 1RM (kg) | `bench_1rm_kg` | Stored in profile |
| Bodyweight (kg) | `bodyweight_kg` | Stored in profile |
| Days per week | `available_days_per_week` | Slider 1–7 |
| Primary goal | `goal` | Matches `TRAINING_GOALS` |
| Equipment checklist | `equipment[]` | Used by equipment-aware prescription |

---

### `AuthStrip.tsx`

Login / registration form. Displayed in the app header.

Calls `login()` or `register()` from `useAuth()`. After `register()` succeeds,
`AuthContext` sets `onboardingPending = true` and `App.tsx` shows the
onboarding form.

---

## Twin Components (`src/components/twin/`)

### `TwinConsoleHeader.tsx`

**Props:** `dtGoal`, `onGoalChange`, `onRefreshRx`, `token`

The header bar for the Digital Twin panel. Contains:
- Goal selector (`Select` from shadcn/ui, values from `trainingGoals.ts`)
- "Refresh u(t)" button — calls `onRefreshRx` (triggers `GET /v1/next-session`)

The button is disabled when `token` is null (user not signed in).

---

### `TwinSummaryStrip.tsx`

**Props:** `readiness: string`, `dtState: UnifiedStateVector | null`, `dtRx: WorkoutPrescription | null`

Three-card summary bar displayed below the header:

| Card | Color | Data source | Display |
|---|---|---|---|
| READINESS | neon-cyan | `readinessScore(dtState)` from `stateUtils.ts` | 0–100 integer |
| HABIT STRENGTH | neon-magenta | `dtState.habit_strength * 100` | Percentage |
| NEXT SESSION | neon-violet | `dtRx.duration_min` + `dtRx.type` | Duration + type |

Shows `—` for each card when the corresponding data is null.

---

### `NextSessionCard.tsx`

**Props:** `token: string | null`, `dtRxLoading: boolean`, `dtRx: WorkoutPrescription | null`, `todaySession: PlannedSessionRead | null`

Displays the current prescription.

**Render states:**
1. No token → "Sign in to load your personalized next session"
2. Loading → animated skeleton
3. `dtRx` available → full prescription display
4. Neither → "Log one workout to generate your first prescription"

**What it renders when prescription is available:**
- `model_version` badge (dim, next to duration badge) — identifies engine version
- Session type heading + `focus` subheading in neon-cyan
- Rationale in italic quotes
- Planning context panel when `todaySession` exists (date/category/modality + deload/benchmark badges)
- **Exercise list** (`exercises[]`) — only rendered if non-empty. Shows name,
  sets×reps, and load note per exercise
- **"Why this session?" details** (expandable `<details>`) containing:
  - `why.state_drivers` — comma-separated
  - `why.goal_alignment` — plain text
  - `why.constraints_applied` rendered as grouped chips for:
    - `weak_point:*`
    - `equipment:*`
    - `block:deload`, `block:benchmark`
  - `why.warnings` — amber warning text

**Non-obvious:** The `exercises` list comes from the backend prescriber. It will
be empty until the prescriber's exercise selection step is more fully wired.
The component safely handles an empty array by not rendering the list section.

---

### `StateSnapshot.tsx`

**Props:** `dtState: UnifiedStateVector | null`

Displays the current athlete state `S(t)`.

Shows "State will appear after your first logged session" when `dtState` is null.

**Three expandable sections when state is available:**

1. **Capacities** — 2×2 grid of legacy scalar mirrors:
   - `c_met_aerobic`, `c_nm_force`, `c_struct`, `b_met_anaerobic`

2. **Habit, signal & skill**:
   - `habit_strength` as percentage
   - `s_struct_signal` value
   - `SkillPanel` widget (from `widgets.tsx`)

3. **Fatigues (0–100)** — four `FatigueBar` widgets:
   - Systemic (`f_met_systemic`)
   - NM peripheral (`f_nm_peripheral`)
   - NM central (`f_nm_central`)
   - Structural (`f_struct_damage`)

**Non-obvious:** This component uses the **legacy scalar mirrors**, not the
decomposed `fatigue_f` / `capacity_x` vectors. The vectors are available in
`dtState` but are not currently visualized here. The `readinessScore()` in
`stateUtils.ts` uses the decomposed `fatigue_f` and `tissue_t` vectors.

**Model version badge:** A dim `engine {model_version}` label renders in the
bottom-right corner of the card when `model_version` is present.

---

### `LogWorkoutForm.tsx`

**Props:** `dtLog`, `updateDtLog`, `todaySession`, `benchmarkKey`, `benchmarkValue`, `onBenchmarkKeyChange`, `onBenchmarkValueChange`, `signedIn`, `token`, `dtLoading`, `dtDose`, `onSubmit`, `onSimulate`, `onCrash`

The workout logging form. All state lives in `DigitalTwinPanel` — this
component is purely presentational.

**Fields:**
- Modality (Select — "Running", "Strength", "Hypertrophy", "Power", "Mixed")
- Duration (minutes)
- Session RPE (1–10)
- Avg RIR (optional)
- Sleep quality (1–10)
- Life stress inverse (1–10, where 10 = no stress)
- Distance (meters, shown for Running)
- Total volume load
- Movement pattern (Select — squat, hinge, run, push, pull, etc.)
- Novelty (0.1–3.0, where >1 = novel / high coordination demand)
- Estimated sets
- Benchmark payload controls (only when `todaySession.is_benchmark` is true)

**Three actions:**
- "Simulate D(t)" → `onSimulate` (calls `POST /v1/simulate-dose`, no state change)
- "Log & update S(t)" → `onSubmit` (calls `POST /v1/log-workout`, advances state; includes `planned_session_id` when available)
- "Crash S(t)" → `onCrash` (development utility; logs a hard session to
  stress-test the engine)

**DosePanel widget** renders inline below the form when `dtDose` is not null.

---

### `widgets.tsx`

Three pure presentational widgets:

#### `FatigueBar`
**Props:** `label: string`, `value: number | undefined`

Animated progress bar for a 0–100 fatigue channel. Uses Framer Motion for the
fill animation on mount. Clamps value to [0, 100].

#### `DosePanel`
**Props:** `dose: StressDose | null`

Returns null when `dose` is null. Otherwise renders a 2-column grid of dose
channels:
- Metabolic (`d_met_systemic`)
- NM Peripheral (`d_nm_peripheral`)
- NM Central (`d_nm_central`)
- Structural Damage (`d_struct_damage`)
- Structural Signal (`d_struct_signal`) — full-width row, neon-cyan color

#### `SkillPanel`
**Props:** `state: UnifiedStateVector | null`

Returns null if `skill_state` is empty. Renders a labeled progress bar per
skill key. Values are 0–1, displayed as percentages.

---

### `TwinConsoleHeader.tsx`

See the table above. Purely presentational — raises callbacks for goal change
and prescription refresh.

---

### `PatternPreviewDemo.tsx`

UI-only demo component. Does not call any API. Used as a placeholder visual
in the twin panel. Safe to ignore for backend integration purposes.

---

## Utility: `stateUtils.ts`

Four pure helper functions (no side effects):

| Function | Purpose |
|---|---|
| `nowIso()` | Returns `new Date().toISOString()` — used to timestamp new workout logs |
| `toApiWorkoutLog(log)` | Strips undefined/zero optional fields before sending to API; normalizes `dominant_movement_pattern` to `"mixed"` when blank; forwards planning/benchmark fields |
| `readinessScore(state)` | Computes 0–100 readiness string from `fatigue_f` + `tissue_t` vectors (see ARCHITECTURE.md for formula) |
| `isApiError(value)` | Type guard for `ApiError` |
| `toApiError(err)` | Normalizes any caught value into an `ApiError` |

**Non-obvious:** `toApiWorkoutLog()` sets `novelty` to `1` (default) if
undefined. This is important — a missing `novelty` field could cause the backend
dose engine to use a different default.
### `PlanningPanel.tsx`

Planning surface for authenticated users.

**Behavior:**
- creates blocks via `POST /v1/planning/blocks`
- lists blocks via `GET /v1/planning/blocks`
- lists sessions (windowed) via `GET /v1/planning/sessions`
- updates sessions (`completed` / `skipped` / `rescheduled`) via `PATCH /v1/planning/sessions/{id}`

**Sections rendered:**
- block create form (goal, start date, duration, sessions/week, deload cadence, benchmark cadence)
- block summary cards
- MVP calendar list with action buttons

---
