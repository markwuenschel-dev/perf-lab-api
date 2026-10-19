---
status: accepted
date: 2026-10-19
accepted: "2026-10-19, by the owner (wave B, B4), accepting the rule decided as plan v3 fork 4 on 2026-10-04 and built in PRs #281 (P1a) and #282 (P1b)"
delivery: delivered
deployed: yes
activation: live (no flag; app/services/prescription_issuance_service.py)
---
# An issued prescription revision is immutable; only safety or the athlete's recheck replaces it

Split out of [ADR-0063](0063-session-commitment-and-issuance.md), which stays `proposed`. This
record accepts one clause of it, the one that was decided, built and deployed: what the athlete
has been shown is never rewritten. ADR-0063's commitment model (accepted / started / expired /
superseded states, commitment sources, delivery receipts, the load ledger) is **not** accepted
here.

**Decision.** The first evaluation of a planned session issues revision 1. Every later evaluation
either serves the current revision unchanged or issues a new revision. It never edits an issued
one: a revision's content and identity (content, content hash, revision number, session, safety
signature, reason, origin) cannot be updated, which an UPDATE trigger enforces (`a051`), nothing in
the app deletes revisions, and `revision_no` only increases for the session's whole life. Ordinary
movement (ranking, exercises, loads, objectives, and readiness movement that does not change the
readiness-redirect outcome) never replaces an issued revision; it informs the next session that has
not been issued yet.

**Only safety, or the athlete's own recheck, replaces, and visibly.** The replacement rule, as
decided in fork 4 and implemented in `prescription_issuance_service.decide`:

| situation | outcome |
|---|---|
| no current revision (including preserved legacy content) | issue: `first_issue` / `legacy_reissue` |
| the current revision is a "safety could not run" rest | replace: `safety_check_rerun` |
| the issued workout fails current safety validation | replace: `issued_no_longer_safe` |
| the structured safety signature changed toward a restriction, or to a different one | replace: `safety_outcome_changed` |
| a restriction cleared | keep on read; on the athlete's `POST /v1/planning/today/recheck`, replace: `athlete_recheck` |
| same safety signature | keep: same revision, identical content |

A replacement is a new revision with its reason recorded; the previous revision is kept, and a
logged workout keeps the revision it answered. The safety signature is structured (kind, the
branch that fired, the exact hard-violation and unevaluated codes), not a boolean; a readiness
redirect is one of its kinds, so readiness that starts or changes a redirect does replace. A freshly
scored alternative passing validation does not prove the stored workout is still safe, so the
stored content itself is re-validated against the current state.

**Not decided here** (left to ADR-0063): when an issued revision becomes a commitment, whether
issuance should happen on `GET /planning/today` (today it does; ADR-0063 argues it should not), and
the `safety_invalidated` terminal state with ledger accounting.

**Evidence.** `tests/test_prescription_revisions.py`: the decision table
(`test_decision_table`), fork 4 (`test_fork4_changed_inputs_same_safety_serve_the_same_revision`),
replacement preserving the previous revision
(`test_a_safety_restriction_appearing_replaces_and_preserves_the_previous`), the content and
identity trigger (`test_revisions_are_immutable_in_the_database`), the recheck
(`test_a_cleared_restriction_is_kept_until_the_athlete_rechecks`), and a log keeping its revision
(`test_a_log_keeps_the_revision_it_answered`).

**Guardrail:** no code path updates an issued revision's content; a change the athlete must see
is a new revision with a recorded reason, from safety or from the athlete's recheck.
