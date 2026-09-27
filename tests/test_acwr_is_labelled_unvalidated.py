"""ACWR is shown as a descriptive band, never as a validated injury-risk predictor (phase 8).

Nothing in this engine has checked the 0.8-1.3 band against injury outcomes (tissue_risk is
shadow-only). The API descriptions and the screens that show it must say so, and must not
reintroduce the "sweet spot" / "spike risk" framing.
"""

from __future__ import annotations

from pathlib import Path

from app.schemas.dashboard import TrainingLoadMetrics

ROOT = Path(__file__).resolve().parents[1]
SCREENS = (
    ROOT / "web/src/perflab/screens/HistoryScreen.tsx",
    ROOT / "web/src/perflab/screens/overview/AuthedOverview.tsx",
)


def test_the_api_says_the_band_is_an_unvalidated_heuristic() -> None:
    fields = TrainingLoadMetrics.model_fields
    assert "unvalidated heuristic" in (TrainingLoadMetrics.__doc__ or "")
    assert "not a validated injury-risk predictor" in (fields["acwr"].description or "")
    for edge in ("sweet_spot_low", "sweet_spot_high"):
        assert "unvalidated heuristic" in (fields[edge].description or "")


def test_screens_do_not_present_the_band_as_injury_risk() -> None:
    for path in SCREENS:
        text = path.read_text(encoding="utf-8").lower()
        for phrase in ("sweet spot", "spike risk", "injury-linked"):
            assert phrase not in text, f"{path.name}: {phrase!r}"
