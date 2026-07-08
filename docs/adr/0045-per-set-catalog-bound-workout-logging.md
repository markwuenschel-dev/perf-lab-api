---
status: proposed
date: 2026-07-07
---
# Workout logging is per-set, catalog-bound, and modality-heterogeneous

The logged workout was a single flat, running-shaped row (`workout_logs`: one
`modality`, `duration_minutes`, `distance_meters`, one `session_rpe`). Strength was
unrepresentable: the request DTO's per-exercise `exercises` list was consumed for the
dose and then discarded, nothing persisted sets/reps/load, no UI entered them, and the
dose law fell back to effort-only (`I = 1`) because no external load ever reached it.
This is the concrete source of "the log is all running" and it blocks the concurrent
multi-domain thesis ([PDR-0001](../pdr/0001-concurrent-multidomain-thesis.md),
[PDR-0002](../pdr/0002-domain-as-lens-over-one-body.md)) at the logging layer.

We remodel the log around the **set**:

- **The set is the atomic logged unit.** A straight-sets day is entered once
  (`3 × 5 @ 100kg @ RPE8`) and materializes three editable set rows — top-set and
  per-set RPE are preserved rather than averaged away, because e1RM extraction wants the
  top set and the dose law's failure-proximity is a last-set property, not a mean.
- **A set binds to a catalog `Exercise`; the exercise's `load_type` types the set's
  fields.** `barbell/dumbbell → load_kg+reps+RPE`, `bodyweight → reps(+band/elevation)`,
  `time → duration`, `distance → distance+pace`. A free-text fallback logs movements not
  yet in the catalog (OCR obstacles, novel skills) with estimated phi and no benchmark
  linkage until a recurring one is promoted. The exercise, not a fixed form, decides the
  fields — this is what stops the log being distance/pace-shaped.
- **`workout_set_logs` is the system of record; benchmarks stay the measurement layer.**
  Sets persist as queryable child rows (not a JSONB blob). e1RM/PR/progression are
  **not** read by scanning set logs — write-time extraction emits `benchmark_observations`
  and every measurement surface reads from there, holding the line of
  [PDR-0003](../pdr/0003-benchmarks-are-the-measurement-layer.md).
- **A session is a heterogeneous bag of sets.** Modality is per-set (from the exercise);
  the session-level `modality` becomes a *derived* label (uniform → that modality, else
  `Mixed`). Running is just a `distance`-type set. Hyrox / Spartan / run-and-lift days
  are one honest session, not two.
- **Strength prescriptions speak in load, closing
  [ADR-0039](0039-dose-law-external-load-vs-effort.md)'s loop.** A prescribed lift carries
  `%e1RM → a suggested kg` (resolved against the athlete's current e1RM benchmark via the
  [ADR-0029](0029-periodization-intent-envelope.md) intensity envelope) **plus** an RPE
  cap. The log pre-fills the suggested kg; the athlete overrides with actual; dose uses
  actual load (external `I`) × RPE (effort `F`). With no e1RM yet, degrade to RPE-only
  autoregulation until logged sets seed one.

Rejected: keeping the **per-exercise summary** (loses top-set/e1RM and failure-proximity);
a **JSONB blob as the queried store** (a parallel measurement source PDR-0003 forbids —
the blob only ever won on migration cost, which a child table pays once); **one modality
per session** (can't log concurrent work); **pure autoregulation** with no suggested load
(discards the external-load signal ADR-0039 exists to capture).

**Guardrail:** the set is the unit and it binds to the exercise catalog — never reintroduce
a fixed running-shaped log form or a single session modality. Set logs are the record;
measurement flows through benchmarks, not through scans over set logs.
