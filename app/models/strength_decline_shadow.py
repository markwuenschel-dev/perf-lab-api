"""StrengthDeclineShadow — persisted counterfactual telemetry for the candidate-aware
prescription-basis decision (INT-02, ADR-0066).

**NO WRITER IS ACTIVE YET — this table is inert (W1-C1, expand step).**

This is deployment-enabling infrastructure, not complete functionality. The table
exists so the database can accept shadow rows; nothing writes to it. The writer
lands separately in W1-C2, which owns carrying an immutable payload out of
prescription-basis resolution and persisting it *after* the production prescription
commit, in an independent best-effort transaction, via ``INSERT ... ON CONFLICT DO
NOTHING``. Until W1-C2 is verified, INT-02's shadow surface is NOT delivered.

``tests/test_strength_decline_shadow_inert.py`` enforces the no-writer state
structurally; it must be deleted as part of W1-C2, not weakened.

Why the writer is deferred rather than shipped: the first attempt persisted the row
from inside ``resolve_prescription_basis``, which runs *before* prescription's
``db.commit()`` (``prescription_service.py:380``). A shadow-row failure at flush
would therefore have taken the whole prescription down — while a ``try: db.add(row)``
guard gave only the illusion of isolation, since ``db.add()`` stages in memory and
does no I/O. ``prescription_service.py:382-383`` already documents the correct
convention: telemetry is written *after* the commit "so a telemetry failure can never
alter or block ``rx``". W1-C2 follows it.

Once written, rows record what the legacy latest-raw e1RM basis WOULD select versus
the candidate-aware basis — the comparison that today survives only as an
un-queryable ``logger.info`` line (``strength_decline_service.py:623-627``) missing
``absolute_delta``, ``relative_delta``, ``ceiling_semantics``, and ``policy_version``
entirely (docs/superpowers/plans/2026-07-12-int-02-strength-decline-hysteresis.md
:97, :203). It is the surface the flag promotion
(``DECLINE_CANDIDATE_PRESCRIPTION_BASIS``: off → shadow → on) must be justified
against.

``uq_strength_decline_shadow_trigger_axis_policy`` on (``trigger_observation_id``,
``capacity_axis``, ``decline_policy_version``) is the concurrency authority: it makes
the future writer idempotent atomically, so two concurrent prescriptions against the
same candidate cannot both insert. W1-C2 must let the constraint arbitrate via ``ON
CONFLICT DO NOTHING`` rather than re-introducing a SELECT-before-INSERT check, which
is a TOCTOU race that manufactures the very failure it means to avoid.

HARD CONSTRAINT — append-only counterfactual telemetry, mirroring the sibling
shadow logs (``capacity_floor_shadow_log``, ``ekf_shadow_log``, ``mpc_shadow_log``,
``recovery_shadow_log``, ``personalization_shadow_log``,
``dose_routing_shadow_log``): ``decision_impact`` is always
``"none_shadow_only"``. Nothing here constrains prescription or mutates state —
``strength_decline_candidates`` alone owns the live active/confirmed/dismissed
lifecycle. There must be no read path from prescription into this table.

Field mapping to the plan's vocabulary: ``candidate_outcome`` = the evaluated
candidate's ``status``; ``candidate_aware_basis`` = "projected updated capacity"
(what the basis WOULD be if promoted); ``selected_basis`` = "actual applied
effect" (what basis this call actually used, per ``mode``);
``decline_policy_version`` / ``authority_policy_version`` = "policy versions".
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StrengthDeclineShadow(Base):
    __tablename__ = "strength_decline_shadow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # NOT NULL is load-bearing, not incidental: this column participates in
    # uq_strength_decline_shadow_trigger_axis_policy, and SQL treats NULLs as
    # DISTINCT — so a single nullable column silently disables the whole unique
    # constraint (three identical NULL-tid rows insert cleanly). That would make
    # W1-C2's INSERT ... ON CONFLICT DO NOTHING a no-op and let duplicates
    # accumulate unbounded with no error. Mirrors strength_decline_candidate,
    # whose trigger_observation_id is Mapped[int]/nullable=False (a032) — every
    # shadow row is derived from an active candidate, so NULL is not a reachable
    # state. See test_unique_constraint_rejects_duplicates.
    trigger_observation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("strength_decline_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    capacity_axis: Mapped[str] = mapped_column(String(30), nullable=False)
    benchmark_code: Mapped[str] = mapped_column(String(100), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    mode: Mapped[str] = mapped_column(String(10), nullable=False)

    # --- Evidence carried over from the evaluated candidate ---------------------
    candidate_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    prior_mean: Mapped[float] = mapped_column(Float, nullable=False)
    prior_variance: Mapped[float] = mapped_column(Float, nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    observation_variance: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_source: Mapped[str] = mapped_column(String(40), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)

    # --- The basis-decision comparison (the row's raison d'être) ----------------
    legacy_basis: Mapped[float] = mapped_column(Float, nullable=False)
    normal_basis: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_aware_basis: Mapped[float] = mapped_column(Float, nullable=False)
    selected_basis: Mapped[float] = mapped_column(Float, nullable=False)
    ceiling: Mapped[float] = mapped_column(Float, nullable=False)
    absolute_delta: Mapped[float] = mapped_column(Float, nullable=False)
    relative_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling_semantics: Mapped[str] = mapped_column(String(60), nullable=False)

    decline_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    authority_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )

    __table_args__ = (
        UniqueConstraint(
            "trigger_observation_id",
            "capacity_axis",
            "decline_policy_version",
            name="uq_strength_decline_shadow_trigger_axis_policy",
        ),
    )
