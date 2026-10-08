"""Identity of the code and parameters that turn one persisted state into the next (P3b-1).

A replayed state is only the state the live system wrote if the same operators, running on the
same parameters, produced both. This module names that: a digest over the source of every
module the workout and benchmark state operators depend on, plus the engine parameters they
read, with the component digests kept beside the overall one so a mismatch can be explained.

What it covers, and what it leaves out, on purpose:

* **In:** the transitive ``app`` import closure of the operators and of ``state_transitions``,
  which the live writers and the replay share (``TRANSITION_MODULES``;
  an architecture test recomputes the closure from the source and fails if this tuple drifts),
  including the tissue-routing table ``phi_table`` that ``tissue_impulse_from_dose`` reads, and
  the canonical serialization of ``default_parameters()``, which the operators call internally.
* **Out:** dose computation and the exercise-catalog lookup. A replay reads the dose actually
  used from the stored snapshot instead of recomputing it. The strength-decline *decision*
  is out too: its outcome is captured per observation, and outcomes other than a passthrough
  are not replayable yet.
* **Not covered:** the numeric environment (libm, CPU). The reconstruction proof that replays the
  history without the new event and must reproduce the stored head is what catches drift there.

Source is hashed after normalizing encoding and line endings, so a checkout's newline style
cannot change the digest. A comment-only edit still changes it: losing replay eligibility after
such a deploy is accepted for v1.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
from functools import lru_cache
from typing import Any

from app.engine.parameters import default_parameters
from app.logic.state_update_v0 import STATE_UPDATE_MODEL_VERSION

IDENTITY_SCHEMA_VERSION = 1

# The roots the closure is computed from: the operators, and the shared transitions that wrap them.
TRANSITION_ROOTS: tuple[str, ...] = ("app.logic.state_transitions", "app.logic.state_update_v0")

# The transitive ``app`` import closure of ``TRANSITION_ROOTS``, sorted. Package ``__init__``
# modules are included: importing a submodule executes them.
TRANSITION_MODULES: tuple[str, ...] = (
    "app",
    "app.domain",
    "app.domain.vectors",
    "app.engine",
    "app.engine.parameters",
    "app.engine.phi_table",
    "app.engine.state_bridge",
    "app.logic",
    "app.logic.benchmark_validity",
    "app.logic.confidence_presentation",
    "app.logic.cross_talk",
    "app.logic.interference",
    "app.logic.state_transitions",
    "app.logic.state_update_v0",
    "app.schemas",
    "app.schemas.engine_vectors",
    "app.schemas.state",
    "app.schemas.workouts",
)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, and no NaN/Infinity (a non-finite
    parameter is an error, never a digest)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_source(raw: bytes) -> bytes:
    """UTF-8 (BOM stripped) with ``\\n`` line endings."""
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def source_digest(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin:
        raise RuntimeError(f"cannot locate the source of {module_name}")
    with open(spec.origin, "rb") as fh:
        return hashlib.sha256(normalize_source(fh.read())).hexdigest()


def parameters_digest() -> str:
    return hashlib.sha256(
        canonical_json(dataclasses.asdict(default_parameters())).encode("utf-8")
    ).hexdigest()


@dataclasses.dataclass(frozen=True)
class TransitionIdentity:
    digest: str
    components: dict[str, Any]


def identity_from(components: dict[str, Any]) -> TransitionIdentity:
    return TransitionIdentity(
        digest=hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest(),
        components=components,
    )


def compute_identity(
    modules: tuple[str, ...] = TRANSITION_MODULES,
) -> TransitionIdentity:
    return identity_from(
        {
            "schema": IDENTITY_SCHEMA_VERSION,
            "state_update_model": STATE_UPDATE_MODEL_VERSION,
            "modules": {name: source_digest(name) for name in modules},
            "parameters": parameters_digest(),
        }
    )


@lru_cache(maxsize=1)
def current_identity() -> TransitionIdentity:
    """The identity of the running process. Source files do not change under a live process."""
    return compute_identity()
