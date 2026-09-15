"""Which strength evidence may size a prescribed load, as of a moment (S2).

Two readers size strength against an e1RM: the prescribed load
(``prescription_service._current_e1rm_values``) and the dose-intensity denominator
(``state_service.prelog_e1rm_denominators``). ADR-0056 requires them to agree, so both
select through :func:`select_basis` — and they agree only for identical evidence AND an
identical ``as_of``. A prescription and a later, or backdated, workout log can straddle an
expiry; each reader passes its own reference time rather than assuming one.

A row is examined in this order, failing closed:

1. **Eligibility** — valid and not quarantined; explicitly permitted
   (``affects_prescription IS TRUE``; NULL is nobody having said, not permission); known
   provenance; value semantics that describe a strength level; and, for any evidence
   derived from a set, the qualifying-set gate (ADR-0055) judged on the stored set. "Derived
   from a set" is decided by the evidence, not the route that recorded it: every value
   that is not a directly measured performance — workout extraction, or a rep-max an
   athlete reports in Assess — must clear the same gate. A recent date never rehabilitates
   a row that fails here.
2. **Freshness** — performed within :data:`STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS` of
   ``as_of``, measured from PERFORMANCE time. Undated and future-dated evidence is
   ineligible. Submission, migration, or re-entry time never refreshes an old performance.
3. **Selection** — the highest qualified value for the lift (comparable = the same e1RM
   benchmark code); equal values tie to the newest performance, then the higher
   observation id. The selected row's provenance travels with it: selecting an estimate
   never makes it a measurement, and a "reported tested max" stays self-report.

When nothing qualifies, the selection says why (a reason code) and which row explains it
(``explanatory``: the most recently performed of the rows that came closest to qualifying —
undated rows rank below dated ones — then the highest id). :func:`explain_missing_basis`
turns that into the athlete-facing category shown beside the prescribed exercise. These
describe ELIGIBILITY, never a verdict that the athlete became weaker.

Expiry is evaluated here, at selection time. It changes prescription eligibility only: it
never invalidates the observation, lowers the capacity watermark, or flips a stored flag.

Three limits of this slice, stated so nobody reads more into it:

* **Comparability.** "The same e1RM benchmark code" is accepted as comparable for the
  three canonical lifts only, each of which is exactly one catalog exercise (pinned by
  ``tests/test_prescription_evidence.py``). It must not silently authorize sharing
  evidence between variants when coverage widens.
* **The reason is a summary.** When nothing qualifies, the reason reported is how far the
  furthest-progressing row got. It does not mean every rejected row failed for that
  reason.
* **Historical training rows.** Before S2, extraction wrote ``affects_prescription=False``
  for any gated set below the all-time watermark. No migration rewrites those flags, so
  equally characterized historical non-PR rows can remain excluded until they leave the
  window. Re-logging the same workout to regain eligibility is not a remedy — it would
  duplicate a training event.

The maximum rule replaces an effectively unbounded peak with a
``STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS`` peak. It still favours unusually high
observations while they remain in the window: an eligible 180 kg from 27 days ago beats an
eligible 160 kg from yesterday, and when the 180 expires the 160 wins. That is the chosen
policy — not decline detection, not an unbiased current estimate, and not conservative
merely because it abstains after expiry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.logic import observation_authority as oa
from app.logic import strength_evidence as se

#: Revalidation horizon for evidence that may size a prescribed load. A product-policy
#: parameter, NOT a physiological expiry date and NOT a validated safety threshold:
#: chosen 2026-09-14 as a provisional four-week horizon (S2 ruling R1) and unvalidated
#: until outcome data says otherwise.
STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS = 28
_MAX_AGE = timedelta(days=STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS)

#: The stored ``validity_status`` of an observation that has not been quarantined or
#: invalidated. A bare string on purpose: it is what the column default and a025's
#: backfill write; ``strength_evidence``'s purpose-specific vocabulary is not persisted.
VALIDITY_VALID = "valid"

#: Provenance a prescription basis may come from. ``legacy_unknown`` — or no statement
#: at all — is not known provenance, however recent the row claims to be.
_KNOWN_PROVENANCE = frozenset({oa.ST_ATHLETE_ENTRY, oa.ST_WORKOUT_EXTRACTION})

#: Value semantics that describe a strength level. ``unknown`` and NULL do not.
_APPLICABLE_SEMANTICS = frozenset({se.VS_MEASURED, se.VS_ESTIMATED, se.VS_LOWER_BOUND})

# Why nothing qualified. Listed in the order a row is examined: a row that fails later got
# further toward qualifying, and the aggregate reason reports the furthest any row got.
REASON_NO_EVIDENCE = "no_evidence"
REASON_NOT_PERMITTED = "not_permitted"
REASON_UNKNOWN_PROVENANCE = "unknown_provenance"
REASON_INSUFFICIENT_CHARACTERIZATION = "insufficient_characterization"
REASON_MISSING_PERFORMED_AT = "missing_performed_at"
REASON_FUTURE_DATED = "future_dated"
REASON_STALE = "stale"

_STAGE: dict[str, int] = {
    reason: stage
    for stage, reason in enumerate((
        REASON_NO_EVIDENCE,
        REASON_NOT_PERMITTED,
        REASON_UNKNOWN_PROVENANCE,
        REASON_INSUFFICIENT_CHARACTERIZATION,
        REASON_MISSING_PERFORMED_AT,
        REASON_FUTURE_DATED,
        REASON_STALE,
    ))
}

# Why there is no weight, in the athlete's terms. Eligibility language only.
EXPLAIN_STALE = "stale"
EXPLAIN_MISSING_DATE = "missing_performance_date"
EXPLAIN_ESTIMATE_NOT_USED = "estimate_not_used"
EXPLAIN_SET_NOT_QUALIFYING = "set_not_qualifying"
EXPLAIN_NO_EVIDENCE = "no_evidence"
EXPLAIN_NOT_QUALIFYING = "not_qualifying"


@dataclass(frozen=True, kw_only=True)
class EvidenceRow:
    """The fields of one benchmark observation that decide whether it may size a load."""

    observation_id: int
    raw_value: float | None
    performed_at: datetime | None
    validity_status: str | None
    quarantined_at: datetime | None
    affects_prescription: bool | None
    source_type: str | None
    value_semantics: str | None
    evidence_type: str | None = None
    source: str | None = None
    reps: int | None = None
    load_kg: float | None = None
    rpe: float | None = None
    rir: float | None = None
    effort_fidelity: str | None = None


@dataclass(frozen=True)
class BasisSelection:
    """The qualified observation for one lift, or why there is none (never both).

    ``explanatory`` is set only when nothing qualified and some row exists: the most recently
    performed of the rows that got furthest toward qualifying.
    """

    selected: EvidenceRow | None
    reason: str | None
    explanatory: EvidenceRow | None = None


def utc_naive(moment: datetime) -> datetime:
    """Stored timestamps are naive UTC. An aware value is converted, never reinterpreted."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def ineligibility(row: EvidenceRow, *, as_of: datetime) -> str | None:
    """Why ``row`` may not size a prescribed load at ``as_of``; ``None`` when it may."""
    if (
        row.validity_status != VALIDITY_VALID
        or row.quarantined_at is not None
        or row.affects_prescription is not True
    ):
        return REASON_NOT_PERMITTED
    if row.source_type not in _KNOWN_PROVENANCE:
        return REASON_UNKNOWN_PROVENANCE
    if row.raw_value is None or row.value_semantics not in _APPLICABLE_SEMANTICS:
        return REASON_INSUFFICIENT_CHARACTERIZATION
    if _is_set_derived(row) and not clears_qualifying_set_gate(
        reps=row.reps, load_kg=row.load_kg, rpe=row.rpe, rir=row.rir,
        effort_fidelity=row.effort_fidelity,
    ):
        return REASON_INSUFFICIENT_CHARACTERIZATION
    if row.performed_at is None:
        return REASON_MISSING_PERFORMED_AT
    age = utc_naive(as_of) - utc_naive(row.performed_at)
    if age < timedelta(0):
        return REASON_FUTURE_DATED
    if age > _MAX_AGE:
        return REASON_STALE
    return None


