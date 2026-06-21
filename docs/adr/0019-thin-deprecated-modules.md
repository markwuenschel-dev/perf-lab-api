---
status: accepted
date: 2026-06-20
---
# Keep deprecated transition modules thin

`app.logic.dose_engine` and `app.logic.state_update` are compatibility/deprecation
shims; current code uses `app.logic.dose_engine_v0.calculate_stress_dose`,
`app.logic.state_update_v0.update_athlete_state`, and
`app.services.state_service.process_new_workout`. This preserves old imports while
making the preferred implementation explicit. Re-introduction is guarded by ruff's
`flake8-tidy-imports` banned-api rule (`TID251`).

**Guardrail:** new code must not build on the deprecated modules.
