"""What a dose-model fit may learn from, and how a session becomes a labelled pair (phase 8).

One source for the fit policy, read by both the 8A census report
(``app/scripts/dose_shadow_report.py``, which runs in the production image without pandas)
and the training frame (``app/ml/dose_calibration/build_training_frame.py``). If they each
kept a copy, the census could say "40 usable pairs" while the frame built 25.

Every rule here is part of ``FIT_POLICY_VERSION``. Changing any of them (an exclusion, the
pairing window, the baseline) changes what a fitted artifact was trained on, so the version
moves with it and activation (``app/logic/dose_model.py``) refuses an artifact fitted under an
older policy.

Pure Python on purpose: no pandas, no database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.logic.dose_model import density_fit_eligible

#: Bump on ANY change to the rules in this module.
FIT_POLICY_VERSION = "fit-policy-1"

# --- Which rows a fit may learn from ------------------------------------------------------

FitTier = Literal[
    #: v0's volume includes the invented max(3, duration/12) set count (backlog C2).
    "fabricated_sets",
    #: Density came from the linked PRESCRIPTION, not the performance (phase 5.4).
    "prescribed_proxy",
    #: v1 did not model density for this session (continuous effort, or no set count).
    "density_not_modelled",
    #: Everything a fit may learn from under this policy.
    "eligible",
]


def fit_tier(*, v0_volume_used_fabricated_sets: bool, v1_density_basis: str | None) -> FitTier:
    """Classify one shadow row. Only ``"eligible"`` rows enter a fit."""
    if v0_volume_used_fabricated_sets:
        return "fabricated_sets"
    if v1_density_basis == "prescribed_timed_work_over_elapsed":
        return "prescribed_proxy"
    if not density_fit_eligible(v1_density_basis):
        return "density_not_modelled"
    return "eligible"


#: Accounts created by the repo's seeders (``demo+…@perflab.local``). Their sessions are
#: synthetic, or real data paired with an invented athlete (seed_shadow_history.py:56-60), so
#: they may exercise the pipeline and never calibrate it.
SEEDED_EMAIL_SUFFIX = "@perflab.local"


def is_seeded_email(email: str | None) -> bool:
    return email is not None and email.lower().endswith(SEEDED_EMAIL_SUFFIX)


# --- How a session becomes a labelled pair ------------------------------------------------
#
# The label is the athlete's NEXT logged session RPE, centred on that athlete's own baseline:
# "a bigger session today makes the next one cost more". The baseline is CAUSAL: the mean RPE
# of the athlete's sessions strictly BEFORE this one. Centring on a mean that includes later
# sessions would hand a held-out athlete's future RPEs to the evaluation, which is the leak
# athlete-grouped validation exists to prevent. Without enough earlier sessions, the pair is
# unusable; it never borrows from the future to make up the difference.

#: The next session must start at least this many whole days later (same-day doubles excluded).
MIN_SESSION_GAP_DAYS = 1
#: Beyond this gap the residual fatigue has cleared and the next RPE says nothing about today.
MAX_SESSION_GAP_DAYS = 4
#: Earlier sessions needed before an athlete's baseline is usable.
#: [assumed] 3: the smallest mean that is not dominated by one session. Versioned with the policy.
MIN_PRIOR_SESSIONS = 3
#: Held-out athletes with fewer labelled pairs than this are "sparse" in evaluation
#: (``app/ml/dose_calibration/evaluate.py``). The census reports how many athletes clear it.
MIN_PAIRS_PER_HELD_OUT_ATHLETE = 10

PairStatus = Literal[
    "labelled",
    "no_next_session",
    "same_day",
    "gap_too_long",
    "insufficient_prior_history",
]


@dataclass(frozen=True)
class LoggedSession:
    workout_log_id: int
    user_id: int
    at: datetime
    session_rpe: float


@dataclass(frozen=True)
class SessionPair:
    workout_log_id: int
    user_id: int
    status: PairStatus
    #: Whole days to the next session, floored; None without a next session.
    gap_days: int | None
    next_session_rpe: float | None
    #: How many of this athlete's sessions came strictly before this one.
    n_prior_sessions: int
    #: Mean RPE of those earlier sessions; None when there are none.
    causal_baseline_rpe: float | None

    @property
    def label(self) -> float | None:
        """Next-session RPE minus the causal baseline; None unless the pair is labelled."""
        if self.status != "labelled":
            return None
        assert self.next_session_rpe is not None and self.causal_baseline_rpe is not None
        return self.next_session_rpe - self.causal_baseline_rpe


def _whole_days(earlier: datetime, later: datetime) -> int:
    """Floor of elapsed days, the same quantity as pandas' ``Timedelta.days``."""
    return int((later - earlier).total_seconds() // 86400)


def pair_sessions(sessions: Iterable[LoggedSession]) -> dict[int, SessionPair]:
    """Pair every logged session with the athlete's next one. Keyed by ``workout_log_id``.

    ``sessions`` must be ALL of the athletes' logged sessions, not only shadow rows: the next
    session and the baseline come from everything the athlete logged. Order within an athlete
    is ``(at, workout_log_id)``, so equal timestamps resolve deterministically.
    """
    by_user: dict[int, list[LoggedSession]] = defaultdict(list)
    for s in sessions:
        by_user[s.user_id].append(s)

    out: dict[int, SessionPair] = {}
    for user_id, rows in by_user.items():
        rows.sort(key=lambda s: (s.at, s.workout_log_id))
        running_sum = 0.0
        for i, s in enumerate(rows):
            baseline = running_sum / i if i else None
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            gap = _whole_days(s.at, nxt.at) if nxt is not None else None
            status: PairStatus
            if nxt is None or gap is None:
                status = "no_next_session"
            elif gap < MIN_SESSION_GAP_DAYS:
                status = "same_day"
            elif gap > MAX_SESSION_GAP_DAYS:
                status = "gap_too_long"
            elif i < MIN_PRIOR_SESSIONS:
                status = "insufficient_prior_history"
            else:
                status = "labelled"
            out[s.workout_log_id] = SessionPair(
                workout_log_id=s.workout_log_id,
                user_id=user_id,
                status=status,
                gap_days=gap,
                next_session_rpe=nxt.session_rpe if nxt is not None else None,
                n_prior_sessions=i,
                causal_baseline_rpe=baseline,
            )
            running_sum += s.session_rpe
    return out


def pairing_rule() -> dict[str, object]:
    """The pairing rule as data, for an artifact's receipt."""
    return {
        "label": "next_session_rpe_minus_causal_baseline",
        "baseline": "mean_rpe_of_strictly_earlier_sessions",
        "min_prior_sessions": MIN_PRIOR_SESSIONS,
        "gap_days": [MIN_SESSION_GAP_DAYS, MAX_SESSION_GAP_DAYS],
        "gap_measure": "floor_elapsed_days",
    }
