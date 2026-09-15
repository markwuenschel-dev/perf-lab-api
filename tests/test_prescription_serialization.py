"""Unit tests for WorkoutPrescription.to_prescribed_content() — pure, no DB.

Guards the single prescribe-and-persist serializer against drift from the
hand-written `prescribed_content` dicts it replaces (planning route + service),
the string keys the state_service reader depends on (ADR-0031 seeding), and the
JSONB write path: the column is written through plain ``json.dumps``, so the
content must already be JSON (N1 added a datetime to every explained exercise).
"""
import json
from datetime import UTC, datetime

import pytest

from app.schemas.prescription import (
    ExercisePrescription,
    LoadExplanation,
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


def _explained_rx() -> WorkoutPrescription:
    rx = _rx()
    rx.exercises[0].load_explanation = LoadExplanation(
        status="no_qualifying_evidence",
        reason="stale",
        benchmark_code="pl_e1rm_squat",
        evaluated_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        evidence_performed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    return rx


def test_to_prescribed_content_matches_the_json_mode_hand_written_dict():
    """The method must reproduce the dict the two call sites hand-wrote, in JSON mode."""
    rx = _explained_rx()
    legacy = {
        "type": rx.type,
        "focus": rx.focus,
        "rationale": rx.rationale,
        "duration_min": rx.duration_min,
        "model_version": rx.model_version,
        "exercises": [e.model_dump(mode="json") for e in rx.exercises],
        "why": rx.why.model_dump(mode="json") if rx.why else None,
    }
    assert rx.to_prescribed_content() == legacy


def test_an_explained_prescription_survives_the_jsonb_write_path():
    """The column is written with plain ``json.dumps``. A python-mode dump carries a
    datetime and cannot be written at all; the persisted form carries ISO strings and
    reads back to the same explanation."""
    rx = _explained_rx()
    with pytest.raises(TypeError):
        json.dumps(rx.model_dump())

    content = json.loads(json.dumps(rx.to_prescribed_content()))
    stored = content["exercises"][0]["load_explanation"]
    assert stored["evaluated_at"] == "2026-09-14T12:00:00Z"
    assert (
        WorkoutPrescription.model_validate(content).exercises[0].load_explanation
        == rx.exercises[0].load_explanation
    )


def test_a_prescription_stored_before_constraint_labels_still_loads():
    """``constraint_details`` was added after prescriptions were already persisted (S-A). A stored
    ``why`` without it must load, with no labels rather than invented ones."""
    content = _rx().to_prescribed_content()
    assert content["why"] is not None
    content["why"].pop("constraint_details")
    content["why"]["constraints_applied"] = ["block:benchmark"]

    loaded = WorkoutPrescription.model_validate(content)

    assert loaded.why is not None
    assert loaded.why.constraints_applied == ["block:benchmark"]
    assert loaded.why.constraint_details == []


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
