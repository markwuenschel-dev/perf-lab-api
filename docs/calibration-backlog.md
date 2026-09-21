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

## C3 · Endurance has no density input at all

**Observed (phase 1.2).** v1 reports `density_basis = not_applicable` for continuous and
mixed sessions: a run's work is distance and pace, which the set count cannot describe.

**What a fit must resolve.** Phase 5 supplies a real temporal work quantity from interval and
continuous structure (`structured_endurance_work`). Until then, endurance rows in the shadow
dataset carry `v1_density_not_modelled = true`, and a fit must not read their v1/v0 ratio as
evidence about density — it is evidence about a missing input.
