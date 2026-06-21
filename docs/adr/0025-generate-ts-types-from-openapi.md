---
status: accepted
date: 2026-06-20
---
# Generate web TypeScript types from the OpenAPI schema

The web app's TypeScript types are generated from the backend's `/openapi.json`
(via `openapi-typescript` → `src/types.gen.ts`) instead of hand-mirrored. The
hand-written API client functions stay; only the type definitions are generated. A
backend schema change is now caught by regenerating and running `tsc --noEmit`, ending
the manual `types.ts` drift that [ADR-0020](0020-frontend-types-manual-mirror.md)
lived with.

Supersedes [ADR-0020](0020-frontend-types-manual-mirror.md). Requires the OpenAPI
schema to stay clean for all routers (the P0 `/openapi.json` serialization fix is a
prerequisite).

**Guardrail:** treat `types.gen.ts` as generated output — never hand-edit it;
regenerate from the schema instead.
