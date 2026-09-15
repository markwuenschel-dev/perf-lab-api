"""The confidence band is one Literal, and the published contract says so.

Before this was typed, the canonical three-value band was declared as bare ``str``
in two schemas: OpenAPI emitted ``string``, and the web re-declared the enum by
hand. Nothing failed when the two drifted — the divergence only showed up at
runtime, as a band the frontend silently narrowed away.

These tests are the guard against that returning. They assert the *published
contract*, not the Python type, because the contract is what the web generates
from — a Literal that stopped reaching OpenAPI would be invisible to a test that
only introspected the annotation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest

from app.logic.confidence_presentation import (
    ESTABLISHED_MAX_VARIANCE,
    PROVISIONAL_MAX_VARIANCE,
    ConfidenceStatus,
    confidence_status,
)
from app.main import app

EXPECTED = ["established", "provisional", "insufficient"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _schemas() -> dict[str, Any]:
    return app.openapi()["components"]["schemas"]


def test_literal_and_derivation_agree_on_the_value_set() -> None:
    """The type and the function that produces it cannot drift apart."""
    assert list(get_args(ConfidenceStatus)) == EXPECTED

    produced = {
        confidence_status(ESTABLISHED_MAX_VARIANCE),
        confidence_status(PROVISIONAL_MAX_VARIANCE),
        confidence_status(PROVISIONAL_MAX_VARIANCE + 1.0),
    }
    assert produced == set(EXPECTED)
    # Every band the function can return is a member of the declared Literal.
    assert produced <= set(get_args(ConfidenceStatus))


def test_assessment_card_publishes_an_enum_not_a_bare_string() -> None:
    prop = _schemas()["AssessmentBenchmarkCard"]["properties"]["confidence_status"]
    # Nullable, so it arrives as anyOf[enum, null] rather than a top-level enum.
    variants = prop["anyOf"]
    enums = [v["enum"] for v in variants if "enum" in v]
    assert enums == [EXPECTED], f"expected one enum variant, got {prop}"
    assert {"type": "null"} in variants, "the band must stay nullable (fresh onramp)"


def test_onboarding_summary_publishes_the_same_enum() -> None:
    """The third publisher of the band, and the one that was missed first time.

    `overall_confidence` is the worst-axis rollup — the same canonical band, from
    the same `confidence_status()` call. It stayed `str` after the other two were
    typed, so the contract published the band two different ways at once.
    """
    prop = _schemas()["OnboardingTwinSummary"]["properties"]["overall_confidence"]
    enums = [v["enum"] for v in prop["anyOf"] if "enum" in v]
    assert enums == [EXPECTED], f"expected one enum variant, got {prop}"


def test_every_publisher_of_the_band_agrees(  # noqa: D103
) -> None:
    """No schema may publish this band as a bare string. Enumerated, not sampled."""
    schemas = _schemas()
    offenders: list[str] = []
    for name, schema in schemas.items():
        for field, prop in (schema.get("properties") or {}).items():
            if "confidence" not in field:
                continue
            blob = json.dumps(prop)
            if "established" not in blob:
                continue  # a different confidence concept (e.g. a numeric score)
            if "enum" not in blob:
                offenders.append(f"{name}.{field}")
    assert offenders == [], f"these publish the band without an enum: {offenders}"


def test_state_history_publishes_an_enum_for_every_axis() -> None:
    prop = _schemas()["StateHistorySnapshotRead"]["properties"]["capacity_confidence_status"]
    assert prop["additionalProperties"]["enum"] == EXPECTED, (
        "per-axis bands must be enum-typed; a bare string here is what let the "
        "web hand-mirror the values"
    )


@pytest.mark.parametrize(
    "schema_name,pointer",
    [
        ("AssessmentBenchmarkCard", ("properties", "confidence_status")),
        ("AssessmentBenchmarkCard", ("properties", "description")),
        ("StateHistorySnapshotRead", ("properties", "capacity_confidence_status")),
        ("OnboardingTwinSummary", ("properties", "overall_confidence")),
    ],
)
def test_committed_contract_is_not_stale(schema_name: str, pointer: tuple[str, ...]) -> None:
    """The committed openapi.json matches what the app currently produces.

    `web/src/types.gen.ts` is generated from the committed file, not from a live
    app, so a regenerated schema that was never committed would leave the web on
    the old shape with every Python-side test still green.
    """
    committed = json.loads((REPO_ROOT / "openapi.json").read_text(encoding="utf-8"))
    live_node: Any = _schemas()[schema_name]
    committed_node: Any = committed["components"]["schemas"][schema_name]
    for key in pointer:
        live_node = live_node[key]
        committed_node = committed_node[key]
    assert committed_node == live_node, (
        f"openapi.json is stale for {schema_name}.{pointer[-1]} — "
        "run `python -m app.scripts.export_openapi` and commit the result"
    )
