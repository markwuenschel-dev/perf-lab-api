---
status: accepted
date: 2026-06-20
---
# Model fatigue as multi-dimensional

Fatigue is tracked as multiple channels — the decomposed `fatigue_f` axes
(`cns, muscular, metabolic, structural, tendon, grip`) plus legacy scalar mirrors
(`f_met_systemic, f_nm_peripheral, f_nm_central, f_struct_damage`) — because
different fatigue types constrain different training choices. Flattening fatigue into
a single readiness score too early would lose signal the prescriber needs.

**Guardrail:** a UI may show a summary readiness score, but the engine must preserve
the individual channels.
