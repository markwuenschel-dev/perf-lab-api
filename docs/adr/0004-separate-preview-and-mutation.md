---
status: accepted
date: 2026-06-20
---
# Keep preview and mutation separate at the API

`POST /v1/simulate-dose` is a pure preview ("what would this workout do?");
`POST /v1/log-workout` is a real state transition ("this happened; update the
athlete"). Clients genuinely ask these two different questions, so the paths stay
distinct rather than fused behind one endpoint with a flag.

**Guardrail:** do not fuse the preview and mutation paths.
