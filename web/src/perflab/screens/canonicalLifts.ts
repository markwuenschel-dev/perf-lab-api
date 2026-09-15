// src/perflab/screens/canonicalLifts.ts
//
// The three canonical lifts whose strength is characterized evidence (S2). The codes are the
// backend's e1RM benchmark codes — pinned there by tests/test_prescription_evidence.py and
// tests/test_strength_single_authority.py — and `profileKey` is the read-only projection the
// profile shows for each. A strength fact for these lifts is always a report (method + value or
// set + date) sent through the strength-evidence service, never a bare profile number.
export const CANONICAL_LIFTS = [
  { code: "pl_e1rm_squat", label: "Squat", profileKey: "squat_1rm_kg" },
  { code: "pl_e1rm_bench", label: "Bench press", profileKey: "bench_1rm_kg" },
  { code: "pl_e1rm_deadlift", label: "Deadlift", profileKey: "deadlift_1rm_kg" },
] as const;

export type CanonicalLift = (typeof CANONICAL_LIFTS)[number];
