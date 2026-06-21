---
status: accepted
date: 2026-06-21
---
# Per-axis capacity confidence: a scalar now, EKF covariance later

[ADR-0034](0034-residual-based-benchmark-anchor.md) (residual gain) and
[ADR-0035](0035-benchmark-seeded-initial-state.md) (the seed is a weak prior) both assume
the model knows *how sure* it is about each capacity axis. Without it, a measured
benchmark and a guessed-from-experience seed compete as equals and the residual anchor
has no principled gain. We add a **per-axis confidence/variance scalar** on the capacity
vector: the seed prior starts low-confidence (high variance → the next observation moves
the axis a lot), a benchmark shrinks variance and corrects hard, and time since last
measurement grows variance. The correction gain is a scalar, Kalman-style function of that
variance. Confidence is tracked for **capacity axes only** — fatigue and tissue are
transient and re-driven each session, so a stored prior is meaningless there.

Trajectory **2 → 3**: the scalar is the honest pre-EKF stepping stone; it generalizes to
the EKF's covariance with no throwaway, matching the B→A path of
[ADR-0032](0032-relative-state-math-benchmark-anchored.md). Consequence: a low-confidence
axis is a signal the prescriber can act on — prescribe its benchmark to sharpen the twin
(active learning). We rejected implicit fixed gains (can't express per-axis/per-domain
uncertainty, can't drive measurement) and full EKF covariance now (premature —
[ADR-0015](0015-mappings-before-ekf.md)).

**Guardrail:** every capacity axis carries a confidence; corrections scale by it (low
confidence → large correction), and the seed prior must always start less confident than
any real measurement. Keep it a scalar per axis until a deliberate decision to adopt full
covariance.
