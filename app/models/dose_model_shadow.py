"""DoseModelShadowLog — v1 computed beside production v0, for the phase-8 re-fit (8A).

One row per ingested workout. Production state and every recommendation are driven by the
v0 dose exactly as before; v1 is computed from the same log and stored here, never applied
(``decision_impact`` is always ``"none_shadow_only"``).

What the row is FOR: phase 8B fits v1's coefficients, and it needs to know where v1 differs
from v0 and why. So each row records not just the two doses but the provenance that says
whether a difference is a real correction or a missing input:

* ``v1_density_basis = "not_applicable"`` — density was not modelled for this session (a
  continuous effort, or no reported set count). A v1/v0 ratio there reflects the absence of an
  endurance-density input, not a corrected one. Phase 5 introduces
  ``structured_endurance_work``; these rows are how the fit will tell the two apart.
* ``v0_volume_sets_basis = "fabricated_fallback"`` — v0's volume includes the invented
  ``max(3, duration/12)`` set count. The fit should exclude these by default.

State provenance matters as much as dose provenance: ``state_update_model`` records which
transition chronology produced the pre-session state, so observations made under two
orderings of decay and adaptation are never pooled by accident.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DoseModelShadowLog(Base):
    __tablename__ = "dose_model_shadow_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Timezone-aware from the start: a new table has no reason to join the naive-timestamp debt
    # that INT-15 is migrating away from (tests/test_no_new_naive_utcnow.py).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    # The workout event time the two doses are "as of".
    session_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- versions: which models and which code produced this observation ---------------
    v0_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    v1_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    state_update_model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prescription_engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Build identifier when the deploy provides one (APP_BUILD_SHA). None otherwise.
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- what the session was --------------------------------------------------------
    modality: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # The planned slot's canonical domain (a045) and category, when the log fulfilled one —
    # the workout family as the planner named it. None for unplanned sessions.
    planned_domain: Mapped[str | None] = mapped_column(String(40), nullable=True)
    planned_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    session_rpe: Mapped[float] = mapped_column(Float, nullable=False)
    # The work representation that was actually reported — None where it was not.
    reported_sets: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_volume_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_exercises: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_set_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- the two doses, summarized for cheap queries ---------------------------------
    v0_total: Mapped[float] = mapped_column(Float, nullable=False)
    v1_total: Mapped[float] = mapped_column(Float, nullable=False)
    # v1_total / v0_total; None when v0_total is zero (the ratio is undefined, not infinite).
    ratio_v1_v0: Mapped[float | None] = mapped_column(Float, nullable=True)

    v0_density_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    v0_density_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    v1_density_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    v1_density_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    v0_volume_sets_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    v1_volume_sets_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # --- fallbacks, as queryable booleans ---------------------------------------------
    # True when v1 did NOT model density for this session (basis "not_applicable").
    v1_density_not_modelled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True when v0's volume includes the fabricated max(3, duration/12) set count.
    v0_volume_used_fabricated_sets: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # --- full detail -------------------------------------------------------------------
    v0_dose_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    v1_dose_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The athlete's state ENTERING the session — the substrate both doses act on.
    state_before_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    decision_impact: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none_shadow_only"
    )
