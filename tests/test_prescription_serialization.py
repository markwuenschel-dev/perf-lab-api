"""Unit tests for WorkoutPrescription.to_prescribed_content() — pure, no DB.

Guards the single prescribe-and-persist serializer against drift from the
hand-written `prescribed_content` dicts it replaces (planning route + service),
and the string keys the state_service reader depends on (ADR-0031 seeding).
"""
from app.schemas.prescription import (
    ExercisePrescription,
    PrescriptionExplanation,
    WorkoutPrescription,
)


def _rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="Strength",
        focus="Lower",
        rationale="fatigue is low, push volume",
        duration_min=60,
        model_version="v0.3",
        exercises=[
            ExercisePrescription(
                name="Back Squat",
                sets=3,
                reps="5",
                load_note="RPE 8",
                weak_point_tags=["quads"],
            )
        ],
        why=PrescriptionExplanation(goal_alignment="strength", state_drivers=["low fatigue"]),
    )


def test_to_prescribed_content_matches_legacy_hand_written_dict():
    """The method must reproduce the exact dict the two call sites hand-wrote."""
    rx = _rx()
    legacy = {
        "type": rx.type,
        "focus": rx.focus,
        "rationale": rx.rationale,
        "duration_min": rx.duration_min,
        "model_version": rx.model_version,
        "exercises": [e.model_dump() for e in rx.exercises],
        "why": rx.why.model_dump() if rx.why else None,
    }
    assert rx.to_prescribed_content() == legacy


def test_to_prescribed_content_why_none_serializes_to_none():
    rx = _rx()
    rx.why = None
    assert rx.to_prescribed_content()["why"] is None


def test_to_prescribed_content_preserves_reader_keys():
    """state_service._seed_exercises_from_prescription reads these by string key."""
    content = _rx().to_prescribed_content()
    assert {
        "type",
        "focus",
        "rationale",
        "duration_min",
        "model_version",
        "exercises",
        "why",
    } <= set(content)
    ex0 = content["exercises"][0]
    assert {"name", "sets", "reps"} <= set(ex0)
