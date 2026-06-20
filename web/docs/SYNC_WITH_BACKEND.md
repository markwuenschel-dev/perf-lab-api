# Syncing with the Backend API

> **The frontend API types are now GENERATED from the backend's OpenAPI schema.**
> They are no longer hand-mirrored from `perf-lab-api/app/schemas/`. The old
> manual type-mapping tables and per-field ritual are retired — the workflow is
> now **regenerate + `tsc`**.

## How it works

```
perf-lab-api/app/schemas/*.py   (Pydantic — source of truth)
        │  FastAPI → OpenAPI document
        ▼
perf-lab-api/openapi.json        (committed at the monorepo root)
        │  npm run gen:types      (openapi-typescript)
        ▼
src/types.gen.ts                 (generated — never hand-edit)
        │  re-exported under friendly names + frontend-only types
        ▼
src/types.ts                     (thin adapter — what app code imports)
```

- **`src/types.gen.ts`** is the verbatim openapi-typescript output. Never edit it
  by hand; it's overwritten on every `npm run gen:types`.
- **`src/types.ts`** is a small hand-curated adapter: it re-exports the generated
  `components["schemas"][...]` shapes under the friendly names the app already
  uses (`UnifiedStateVector`, `WorkoutLog`, `Modality`, …) and defines the few
  **frontend-only** types that have no backend counterpart (`ApiError`,
  `FieldTestHandoff`). App code keeps importing from `@/types` / `../types` — only
  the underlying definitions changed.

## Regenerating after a backend change

The backend now lives in **this same repo** (monorepo); `gen:types` reads the
committed `openapi.json` at the repo root (`../openapi.json` from `web/`):

```bash
# 1. In perf-lab-api: regenerate + commit the contract
#    python -m app.scripts.export_openapi

# 2. In perf-lab-web: regenerate the TypeScript types
npm run gen:types        # rewrites src/types.gen.ts from ../openapi.json
npm run check:types      # tsc --noEmit — every break in consumers surfaces here
```

Fix whatever `tsc` flags, then commit `src/types.gen.ts` together with any
consumer fixes. `npm run build` (Netlify's command) runs `tsc -b` too, so a stale
or broken type can't ship.

> Want to regenerate from the live API instead of the sibling file? Point
> openapi-typescript at the URL:
> `npx openapi-typescript https://perf-lab-api.onrender.com/openapi.json -o src/types.gen.ts`
> (use the committed `openapi.json` for deterministic, offline builds).

## Naming notes (generated name ≠ friendly name)

The adapter in `src/types.ts` bridges a couple of mismatches:

| OpenAPI schema | Friendly alias (`src/types.ts`) | Route |
|---|---|---|
| `MetricsRequest` | `ComputeMetricsRequest` | `POST /compute-metrics` (legacy, no `/v1`) |

Inline Pydantic literals aren't standalone schemas, so the adapter derives them
from the parent type — e.g. `Modality = components["schemas"]["WorkoutLog"]["modality"]`.

## Request-body ergonomics

openapi-typescript marks properties that carry a server-side **default** as
*required* in the generated types (correct for responses — the field is always
present). When **building a request body**, the backend still fills those
defaults, so you may omit them. Construct the payload with the fields the form
captures and assert the contract type, e.g.:

```ts
return { timestamp, modality, duration_minutes, session_rpe, sleep_quality,
         life_stress_inverse } satisfies Partial<WorkoutLog> as WorkoutLog;
```

`satisfies Partial<WorkoutLog>` still type-checks the fields you *do* set; the
cast covers the server-defaulted remainder. (See `buildWorkoutLog` in
`LogWorkoutModal.tsx` and `completeOnboarding` in `AuthContext.tsx`.)

## Current wiring status

The phased plan for wiring screens to endpoints (and the open units/axes
questions) lives in **`perf-lab-api/docs/REDESIGN_ROADMAP.md`** — that is now the
single source of truth for what's wired vs. dormant vs. simulation-only. This doc
only covers the type-contract mechanics.
