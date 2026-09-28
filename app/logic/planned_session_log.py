"""Planned session -> synthetic ``WorkoutLog`` for the C1b planned-week projection (ADR-0073).

A ``PlannedSession`` carries no dose: it is a slot (domain, category, deload/benchmark flags)
whose content is resolved on the day. To project the week forward through the real engine,
each pending session is turned into the synthetic log the dose law can consume, using the
same "planned intent -> WorkoutLog" bridge the forward projection and the shadow MPC use
(``app.engine.simulate.session_log_from_intent``).

Every number here is an ESTIMATE of a session that has not happened, and says which kind:

* ``basis == "prescribed"`` — a prescription has been stored on the session (it was opened);
  its RPE caps set the intensity band.
* ``basis == "template_estimate"`` — nothing prescribed yet; intensity is the balanced band.

Duration is never read from the prescription: it comes from the block's
``target_session_minutes`` (else the modality baseline), scaled by ``deload_volume_factor``
on a deload session. Display-only — the result must never feed scoring (ADR-0064).

Pure: no DB, no clock. The caller supplies ``when``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from app.engine.simulate import INTENSITY_BANDS, SESSION_BASELINES, session_log_from_intent
from app.logic.mpc.candidate_dose import modality_for_domain
from app.schemas.workouts import WorkoutLog

if TYPE_CHECKING:
    from app.models.mesocycle import MesocycleBlock, PlannedSession

SessionBasis = Literal["prescribed", "template_estimate"]

# Band used when nothing is prescribed, or the prescription carries no RPE cap.
_DEFAULT_INTENSITY = "balanced"


def prescribed_rpe(prescribed_content: dict[str, Any] | None) -> float | None:
    """The prescribed session RPE: the max exercise ``rpe_cap`` in a stored prescription.

    ``prescribed_content`` is the JSONB written by ``WorkoutPrescription.to_prescribed_content``
    (``exercises[].rpe_cap``, ``app/schemas/prescription.py``). None when nothing is prescribed
    or no exercise carries a numeric cap — never guessed.
    """
    if not prescribed_content:
        return None
    exercises = prescribed_content.get("exercises")
    if not isinstance(exercises, list):
        return None
    caps: list[float] = []
    for ex in cast(list[object], exercises):
        if not isinstance(ex, dict):
            continue
        cap = cast(dict[str, object], ex).get("rpe_cap")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool):
            caps.append(float(cap))
    return max(caps) if caps else None


def session_basis(session: PlannedSession) -> SessionBasis:
    """``prescribed`` once a prescription is stored on the session, else ``template_estimate``."""
    return "prescribed" if session.prescribed_content else "template_estimate"


def intensity_band_for_rpe(rpe: float | None) -> str:
    """The ``INTENSITY_BANDS`` key whose target RPE is nearest ``rpe`` (balanced when None).

    Ties resolve to the lower band — an estimate should not round effort up.
    """
    if rpe is None:
        return _DEFAULT_INTENSITY
    ordered = sorted(INTENSITY_BANDS.items(), key=lambda kv: kv[1]["rpe"])
    return min(ordered, key=lambda kv: abs(kv[1]["rpe"] - rpe))[0]


def session_modality(session: PlannedSession, block: MesocycleBlock) -> str:
    """Projection modality from the session's domain, falling back to the block goal.

    ``PlannedSession.modality`` is a lossy display label; the domain is what the prescriber
    keys on, and a NULL domain falls back to the block goal exactly as prescription does.
    """
    domain = session.domain or block.goal.value
    return modality_for_domain(domain)


def planned_duration_minutes(session: PlannedSession, block: MesocycleBlock, modality: str) -> float:
    """Block target minutes (else the modality baseline), x deload factor on a deload session."""
    base = float(SESSION_BASELINES.get(modality, SESSION_BASELINES["Mixed"])["duration_minutes"])
    minutes = float(block.target_session_minutes) if block.target_session_minutes else base
    if session.is_deload:
        minutes *= float(block.deload_volume_factor)
    return minutes


def planned_session_to_log(
    session: PlannedSession, block: MesocycleBlock, when: datetime
) -> WorkoutLog:
    """The synthetic log a pending planned session is projected as, timestamped ``when``."""
    modality = session_modality(session, block)
    base = float(SESSION_BASELINES.get(modality, SESSION_BASELINES["Mixed"])["duration_minutes"])
    minutes = planned_duration_minutes(session, block, modality)
    intensity = intensity_band_for_rpe(prescribed_rpe(session.prescribed_content))
    return session_log_from_intent(
        when, modality, scale=minutes / base, intensity=intensity, recovery="standard"
    )
