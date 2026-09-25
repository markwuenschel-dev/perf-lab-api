# Calibration backlog

Named, measured questions for phase **8B** (coefficient fitting). Each entry states what was
observed, how it was measured, and what a fit would have to resolve. Nothing here is a fix:
these are findings that a re-fit must account for, recorded when they were discovered so the
fit is not designed in ignorance of them.

This is not a list of bugs. A bug is wrong behaviour; these are places where the model is
*insensitive* or *unvalidated*, which no amount of correct code resolves — only data does.

---

## C1 · Load/intensity sensitivity: dose cannot price a volume-for-intensity trade

**The statement.** With sets and session duration held constant, a materially higher resolved
load (equivalently, a lower RIR) produces almost no change in modelled dose — in **v1** as
well as v0. Until this is calibrated or the law is redesigned, **dose cannot reliably
distinguish volume-for-intensity tradeoffs**, which is precisely the choice a max-strength
session is made of.

**Observed (phase 3.2, `docs/simulations/phase-3-2.md`).** Holding sets and session duration
fixed and increasing resolved load by lowering the target RIR by one — RPE 8 → 9, which
resolves to 120 kg → 125 kg on a 140 kg e1RM — changes total modelled dose by **1.02×**. In
**both** engines.

**Sharpened (phase 3.4, `docs/simulations/phase-3-4.md`).** Against the live rule rather than
a bare baseline, the consequence is starker and it decided a promotion. Legacy `hard` for max
strength gives `6×3 @ RPE 9 / 125 kg`; the candidate gives `5×3 @ RPE 9.5 / 127.5 kg` — three
working reps and 338 kg of volume-load traded for 2.5 kg on the bar. v1 reads legacy hard at
**0.64** against medium's 0.55, but reads the candidate at **0.56**: essentially identical to
medium. The engine we are migrating TO is the one that cannot see the candidate's only lever.

That is why phase 3.4 promoted nothing. Choosing `5×3 @ 127.5` over `6×3 @ 125` on the
strength of the model would be choosing on a number the model does not compute.

**Why it happens.** The session volume proxy is
`V = 1.0·duration + 0.02·volume_load + 2.0·sets` (`app/engine/parameters.py`,
`dose_volume_weights`). Load enters only through `volume_load`, at a weight of 0.02, so a 4%
load increase at constant sets and duration moves `V` by a fraction of a percent. Effort also
enters through `F` (proximity to failure), but not enough to change the total materially.

**Why it matters.** A max-strength session's difficulty *is* proximity to failure and the load
that follows from it. A model that cannot distinguish a hard maximal session from an easy one
cannot value that family's training at all — and phase 3.2's `max_strength` candidate is
exactly such a transform.

**What a fit must resolve.** Whether the dose law's response to relative load (at fixed
volume) is genuinely near-flat, or whether `volume_load`'s weight and the `F` exponent are
simply unfitted. This needs sessions that differ in load at matched set counts, with an
outcome to fit against.

**Not asserted as a gate.** `tests/test_difficulty_invariants.py` records the insensitivity
and asserts the *structural* claim (RIR falls, resolved load rises, rest and selection
unchanged) without demanding a magnitude. There is no evidence for what the magnitude should
be, and inventing a threshold would encode a guess as a requirement.

**Promotion criterion.** No difficulty policy whose primary lever is load or proximity to
failure should be promoted on modelled-dose evidence until this is resolved. Such a policy may
still be promoted on prescription quality — but that argument has to be made and recorded on
its own terms, not supported by a dose number that is flat by construction.

---

## C2 · Density semantics are corrected but uncalibrated

**Observed (phase 1.2, 8A).** v1 defines density as work per unit elapsed time; v0 defined it
as minutes per set, the reciprocal. Replacing `x` with `1/x` changes the response surface
nonlinearly, so `dose_beta` and every density-dependent coefficient fitted for v0 do not
describe v1.

**Measured consequences.** v1/v0 total dose ranges 0.36×–3.08× across ordinary sessions;
375 of 990 controlled one-factor pairs invert (`docs/simulations/dose-v0-v1.md`).

**What a fit must resolve.** The exponent and the axis shaping, against logged sessions
captured by the 8A shadow (`dose_model_shadow_log`), excluding rows flagged
`v0_volume_used_fabricated_sets`.

---

## C2b · Density-output coupling: the density AXIS still grows with duration at fixed work

**The statement.** v1 correctly defines the density *input* as work per elapsed time. The
density *output* — the `density` component of the six-axis dose — is not invariant to a
duration change at fixed work, because it is scaled by the same session base as every other
axis. Density-input semantics are fixed; density-axis semantics are not.

**Why it happens.** Every axis is `base · m[axis] · …`, and the density axis is
`base · m["density"] · Δ` (`app/logic/dose_engine_v0.py:754, :764, :773` — the law v1 shares).
`base` carries `log1p(V)` (`:522-529`), and the volume proxy `V` includes `1.0·duration`
(C1). So a longer session at identical work raises `base`, and with it the density axis, even
as `Δ` falls or stays put.

