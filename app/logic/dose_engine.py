"""The ONE production dose entry point.

Every code path whose dose drives athlete state or a recommendation imports
``calculate_stress_dose`` from HERE — never from a versioned engine module directly.

Why this module exists: the registry in ``app/logic/dose_model.py`` declared that production
is pinned to v0 and that an uncalibrated model can never become production. But nothing read
that declaration — every caller imported an engine module by name — so when a caller was left
importing ``dose_engine_v1`` after #230, the registry said "v0" while workout ingestion
computed v1. A pin that no caller consults is documentation, not enforcement.

Here the pin is enforced by construction:

* The production engine is resolved THROUGH ``select_production_dose_model()`` at import
  time, so a misconfigured registry fails loudly at startup rather than quietly per request.
* Activating a new model (phase 8C) is one edit to the registry; no caller changes.
* ``tests/test_production_dose_pinning.py`` scans ``app/`` and fails if any module other than
  the declared shadow/replay/calibration paths imports a versioned engine's
  ``calculate_stress_dose`` directly.

Paths that intentionally pin a specific version — replay of stored history, calibration
frames fitted under v0, the v1 shadow capture — keep importing that version explicitly, each
with its reason stated in place.
"""
from __future__ import annotations

from importlib import import_module

from app.logic.dose_model import select_production_dose_model

_PRODUCTION = select_production_dose_model()
_ENGINE = import_module(_PRODUCTION.module)

#: Which dose model production is computing with. Identical to the ``dose_model_version``
#: every production dose records, and checked against it in tests.
PRODUCTION_DOSE_MODEL_NAME: str = _PRODUCTION.name

calculate_stress_dose = _ENGINE.calculate_stress_dose
