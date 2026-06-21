---
status: accepted
date: 2026-06-20
---
# Use the exercise library as a metadata layer

Movement metadata — equipment, phi vectors, tissue/fatigue weights, weak-point tags —
lives in `Exercise` rows. The prescriber and dose engine need structured movement
information, and hard-coded movement choices do not scale across modalities. Prefer
data-driven selection and phi resolution over sprawling if/else branches.

**Guardrail:** prefer data-driven exercise selection over hard-coded movement branches.
