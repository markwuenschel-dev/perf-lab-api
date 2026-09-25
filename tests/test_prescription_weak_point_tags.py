"""A prescribed exercise must say which of the athlete's deficits it addresses.

`ExercisePrescription.weak_point_tags` advertised exactly that linkage and was never
assigned at any of its three construction sites, so it was always `[]`. The field claimed
something the response never carried, which is why "which goals are being advanced" scored
weakest of the eight explanations.

The distinction these tests pin: the value is the INTERSECTION of the exercise's catalog
tags with the athlete's ACTIVE weak points, not the raw catalog list. The catalog says what
an exercise can address in general; the intersection says what it addresses for this
athlete, which is the question being asked.
"""

from app.models.exercise import Exercise
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.services.prescription_service import _enrich_exercises_with_weak_point_tags


async def _exercise(db, name: str, tags: list[str]) -> Exercise:
    ex = Exercise(
        name=name,
        modality="Strength",
        movement_pattern="squat",
        load_type="barbell",  # NOT NULL on the catalog table
        weak_point_tags=tags,
    )
    db.add(ex)
    await db.commit()
    return ex


def _rx(*names: str) -> WorkoutPrescription:
    return WorkoutPrescription(
        type="Strength",
        focus="Lower",
        rationale="r",
        duration_min=60,
        exercises=[ExercisePrescription(name=n, sets=5, reps="5") for n in names],
    )


async def test_only_the_athletes_active_weak_points_are_reported(async_db) -> None:
    """The catalog list is what the exercise CAN address; the athlete's list is what counts.

    Reporting the raw catalog tags would tell an athlete their squat addresses a deficit
    they do not have.
    """
    await _exercise(async_db, "Back Squat", ["anterior_chain", "grip", "squat_pattern"])
    rx = _rx("Back Squat")

    await _enrich_exercises_with_weak_point_tags(
        async_db, rx, ["grip", "posterior_chain"]
    )

    assert rx.exercises[0].weak_point_tags == ["grip"]


async def test_an_exercise_addressing_none_of_them_reports_an_empty_list(async_db) -> None:
    """Empty means "addresses none of your active weak points" - real information."""
    await _exercise(async_db, "Calf Raise", ["calves"])
    rx = _rx("Calf Raise")

    await _enrich_exercises_with_weak_point_tags(async_db, rx, ["grip"])

    assert rx.exercises[0].weak_point_tags == []


async def test_an_athlete_with_no_active_weak_points_gets_no_tags(async_db) -> None:
    """Nothing to intersect against, so nothing is claimed."""
    await _exercise(async_db, "Front Squat", ["anterior_chain"])
    rx = _rx("Front Squat")

    await _enrich_exercises_with_weak_point_tags(async_db, rx, [])

    assert rx.exercises[0].weak_point_tags == []


async def test_an_exercise_missing_from_the_catalog_is_left_alone(async_db) -> None:
    """A name with no catalog row must not raise, and must not invent a linkage."""
    rx = _rx("Some Unlisted Movement")

    await _enrich_exercises_with_weak_point_tags(async_db, rx, ["grip"])

    assert rx.exercises[0].weak_point_tags == []


async def test_each_exercise_is_resolved_independently(async_db) -> None:
    await _exercise(async_db, "Deadlift", ["posterior_chain", "grip"])
    await _exercise(async_db, "Leg Press", ["quads"])
    rx = _rx("Deadlift", "Leg Press")

    await _enrich_exercises_with_weak_point_tags(async_db, rx, ["grip", "quads"])

    by_name = {e.name: e.weak_point_tags for e in rx.exercises}
    assert by_name["Deadlift"] == ["grip"]
    assert by_name["Leg Press"] == ["quads"]


async def test_tags_are_ordered_deterministically(async_db) -> None:
    """A response field that reorders between identical requests is a diffing nuisance."""
    await _exercise(async_db, "Clean Pull", ["posterior_chain", "grip", "hip_hinge"])
    rx = _rx("Clean Pull")

    await _enrich_exercises_with_weak_point_tags(
        async_db, rx, ["hip_hinge", "grip", "posterior_chain"]
    )

    tags = rx.exercises[0].weak_point_tags
    assert tags == sorted(tags)
    assert tags == ["grip", "hip_hinge", "posterior_chain"]


async def test_a_prescription_with_no_exercises_is_a_no_op(async_db) -> None:
    rx = WorkoutPrescription(
        type="Recovery", focus="Mobility", rationale="r", duration_min=30
    )

    await _enrich_exercises_with_weak_point_tags(async_db, rx, ["grip"])

    assert rx.exercises == []


async def test_the_persisted_structure_carries_the_enriched_tags(
    async_db, seeded_exercise_catalog
) -> None:
    """The full pipeline, not the enricher alone.

    The structure used to be re-derived BETWEEN the load enricher and this one, so the
    structure persisted with a prescription carried pre-enrichment tags while ``exercises[]``
    carried the enriched ones: two views of one session that disagreed. Nothing re-validates
    stored content, so it never raised. The re-derivation now runs after both enrichers.
    """
    from app.models.user import AthleteProfile, User
    from app.models.weak_point import WeakPoint, WeakPointSource
    from app.schemas.prescription import project_exercises
    from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
    from app.services.prescription_service import prescribe_for_athlete
    from app.services.state_service import initialize_athlete_state

    user = User(email="wp-structure@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell", "dumbbells", "pullup_bar"]))
    for tag in ("hip_hinge", "squat_pattern", "push_horizontal", "push_vertical",
                "pull_vertical", "carry", "core_stability", "single_leg"):
        async_db.add(WeakPoint(user_id=user.id, tag=tag, source=WeakPointSource.SELF_REPORT))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    rx = await prescribe_for_athlete(async_db, user.id, TRAINING_GOAL_DEFAULT)

    # Load-bearing only if the enricher actually tagged something.
    assert any(ex.weak_point_tags for ex in rx.exercises), [e.name for e in rx.exercises]
    assert rx.structure is not None
    assert project_exercises(rx.structure) == rx.exercises
