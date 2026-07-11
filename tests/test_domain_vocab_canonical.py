"""ADR-0057 — one canonical DomainCode vocabulary, three roles.

These are pure, DB-free guards. They fail CI the moment a seed row (or a
`domain_lenses` element) reintroduces a non-canonical spelling, and they pin the
`DomainCode` vocabulary invariants and the operational write-boundary behavior.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.logic.domain_vocab import (
    DOMAIN_ALIASES,
    DOMAINS,
    PRESCRIPTION_SUPPORTED_DOMAINS,
    canonical_domain,
    is_canonical_domain,
    is_prescription_supported,
    normalize_domain_at_boundary,
)
from app.scripts.seed_benchmarks import (
    BENCHMARKS,
    DERIVED_METRICS,
    SKILL_VIEW_METADATA,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The three folded home-domain spellings the seed used to carry.
_LEGACY_SPELLINGS = {"mixed_modal", "olympic_lifting", "sprinting"}
# ``sprinting`` legitimately survives as an *inbound goal* alias (Sprinting →
# running); only these two had no alias and were pure seed drift.
_UNALIASED_LEGACY = {"mixed_modal", "olympic_lifting"}


# --------------------------------------------------------------------------
# Vocabulary invariants
# --------------------------------------------------------------------------

def test_prescription_supported_is_subset_of_domains() -> None:
    assert PRESCRIPTION_SUPPORTED_DOMAINS <= DOMAINS
    # v1: prescription capability spans the whole vocabulary, but it is an
    # explicit declaration, not a live alias — the equality is asserted, so a
    # future DOMAINS addition without a matching capability edit fails here.
    assert PRESCRIPTION_SUPPORTED_DOMAINS == DOMAINS


def test_every_alias_target_is_canonical() -> None:
    assert set(DOMAIN_ALIASES.values()) <= DOMAINS


def test_legacy_home_domain_spellings_are_not_canonical() -> None:
    for legacy in _LEGACY_SPELLINGS:
        assert legacy not in DOMAINS


def test_folded_domain_spellings_gain_no_new_alias() -> None:
    # ADR-0057 rejected adding mixed_modal/olympic_lifting aliases (bakes the
    # drift back in). The pre-existing Sprinting goal alias is intentionally
    # exempt — it normalizes an inbound *goal*, not a persisted domain.
    for legacy in _UNALIASED_LEGACY:
        assert legacy not in DOMAIN_ALIASES


# --------------------------------------------------------------------------
# Owned seed data contains only canonical DomainCodes
# --------------------------------------------------------------------------

def test_benchmark_seed_home_domains_are_canonical() -> None:
    for row in BENCHMARKS:
        assert is_canonical_domain(str(row["domain"])), row["code"]


def test_derived_metric_seed_home_domains_are_canonical() -> None:
    for row in DERIVED_METRICS:
        assert is_canonical_domain(str(row["domain"])), row["code"]


def test_benchmark_seed_domain_lenses_are_canonical() -> None:
    # inline kwargs + the idempotent enrichment pass both carry lenses
    for row in BENCHMARKS:
        for lens in row.get("domain_lenses") or []:
            assert is_canonical_domain(str(lens)), (row["code"], lens)
    for code, meta in SKILL_VIEW_METADATA.items():
        for lens in meta.get("domain_lenses") or []:
            assert is_canonical_domain(str(lens)), (code, lens)


def test_coaching_template_seed_domains_are_canonical() -> None:
    raw: Any = json.loads(
        (_REPO_ROOT / "app" / "data" / "coaching_templates" / "bundled.json").read_text()
    )
    templates = cast(
        "list[dict[str, Any]]", raw if isinstance(raw, list) else raw.get("templates", [])
    )
    for tmpl in templates:
        if isinstance(tmpl, dict) and "domain" in tmpl:
            assert is_canonical_domain(str(tmpl["domain"])), tmpl.get("id") or tmpl["domain"]


# --------------------------------------------------------------------------
# Write-boundary normalization (operational Objective.domain)
# --------------------------------------------------------------------------

def test_normalize_boundary_folds_aliases_to_canonical() -> None:
    assert normalize_domain_at_boundary("Running") == "running"
    assert normalize_domain_at_boundary("Sprinting") == "running"
    assert normalize_domain_at_boundary("sprinting") == "running"
    assert normalize_domain_at_boundary("OlympicLifts") == "weightlifting"
    assert normalize_domain_at_boundary("MetCon") == "mixed"


def test_normalize_boundary_passes_none_and_canonical_through() -> None:
    assert normalize_domain_at_boundary(None) is None
    for d in DOMAINS:
        assert normalize_domain_at_boundary(d) == d


def test_normalize_boundary_rejects_unaliased_non_canonical() -> None:
    for bad in (*_UNALIASED_LEGACY, "not_a_domain", "swimming"):
        with pytest.raises(ValueError):
            normalize_domain_at_boundary(bad)


def test_prescription_capability_helper() -> None:
    assert is_prescription_supported("powerlifting")
    assert not is_prescription_supported("mixed_modal")
    # mixed_modal/olympic_lifting have no alias, so canonical_domain leaves them
    # untouched (they are caught by the reject path, not silently folded).
    assert canonical_domain("olympic_lifting") == "olympic_lifting"
