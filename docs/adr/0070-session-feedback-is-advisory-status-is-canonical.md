---
status: accepted
date: 2026-09-05
accepted: "2026-09-06, PR #219 (implements it)"
delivery: delivered
deployed: yes
activation: live
reconciled: 2026-10-19
---
# Session feedback is advisory; `PlannedSession.status` stays canonical

`SessionFeedback` ([telemetry.py](../../app/models/telemetry.py)) has shipped since the telemetry wave
but is read by nothing on a request path — only the offline dataset builders under `app/analysis/` and
`app/ml/`. Wiring it into the prescriber (reading C, "it proposes, you adjust") means it starts
describing the same planned session that `PlannedSession.status` already describes, and the existing
adherence signal — `count_block_skips` ([planning_service.py](../../app/services/planning_service.py))
— reads `status == SKIPPED` alone. Two readers, one event, no stated precedence: an athlete who reports
a skip could be penalised once through status and again through feedback, and `POST /v1/feedback`
currently transitions nothing, so the two can disagree outright.

**Decision — four invariants, binding on any code that reads feedback:**

1. **Terminal-only.** A `SessionFeedback` row may be created only for a planned session already in a
   terminal state. Feedback describes an outcome; it does not create one.
2. **`PlannedSession.status` is the single canonical source of *occurrence*.** Feedback stays advisory
   and contributes only the dimension status cannot express — *was the session modified, and how*
   (`modified_volume` / `modified_intensity` / `modified_exercises` / `modification_reason`). Feedback
   never writes `status`.
3. **One aggregate, deduplicated by `planned_session_id`.** Occurrence and modification are read as a
   single signal with stated precedence, never as two overlapping counts.
4. **Recency is training time, not reporting time.** The window is measured from the session's
   scheduled/completed date, never `SessionFeedback.created_at`, and is bounded to the active block.

The resulting prescriber adjustment is a **policy response** — bounded, and surfaced in
`why.constraints_applied`. It asserts that the athlete deviated, not that the deviation *caused* a later
outcome. Causal claims are out of scope here and belong to the offline research questions that already
consume these columns.

Invariant 2 is the load-bearing one, and it is a genuine trade-off. Making feedback the transition point
instead would let an athlete skip a session in one write. We rejected it because it turns a data-capture
endpoint into a lifecycle mutator — `POST /v1/feedback` is documented as *"Data-capture only: this
endpoint changes no prescription or decision"* — and because status semantics are already governed
deliberately by [ADR-0069](0069-date-move-does-not-change-session-status.md), which exists precisely
because `PlannedSession` is mutated in place with no revision row to carry a session forward. Widening
who may write `status` makes that repair harder to hold. The cost we accept: skipping requires a PATCH
and a POST, with a partial-failure window the UI must handle.

We also rejected using `created_at` for recency (submission time measures reporting behaviour, not
training behaviour, systematically overweights delayed reports, and makes replays non-reproducible), and
keeping two independent signals (they are not independent observations when they describe the same
session).

**Guardrail:** feedback never writes `PlannedSession.status`, and no reader may count occurrence twice.
If a second consumer of adherence is ever added, it consumes the single deduplicated aggregate — not
`status` and `SessionFeedback` separately. Recency is always derived from when training was scheduled or
completed, never from when the athlete got round to reporting it.
