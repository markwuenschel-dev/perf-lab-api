---
status: accepted
date: 2026-06-21
---
# Periodization lives as an intent envelope on the block

Two planning systems existed in parallel: a rich periodization model in
`app/logic/planning.py` (accumulation → intensification → peak → taper, with
`target_rpe_range` and `volume_modifier` progression) that **nothing but its own test
imported**, and the live `planning_service.py`, which stamps the *same* flat weekly
template for every week and only flags periodic deloads. The prescriber received
`week_number` in `block_context` but never read it, so week 1 and week 11 of a block
prescribed identically given equal state. We are collapsing these into one: the live
`MesocycleBlock` carries each week's phase, and a week resolves to a
`(target_rpe_range, volume_modifier)` envelope the prescriber reads. State may pull
intensity/volume **down** within the envelope (autoregulation) but never above the
planned ceiling. We rejected keeping two systems (drift, confusion) and deleting
periodization outright (loses the long-horizon shape [PDR-0008](../pdr/0008-plan-is-a-seed-not-a-rail.md) commits to).

**Guardrail:** exactly one periodization source of truth lives on the block. The
prescriber must read the week's phase, and state can reduce but never exceed the
planned envelope.