**Observed (phase 4.1, 2026-09-21).** Running the two density invariants in
`tests/properties/test_dose_invariants.py` against `dose_engine_v1` instead of v0: both still
fail. Falsifying example for "same work in less elapsed time is denser": 20 sets in 20 min vs
40 min — v1 reports `density_value = 2.5` for both (Δ at its clamp) and a density axis of
**1.436** for the faster session against **1.535** for the slower one. Not established:
whether the inversion also occurs where Δ is unclamped, or only when Δ saturates.

**Why it matters.** "v1 fixed density" is true of the input and false of the axis. Those two
xfails will NOT clear when production activates v1 at 8C, and anyone reading the density axis
as temporal compression will be misled.

**What a fit (or redesign) must resolve.** Whether the density axis should be decoupled from
`base`'s duration term, or the invariants restated against `density_value`. This is a dose-law
question, deliberately not addressed in the workout-family refactor (phase 4).

---

## C4 · Tissue-penalty magnitude is a hand-set guess

**The statement.** Every template's `tissue_penalty` is `max(named tissues) / 100 ·
tissue_weight` (`app/logic/candidate_library.py`, `_score_from_spec`). Since phase 4.2b the
`max` is uniform: the penalty reads the most-stressed tissue the session loads, for all 37
templates. That settles WHICH tissue matters. It says nothing about HOW MUCH a loaded tissue
should cost: `tissue_weight` (per template) and the global `tissue_penalty` score weight
(`-0.08`, `app/logic/constraint_engine/candidate.py`, `DEFAULT_SCORE_WEIGHTS`) are fitted to
nothing.

**Why this is recorded rather than fixed.** 4.2b was a semantic consistency change — phase
1.5 had already chosen weakest-link, and 13 hand-coded templates still averaged. It needed no
outcome data because it claims no outcome: a knee at 90 beside a hip at 0 should read as 90,
not a synthesized 45. Claiming the penalty's SIZE predicts injury, recovery or performance is
a different claim, and it does need data.

**Measured sensitivity (phase 4.2b, 2026-09-21).** Mean → max raised the penalty by up to
0.375 on the characterization grid, moving a weighted total by at most 0.030 — enough to
change the chosen session in close pools (3 of 84 grid rankings changed their top pick).

**What a fit must resolve.** Per-template `tissue_weight` and the global weight, against
logged sessions with a tissue outcome (pain reports, missed sessions, readiness drops).

---

## C3 · Endurance has no density input at all

**Observed (phase 1.2).** v1 reports `density_basis = not_applicable` for continuous and
mixed sessions: a run's work is distance and pace, which the set count cannot describe.

**What a fit must resolve.** A not-modelled endurance row's v1/v0 ratio is evidence about a
missing input, not about density.

**Phase 5.4 (2026-09-25): a prescribed proxy, not performed density.** When a log is
EXPLICITLY linked to a planned session whose structure is fully timed, v1 (dose version
`v1.1`) reads `prescribed_timed_work_over_elapsed`: the prescription's work seconds (interval
work only; recovery enters through the denominator) over the logged elapsed seconds. It is
dimensionless in (0, 1], and each row's `v1_dose_json.density_provenance` records it as
`prescribed_not_performed`. Impossible timing (prescribed work > logged elapsed), a zero
elapsed time, ranged or distance-only work, and heuristic links stay not modelled, with a reason.

**Default for 8B, encoded in `dose_model.DENSITY_FIT_ELIGIBILITY`:** these rows are EXCLUDED
from fitting (as are not-modelled and legacy rows). They remain useful for shadow diagnostics.
A fit may opt in only after demonstrating adherence. Performed structure, when the athlete can
log it, gets its own basis (`performed_timed_work_over_elapsed`, fit-eligible), so historical
proxy rows stay unambiguous. A guard test makes any `app/ml` reader of the shadow log consult
the policy.

---

## C5 · Endurance difficulty has levers but no magnitudes (phase 5.5, deferred)

**The statement.** `app/logic/difficulty.py` `POLICIES` already says which lever each endurance
family may pull when difficulty changes: easy aerobic moves duration, threshold moves
accumulated work, a max-velocity sprint session moves the number of quality repetitions. It does
not say how far any of them moves, and `CONSTRAINTS` states no endurance bound, so the validator
rejects any threshold recovery change and any easy-aerobic intensity change.

**Why deferred (2026-09-25).** No universal progression constant exists to borrow: endurance
programming varies repetitions, duration, recovery and intensity by session type, athlete and
phase (Casado et al. 2022; Hofmann & Tschakert 2017; Tønnessen et al. 2024; Haugen et al.
2022; Buchheit & Laursen 2013). A ±1 repetition or ±15 % duration step chosen now would be a
physiology guess written into production. A phase 3.4 candidate would also stay dormant until
dose can tell the change apart (C1).

**What exists instead.** `tests/test_endurance_difficulty_validator.py` exercises the validator
on interval and continuous blocks with test-only synthetic transforms, and pins that no
endurance bound or transform exists yet. That run found, and fixed, fields the phase 5.3 blocks
added but the detector could not see (`activity`, `rpe_cap`, `recovery_after_last_rep`,
`quality_stop`), and a volume scaler that left the displayed interval count stale.

**What a fit must resolve.** Per-family step sizes and the threshold recovery bound, against
logged endurance sessions with an outcome. One further prerequisite: scaling a continuous
duration must decide how its display text (`"20 min @ RPE 7–8"`) changes. That is a UI
decision, not a side effect.
