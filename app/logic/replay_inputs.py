"""What a state transition was given, as a JSON snapshot (P3b-1).

Pure builders for the ``replay_input`` columns on ``workout_logs`` and
``benchmark_observations``. A snapshot is the operator's arguments *as it saw them*, taken at
the moment of the transition, so a later replay never has to consult a row that can change
afterwards (benchmark definitions and mappings, the exercise catalog, the profile).

Both builders return plain JSON values only: the columns are JSONB, and a non-finite float
is refused rather than stored (JSONB cannot hold one, and a NaN input would make a replay
meaningless).

``v`` versions the layout. A reader that does not know a version must treat the row as not
replayable.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.logic.state_transitions import BenchmarkOperatorInput

REPLAY_INPUT_VERSION = 1

# How the benchmark state operator was used for the observation.
EVALUATION_APPLIED = "applied"  # run against the head, in time order (a row may still be suppressed)
EVALUATION_RECORD_ONLY = "record_only"  # dated before the head: kept as evidence, not evaluated
EVALUATION_NOT_EVALUATED = "not_evaluated"  # no state authority, or invalid, or no mappings


def _finite(value: Any, what: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"replay input {what} is not finite: {value!r}")
    return value


def workout_replay_input(
    *,
    modality: str,
    dominant_movement_pattern: str | None,
    sleep_quality: float | None,
    life_stress_inverse: float | None,
    dose: Mapping[str, Any],
) -> dict[str, Any]:
    """The workout operator's non-state arguments: the final log fields it reads and the
    dose actually used (not recomputed from the catalog on replay)."""
    return {
        "v": REPLAY_INPUT_VERSION,
        "kind": "workout",
        "modality": modality,
        "dominant_movement_pattern": dominant_movement_pattern,
        "sleep_quality": _finite(sleep_quality, "sleep_quality"),
        "life_stress_inverse": _finite(life_stress_inverse, "life_stress_inverse"),
        "dose": dict(dose),
    }


_MAPPING_FIELDS = (
    "id", "target_vector", "target_key", "mapping_type",
    "coefficient", "intercept", "min_value", "max_value", "config",
)


def snapshot_mapping(mapping: Any) -> dict[str, Any]:
    """One observation-mapping row as the operator reads it."""
    out = {name: getattr(mapping, name) for name in _MAPPING_FIELDS}
    for name in ("coefficient", "intercept", "min_value", "max_value"):
        _finite(out[name], f"mapping.{name}")
    return out


@dataclass(frozen=True)
class MappingSnapshot:
    """A mapping row rebuilt from a snapshot. The operators read it by attribute only."""

    id: int
    target_vector: str
    target_key: str
    mapping_type: str
    coefficient: float
    intercept: float
    min_value: float | None
    max_value: float | None
    config: dict[str, Any] | None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MappingSnapshot:
        return cls(**{name: raw[name] for name in _MAPPING_FIELDS})


def benchmark_replay_input(
    op: BenchmarkOperatorInput,
    *,
    effect: str,
    authority_policy_version: str | None,
    evaluation: str,
    decline: Mapping[str, Any] | None,
    state_row_written: bool,
    predecessor_state_id: int | None,
) -> dict[str, Any]:
    """The benchmark operator's arguments and what happened around it.

    * ``observation_weight_used`` is the definition's weight, the one the operator reads. It is
      not the observation's own ``observation_weight`` column, which is the request's.
    * ``mappings`` are kept in the order they were applied.
    * ``decline`` is the strength-decline machine's decision, or None when it did not run.
    * ``state_row_written`` is False when the transition produced no row (a suppressed
      capacity update, a no-op floor).
    """
    return {
        "v": REPLAY_INPUT_VERSION,
        "kind": "benchmark",
        "observed_at": op.observed_at.isoformat(),
        "raw_value": _finite(op.raw_value, "raw_value"),
        "normalized_value": _finite(op.normalized_value, "normalized_value"),
        "score01": _finite(op.score01, "score01"),
        "better_direction": op.better_direction,
        "observation_weight_used": _finite(op.observation_weight_used, "observation_weight_used"),
        "mappings": [snapshot_mapping(m) for m in op.mappings],
        "effect": effect,
        "authority_policy_version": authority_policy_version,
        "evaluation": evaluation,
        "decline": dict(decline) if decline is not None else None,
        "state_row_written": state_row_written,
        "predecessor_state_id": predecessor_state_id,
    }


def operator_input_from_snapshot(snap: Mapping[str, Any]) -> BenchmarkOperatorInput:
    """Rebuild the benchmark operator's input from a ``benchmark`` snapshot."""
    if snap.get("v") != REPLAY_INPUT_VERSION or snap.get("kind") != "benchmark":
        raise ValueError("not a version-1 benchmark snapshot")
    return BenchmarkOperatorInput(
        observed_at=datetime.fromisoformat(snap["observed_at"]),
        raw_value=snap["raw_value"],
        normalized_value=snap["normalized_value"],
        score01=snap["score01"],
        better_direction=snap["better_direction"],
        observation_weight_used=snap["observation_weight_used"],
        mappings=[MappingSnapshot.from_dict(m) for m in snap["mappings"]],
    )
