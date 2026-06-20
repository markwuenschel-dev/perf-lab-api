---
status: superseded by ADR-0025
date: 2026-06-20
---
# Frontend types are manual mirrors

`src/types.ts` manually mirrored the backend Pydantic schemas — it was not generated.
The project was small enough that manual sync was acceptable, at the cost of requiring
deliberate updates to `types.ts`, `trainingGoals.ts`, the API client, and form/render
components on every backend schema change.

**Superseded by [ADR-0025](0025-generate-ts-types-from-openapi.md):** types are now
generated from the backend OpenAPI schema, ending the hand-mirror drift.
