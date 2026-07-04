"""Q1 next-session decrement — offline, shadow/research-only ML pipeline.

Mirrors the Q2 recovery-priors pipeline (build_training_frame -> train -> evaluate ->
model_card). Learns the RESIDUAL ``decrement = observed_next_rpe - expected_next_rpe``,
where the expectation is E[next_rpe | planned next-session difficulty]. See ``model_card``
for provenance, leakage handling, and where this would feed later (shadow signal into the
prescriber's expected-difficulty / readiness). Nothing here plugs into the engine.
"""
