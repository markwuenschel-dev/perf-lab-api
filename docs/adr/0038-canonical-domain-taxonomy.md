---
status: proposed
date: 2026-06-21
---
# Canonical domain is the one taxonomy; the prescriber keys on it

Three goal vocabularies coexisted: `TrainingGoal` (14 — the prescriber's
candidate-generator key), `BlockGoal` (9 — the DB column on `MesocycleBlock`), and
`modality_mix` domain strings — plus a `domain_vocab.py` canonical layer that already
existed but that nothing downstream consumed where it mattered. Consequences: the
prescriber branched on `TrainingGoal`; `BlockGoal` values `Hyrox`/`CrossFit`/`Recomp`
weren't even in the alias table; and a Hyrox/Recomp block had no path to real candidates
(fell through to General). That blocks [ADR-0030](0030-block-derived-intent-modality-mix.md)
(block-derived intent) and [ADR-0037](0037-model-concurrent-interference.md) (interference
across the mix), which both need one shared notion of "domain."

We converge everything on `domain_vocab`: canonical `domain` (from `DOMAINS`) is the single
internal taxonomy; the prescriber generates candidates keyed on **canonical domain**
(+ planned-session `category` for sub-intent) rather than `TrainingGoal`; and
`BlockGoal`/`TrainingGoal` remain thin API/DB-facing enums that are always canonicalized
through `domain_vocab` (no DB migration — `BlockGoal` stays a column). Add the missing
`Hyrox`/`CrossFit`/`Recomp` aliases and a "mixed" domain candidate path so concurrent
blocks get real sessions. `modality_mix` is a `domain → weight` map and composes natively.
We rejected a `BlockGoal → TrainingGoal` lookup (unblocks decoupling but keeps three
vocabularies drifting, still can't prescribe concurrent/Hyrox work) and a single new
unified enum (large API/DB blast radius for no gain over canonicalizing internally).

Enables [ADR-0030](0030-block-derived-intent-modality-mix.md),
[ADR-0037](0037-model-concurrent-interference.md); aligns with
[ADR-0022](0022-legibility-over-cleverness.md).

**Guardrail:** `domain_vocab` is the single source of truth for domain/goal/modality;
every module resolves through it and the prescriber dispatches on canonical domain. New
goals/sports are added as aliases there, never as a new parallel vocabulary.
