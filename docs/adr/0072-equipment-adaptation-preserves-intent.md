---
status: accepted
date: 2026-09-27
---
# Equipment adaptation preserves training intent

Phase 9. Relates to ADR-0016 (catalog-resolved exercise slots), ADR-0064 (a constraint is
never bypassed to fill the calendar) and [ADR-0071](0071-domain-periodization-envelope.md).

## Context

A planned day is bound to a template (`app/logic/planned_session_slots.py`). Under the
athlete's equipment, the prescriber had three ways to produce something that was not that
day's session, and none of them said so:

1. **A template with no slots** went to the generic equipment map (`_map_selection`). For an
   athlete with no equipment configured, that meant "Air Squat, Push-up, Lunges" on a
   strength-volume, hypertrophy-isolation or neural-priming day.
2. **A template whose slots mostly failed** emitted the subset that resolved, or the map when
   none did. So a session was prescribed under the planned session's name while being a
   different session.
3. **An empty pool** fell back silently to the general templates ("should not happen"). A
   bodyweight athlete on a HYROX day got full-body GPP.

The phase-9 kit harness (`app/scripts/kit_matrix.py`, `tests/test_kit_eligibility.py`) found
59 such cells among 144 planned-day × kit combinations.

## Decision

**Equipment adaptation may change the exercise implementation. It must not silently change
the session's training intent or sport identity.**

- Every planned day × kit has an authored expectation (`app/logic/kit_support.py`).
  - A **supported** kit gets the planned template, or a stated in-domain replacement, with
    every authored slot realized.
  - An **unsupported** kit gets a stated in-domain replacement
    (`plan:session_replaced=<slug>(equipment)`). If there is none, it gets an explicit
    zero-work **Equipment Unavailable** (`plan:session_unavailable=<slug>`,
    `equipment:unavailable=<missing tags>`). That outcome keeps the planned domain, category
    and binding.
- An authored slotted template is **atomic** under configured equipment, as circuits already
  were. Partial realization is a different session.
- Equipment variants are **family parameters** (`kit_fallback_for`). A variant is eligible
  only when its primary does not resolve, and never rotates with it.
- The generic equipment map is not an adaptation. It remains only for "no catalog loaded".
- `unconfigured` equipment keeps its permissive meaning and is never "unavailable".

## Consequences

- Some athletes see "Equipment Unavailable" where they used to see filler. That is the point:
  telemetry can now tell "adapted" apart from "could not fulfil".
- Honest variants are authored per day, and only where intent survives. Examples: a dumbbell
  front squat keeps a volume-strength day, and jumps keep neural priming. No bodyweight
  movement stands in for a snatch.
- The matrix is product judgement and is expected to change. A change is a reviewed edit to
  `kit_support.py`, and the test pins today's behaviour to it cell by cell.