def _is_set_derived(row: EvidenceRow) -> bool:
    """Evidence whose value was inferred from a set rather than measured directly. Decided
    by the evidence itself, never by the route that recorded it."""
    return row.source_type == oa.ST_WORKOUT_EXTRACTION or row.value_semantics != se.VS_MEASURED


def clears_qualifying_set_gate(
    *,
    reps: int | None,
    load_kg: float | None,
    rpe: float | None,
    rir: float | None,
    effort_fidelity: str | None,
) -> bool:
    """The ADR-0055 qualifying-set gate, shared by every route that records set-derived
    strength evidence: a loaded set of 1-5 reps near failure. An unstated effort fidelity
    fails closed to the stricter bar."""
    if load_kg is None or load_kg <= 0:
        return False
    return se.is_e1rm_informative(reps, rpe, rir, effort_fidelity or se.FIDELITY_UNSTATED)


def select_basis(rows: Iterable[EvidenceRow], *, as_of: datetime) -> BasisSelection:
    """The highest qualified value for one lift at ``as_of``, or the reason there is none."""
    best: EvidenceRow | None = None
    closest = REASON_NO_EVIDENCE
    explanatory: EvidenceRow | None = None
    for row in rows:
        reason = ineligibility(row, as_of=as_of)
        if reason is not None:
            if _STAGE[reason] > _STAGE[closest]:
                closest, explanatory = reason, row
            elif reason == closest and explanatory is not None and _recency(row) > _recency(explanatory):
                explanatory = row
            continue
        if best is None or _rank(row) > _rank(best):
            best = row
    if best is not None:
        return BasisSelection(selected=best, reason=None)
    return BasisSelection(selected=None, reason=closest, explanatory=explanatory)


