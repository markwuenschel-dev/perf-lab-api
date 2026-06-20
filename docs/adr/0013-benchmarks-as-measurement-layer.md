---
status: accepted
date: 2026-06-20
---
# Use benchmarks as a separate measurement layer

Benchmark protocols and benchmark observations are represented separately from
workouts. Benchmarks are *measurements*: they may happen inside a planned session, but
their role is to calibrate or validate the model, not to drive dose. When a measurement
should update state or weak-point signals, it flows through `BenchmarkObservation` and
`ObservationMapping`.

This is the architectural side of the product decision in
[PDR-0003](../pdr/0003-benchmarks-are-the-measurement-layer.md).

**Guardrail:** route measurements through the benchmark layer, not the workout/dose path.
