# Contributing to perf-lab-web

## Prerequisites

- Node 18+ (`node --version`)
- npm 9+ (bundled with Node)
- A running instance of [perf-lab-api](https://github.com/Nalakram/perf-lab-api) (local or deployed)

---

## Local Setup

```bash
git clone https://github.com/Nalakram/perf-lab-web.git
cd perf-lab-web
npm install
```

---

## Environment Variables

Create a `.env.local` file in the project root:

```env
VITE_API_BASE_URL=http://localhost:8000
```

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | Base URL of the perf-lab-api backend, **no trailing slash** | `http://localhost:8000` |

If this variable is missing or empty, the API client will log a console warning
and all API calls will silently fail. The app will still render, but auth and
all data operations will not work.

For the deployed backend, use the production URL instead.

---

## Dev Server

```bash
npm run dev
```

Starts Vite's dev server with HMR. Default port is `5173`.

---

## Build

```bash
npm run build
```

Runs `tsc -b` (full TypeScript build) followed by `vite build`.
Output goes to `dist/`.

---

## Type Checking

```bash
npx tsc --noEmit
```

This is the fastest way to catch type errors without building. Run this before
committing when you change `src/types.ts` or the API client.

---

## Linting

```bash
npm run lint
```

Uses ESLint with `eslint-plugin-react-hooks` and `eslint-plugin-react-refresh`.

---

## Pointing at a Different Backend

To develop against a deployed backend:

```env
VITE_API_BASE_URL=https://your-api.example.com
```

To develop against a local backend with a non-default port:

```env
VITE_API_BASE_URL=http://localhost:8080
```

The `/v1` path prefix is appended automatically by the API client.
Do **not** include `/v1` in `VITE_API_BASE_URL`.

---

## Backend Prerequisites

The API requires PostgreSQL. The easiest local setup:

```bash
# From perf-lab-api directory:
alembic upgrade head   # creates all tables
uvicorn app.main:app --reload
```

The frontend's first call is `POST /auth/register` → `POST /auth/token` → `GET /auth/me`.
If the backend is not running, auth will silently time out.

---

## Tailwind v4 — Do Not Modify the @theme Block

The project uses Tailwind CSS v4 (CSS-first; there is no `tailwind.config.js`).
The custom neon color tokens (`neon-cyan`, `neon-magenta`, `neon-violet`) are
defined in `src/index.css` inside the `@theme inline` block as `--color-neon-*`.
These are used extensively across all twin components.

If you are upgrading Tailwind or touching the config, verify that these
class names still resolve:

- `text-neon-cyan`
- `text-neon-magenta`
- `text-neon-violet`
- `bg-neon-cyan`
- `shadow-neon-cyan`

Breaking these will corrupt the UI without TypeScript errors, since Tailwind
classes are strings.

---

## Path Alias

The project uses `@` as an alias for `src/`:

```typescript
import { Button } from "@/components/ui/button";
// resolves to src/components/ui/button
```

This is configured in `vite.config.ts` and `tsconfig.app.json`. Do not remove
the alias — shadcn/ui components depend on it.

---

## Preview Production Build

```bash
npm run preview
```

Serves the `dist/` folder locally for production smoke-testing.

---

## Commit Style

Follow the existing commit convention:

```
feat(scope): short description
fix(scope): short description
docs: short description
```

Examples from the project history:
- `feat(v0.3): sync frontend to v0.3 backend API contracts`
- `fix(ui+api): register neon colors in Tailwind v4`