def explain_missing_basis(selection: BasisSelection) -> str | None:
    """Why there is no weight, as an athlete-facing category; ``None`` when a basis exists."""
    if selection.selected is not None:
        return None
    reason, row = selection.reason, selection.explanatory
    if reason == REASON_NO_EVIDENCE or row is None:
        return EXPLAIN_NO_EVIDENCE
    if reason == REASON_STALE:
        return EXPLAIN_STALE
    if reason == REASON_MISSING_PERFORMED_AT:
        return EXPLAIN_MISSING_DATE
    if row.evidence_type == se.EV_REPORTED_ESTIMATE:
        return EXPLAIN_ESTIMATE_NOT_USED
    if (
        reason == REASON_INSUFFICIENT_CHARACTERIZATION
        and _is_set_derived(row)
        and (row.reps is not None or row.load_kg is not None)
    ):
        return EXPLAIN_SET_NOT_QUALIFYING
    return EXPLAIN_NOT_QUALIFYING


def _rank(row: EvidenceRow) -> tuple[float, datetime, int]:
    """Value, then newest performance, then the higher id. Only eligible rows are ranked,
    and eligibility guarantees both fields are set."""
    assert row.raw_value is not None and row.performed_at is not None
    return (row.raw_value, utc_naive(row.performed_at), row.observation_id)


def _recency(row: EvidenceRow) -> tuple[bool, datetime, int]:
    """Most recently performed first; undated rows rank below every dated one; then id."""
    performed = utc_naive(row.performed_at) if row.performed_at is not None else datetime.min
    return (row.performed_at is not None, performed, row.observation_id)
