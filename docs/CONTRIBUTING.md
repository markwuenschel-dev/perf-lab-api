# Contributing to perf-lab-web

## Prerequisites

- Node 18+
- npm 9+
- running instance of `perf-lab-api`
- PostgreSQL-backed backend with Alembic migrations applied

Check versions:

```bash
node --version
npm --version
```

## Local Setup

```bash
git clone https://github.com/Nalakram/perf-lab-web.git
cd perf-lab-web
npm install
```

## Environment Variables

Create `.env.local` in the frontend project root:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Do not include `/v1` in `VITE_API_BASE_URL`. The API client appends `/v1` for versioned routes.

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | backend base URL, no trailing slash | `http://localhost:8000` |

If this variable is missing, the client logs a warning and API calls throw a configuration error.

## Backend Setup

From `perf-lab-api`:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

The backend uses Alembic for schema management. Do not rely on `create_all`.

Current migration chain:

```text
a000_init
a001_benchmark_kpi
a002_planned_bench_cols
```

## Dev Server

```bash
npm run dev
```

Starts Vite.

## Build

```bash
npm run build
```

Runs:

```bash
tsc -b && vite build
```

## Type Checking

```bash
npx tsc --noEmit
```

Run this after touching:

- `src/types.ts`
- `src/api/perfLabClient.ts`
- API-consuming components
- training goals
- planning DTOs

## Linting

```bash
npm run lint
```

Uses ESLint with React hooks / refresh plugins.

## Preview Production Build

```bash
npm run preview
```

## Tech Stack

Current uploaded `package.json` shows:

- React 19
- TypeScript ~5.9
- Tailwind CSS 4
- Vite via `rolldown-vite` override
- Framer Motion 12
- Radix/shadcn stack
- Lucide React
- React Query installed
- Geist variable font

## API Client Rules

All HTTP calls should go through:

```text
src/api/perfLabClient.ts
```

Do not scatter `fetch()` calls through components.

The client owns:

- base URL handling
- `/v1` prefixing
- auth headers
- error normalization
- 401 session clearing

## Auth Rules

Auth routes are outside `/v1`:

```text
/auth/register
/auth/token
/auth/me
```

Modern training routes are under `/v1`.

JWT tokens are stored in `sessionStorage`, not `localStorage`. This gives tab-scoped sessions.

## Type Sync Rules

Frontend types are manual mirrors of backend Pydantic schemas.

When backend schemas change:

1. update `src/types.ts`
2. update `src/trainingGoals.ts` if enum/literal values changed
3. update `src/api/perfLabClient.ts` if routes or payloads changed
4. update relevant forms/components
5. run typecheck/build/lint

Pay special attention to:

- `WorkoutLog`
- `StressDose`
- `UnifiedStateVector`
- `WorkoutPrescription`
- planning DTOs
- onboarding DTOs
- benchmark/dashboard DTOs when added to frontend

## Planning UI Rules

`PlanningPanel.tsx` owns the current planning MVP:

- create block
- list blocks
- list sessions
- mark complete
- skip
- reschedule +1 day

Backend planning routes require auth.

Do not build separate planning API helpers outside the central client.

## Tailwind v4 / Design Tokens

Neon colors are defined in `tailwind.config.js`:

```js
neon: {
  cyan: "#00f5ff",
  magenta: "#ff00aa",
  violet: "#8b00ff",
}
```

Do not remove or rename these tokens without updating all classes:

- `text-neon-cyan`
- `text-neon-magenta`
- `text-neon-violet`
- `bg-neon-cyan`
- `border-neon-cyan`
- `shadow-neon-cyan`

## Path Alias

The project uses `@` for `src/`.

Configured in `vite.config.js`:

```ts
alias: {
  "@": path.resolve(__dirname, "./src"),
}
```

Do not remove this alias. shadcn-style imports depend on it.

## Component Style

Current pattern:

- stateful parent components own API calls
- presentational child components receive props and callbacks
- shared API types live in `src/types.ts`
- UI components live under `src/components/ui/`

Examples:

- `DigitalTwinPanel` owns the twin loop
- `PlanningPanel` owns planning calls
- `AuthContext` owns auth/session state
- `AuthStrip` renders login/register/logout controls
- `OnboardingForm` renders profile setup

## Error Handling

Use the `ApiError` shape from `src/types.ts`.

`handleResponse()` extracts FastAPI `detail` when available. Auth-required calls should pass `sessionOn401: true` so the auth bridge clears stale sessions.

## Commit Style

Use concise conventional commits:

```text
feat(scope): short description
fix(scope): short description
docs: short description
```

Examples:

```text
feat(planning): add block calendar MVP
fix(api): sync frontend workout log fields
docs: refresh backend architecture docs
```

## Pre-Commit Checklist

Before committing:

```bash
npx tsc --noEmit
npm run build
npm run lint
```

Also verify:

- `.env.local` does not get committed
- `VITE_API_BASE_URL` has no `/v1`
- new backend fields are reflected in `src/types.ts`
- new routes use `perfLabClient.ts`
- Tailwind neon classes still resolve
- auth-required calls pass token and handle 401

## Backend-Frontend Change Checklist

When backend changes:

- update frontend DTOs
- update API client
- update docs
- run typecheck/build
- manually test auth, onboarding, twin loop, and planning loop

When frontend changes:

- keep domain names aligned with backend concepts
- avoid UI-only naming that obscures `S(t)`, `D(t)`, `u(t)`, blocks, sessions, benchmarks, and weak points
