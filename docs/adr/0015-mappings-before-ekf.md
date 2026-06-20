---
status: accepted
date: 2026-06-20
---
# Use observation mappings before full EKF complexity

Benchmark assimilation currently uses weighted residual-style mapping rules rather than
a full Extended Kalman Filter. This closes the benchmark-to-state loop while keeping the
system legible and easy to debug. The trade-off is that it is less theoretically
complete than a full state estimator — accepted deliberately until the model is mature
enough to justify heavier assimilation machinery.

**Guardrail:** keep mappings explicit and auditable; defer EKF/Bayesian machinery until
it's clearly warranted.
