from datetime import UTC, datetime

from app.models.telemetry import (
    CandidateDecisionLog,
    OutcomeEvent,
    PainReport,
    PrescriptionDecision,
    SessionFeedback,
)


def test_prescription_decision_defaults():
    pd = PrescriptionDecision(
        athlete_id=1, goal="Strength", algorithm_version="v0", decision_mode="adaptive",
    )
    assert pd.decision_mode == "adaptive"
    assert pd.chosen_score is None


def test_candidate_decision_log_defaults():
    cdl = CandidateDecisionLog(
        prescription_decision_id=1, branch_id="readiness_cns",
        candidate_type="Metabolic Conditioning", source="redirect",
    )
    assert cdl.hard_failed is False
    assert cdl.chosen is False


def test_session_feedback_defaults():
    sf = SessionFeedback(planned_session_id=42, status="skipped")
    assert sf.modified_volume is False
    assert sf.pain_flag is False
    assert sf.followed_as_prescribed is None


def test_pain_report_axes():
    valid_axes = {"shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger", "other"}
    pr = PainReport(
        athlete_id=1, reported_at=datetime.now(UTC),
        tissue_axis="knee", severity_0_10=4.0, affected_training=True, onset="gradual",
    )
    assert pr.tissue_axis in valid_axes


def test_outcome_event_types():
    valid_types = {
        "pain_event", "tissue_skip", "non_tissue_skip", "unknown_skip",
        "tissue_modified", "non_tissue_modified", "forced_deload", "planned_deload",
        "benchmark_underperformance", "excessive_fatigue",
    }
    oe = OutcomeEvent(
        athlete_id=1, occurred_at=datetime.now(UTC),
        event_type="unknown_skip", confidence=0.5,
    )
    assert oe.event_type in valid_types
    assert oe.confidence == 0.5
