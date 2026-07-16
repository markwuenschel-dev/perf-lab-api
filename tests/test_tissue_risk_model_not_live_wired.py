"""AUD-C9 invariant: the uncalibrated tissue-risk model must not feed live prescription scoring.

``app.logic.tissue_risk.compute_tissue_risk`` reports ``calibrated=False``. It may inform shadow
research (the MPC shadow objective) and offline training (``app/ml``), but an uncalibrated model
must not become live candidate authority — the ``ENABLE_TISSUE_RISK_CANDIDATE_PENALTY`` flag that
was meant to wire it live was removed as a dead gate, and nothing may quietly wire it in its
place. This asserts that the model is imported only by allowed shadow/offline modules; a live
importer (the prescriber, candidate_library, prescription_service, constraint-engine scoring)
fails the check.

Note: this is about the *model*. The separate ``tissue_t`` arithmetic in ``candidate_library`` is
a different, pre-existing live mechanism and is deliberately out of scope here.
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
TISSUE_MODEL = "compute_tissue_risk"

# Modules allowed to import the uncalibrated model: shadow research + offline ML only.
_ALLOWED_PREFIXES = ("logic/mpc/", "ml/")


def _importers_of_tissue_model() -> list[str]:
    importers: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        if path.name == "tissue_risk.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("tissue_risk"):
                if any(alias.name == TISSUE_MODEL for alias in node.names):
                    importers.append(path.relative_to(APP_DIR).as_posix())
    return importers


def test_tissue_model_is_imported_somewhere() -> None:
    """Guard the guard: if nothing imports the model, the allowlist check is vacuous."""
    assert _importers_of_tissue_model(), "no importer of compute_tissue_risk found — check is vacuous"


def test_uncalibrated_tissue_model_is_not_wired_into_live_scoring() -> None:
    live_importers = [
        module
        for module in _importers_of_tissue_model()
        if not module.startswith(_ALLOWED_PREFIXES)
    ]
    assert not live_importers, (
        "the uncalibrated tissue-risk model (calibrated=False) is imported by non-shadow, "
        f"non-offline module(s): {live_importers}. An uncalibrated model must not feed live "
        "candidate scoring — calibrate it and add a validated promotion path first."
    )
