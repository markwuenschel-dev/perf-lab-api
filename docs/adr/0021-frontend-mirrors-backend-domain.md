---
status: accepted
date: 2026-06-20
---
# Keep the frontend control loop close to the backend domain

The UI's names and flow mirror backend concepts
(`simulate-dose -> log-workout -> next-session`;
`planning block -> planned session -> today's session`). This preserves conceptual
legibility and avoids inventing a separate UI-only mental model that drifts from the
engine.

**Guardrail:** do not hide the control loop behind vague UI abstractions too early.
