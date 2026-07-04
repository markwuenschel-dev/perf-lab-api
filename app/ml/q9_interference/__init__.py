"""Q9 concurrent-training interference — offline suppression-alpha estimation (shadow-only).

Learns/validates the ADR-0037 CROSS-AXIS interference suppression alphas from data where
an athlete's concurrent endurance / CNS load precedes a strength/power/skill benchmark
change. Emits a versioned ``shadow_only`` artifact recording learned-vs-default alphas and
the UNWIRED binding to ``EngineParameters.interference_*_alpha``; nothing here applies an
override. Mirrors the shipped Q2/Q6 pipeline shape.
"""
