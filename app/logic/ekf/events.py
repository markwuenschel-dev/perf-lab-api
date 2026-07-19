"""Canonical EKF shadow-log event vocabulary + the "original wellness assimilation" predicate.

``event_type`` names the EKF *transition operator*; the observation *source* is carried
independently by ``source_wellness_sample_id``. The two dimensions are orthogonal (AUD-C8
head-correction replay, Q9):

    event_type  source_id   meaning
    ----------  ---------   -------------------------------
    predict     NULL        ordinary covariance propagation (workout)
    update      NULL        non-wellness measurement update (benchmark)
    update      non-NULL    original wellness assimilation
    replay      non-NULL    corrected wellness replay

Every query that means "the original wellness assimilation row" must reuse
``original_wellness_assimilation_clause`` rather than re-spelling the predicate, so the
conflict classifier, pending-correction lookup, reconciliation, and tests cannot drift apart.
The migration inlines the same literals (migrations stay replayable independent of app refactors).
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, and_

from app.models.ekf_shadow import EkfShadowLog

# Transition-operator vocabulary — centralized so no query hard-codes the literal.
EKF_EVENT_PREDICT = "predict"
EKF_EVENT_UPDATE = "update"
EKF_EVENT_REPLAY = "replay"


def original_wellness_assimilation_clause() -> ColumnElement[bool]:
    """SQL predicate selecting original wellness-assimilation rows only (never replay rows)."""
    return and_(
        EkfShadowLog.source_wellness_sample_id.isnot(None),
        EkfShadowLog.event_type == EKF_EVENT_UPDATE,
    )


def wellness_replay_clause() -> ColumnElement[bool]:
    """SQL predicate selecting corrected wellness-replay rows only."""
    return and_(
        EkfShadowLog.source_wellness_sample_id.isnot(None),
        EkfShadowLog.event_type == EKF_EVENT_REPLAY,
    )
