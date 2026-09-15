"""Every seeded benchmark says what it measures and how to measure it (S-B, Assess help).

Where no measurement protocol is established, the benchmark says so in exactly those words —
this coverage check accepts that explicit marker and never pressures anyone into inventing
instructions to make it pass. Pure: reads the code-owned catalog, no database.
"""
from app.scripts.seed_benchmarks import (
    BENCHMARK_EXPLANATIONS,
    BENCHMARKS,
    PROTOCOL_NOT_YET_DEFINED,
    UNDEFINED_PROTOCOL_CODES,
)

SEEDED = {row["code"] for row in BENCHMARKS}


def test_every_seeded_benchmark_is_explained_and_no_unknown_code_is() -> None:
    assert set(BENCHMARK_EXPLANATIONS) == SEEDED


def test_each_explanation_is_a_description_and_a_separate_protocol() -> None:
    for code, text in BENCHMARK_EXPLANATIONS.items():
        assert set(text) == {"description", "protocol_summary"}, code
        assert text["description"].strip(), code
        assert text["protocol_summary"].strip(), code
        assert text["description"] != text["protocol_summary"], code
        assert len(text["description"]) <= 240, code
        assert len(text["protocol_summary"]) <= 300, code


def test_an_unestablished_protocol_says_so_and_only_those_do() -> None:
    assert UNDEFINED_PROTOCOL_CODES <= SEEDED
    for code, text in BENCHMARK_EXPLANATIONS.items():
        says_undefined = text["protocol_summary"] == PROTOCOL_NOT_YET_DEFINED
        assert says_undefined == (code in UNDEFINED_PROTOCOL_CODES), code


def test_the_explanation_text_has_one_source() -> None:
    """Inline text on a seed row would be a second copy that only fresh databases receive."""
    for row in BENCHMARKS:
        assert "description" not in row and "protocol_summary" not in row, row["code"]
