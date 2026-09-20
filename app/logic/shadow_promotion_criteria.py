"""What each shadow subsystem would need before it could earn production authority.

The project's rule is that advanced estimators must not remain permanent shadow
demonstrations: each needs explicit calibration, replay, promotion, canary, rollback and
recertification criteria, and authority must be *earned* through evidence.

Those criteria existed only as ADR prose. Prose cannot be checked, so the real state had
to be rediscovered by grep — which is how ADR-0041's calibration gate sat with zero callers
without anyone noticing, and how four ADRs came to describe canary/rollback machinery that
was never written.

This module makes the criteria data instead. Each subsystem declares, per criterion, either
the symbol that implements it or ``None`` with a reason. ``tests/test_shadow_promotion_criteria.py``
enforces two things that keep this honest:

1. **No aspiration.** Every ``implemented_by`` must resolve to a real importable symbol, so
   a criterion cannot be marked done by writing a plausible dotted path.
2. **No silence.** Every shadow service on disk must appear here, and every unimplemented
   criterion must carry a reason. Adding a new shadow subsystem fails CI until its promotion
   story is declared — which is the point: a shadow estimator with no stated path to
   promotion is how you get seven permanent demonstrations.

This registry confers no authority. It records what would be required; it does not promote
anything, and nothing reads it to make a decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The six criteria the project requires before a shadow estimator can take authority.
CRITERIA_NAMES: Final[tuple[str, ...]] = (
    "calibration",
    "replay",
    "promotion_path",
    "canary",
    "rollback",
    "recertification",
)


@dataclass(frozen=True)
class Criterion:
    """One promotion criterion's real state.

    ``implemented_by`` is a dotted path to the symbol that actually implements it, verified
    importable by the test. ``note`` explains what is missing when it is ``None`` - required,
    so "not done" always comes with a reason rather than a blank.
    """

    implemented_by: str | None
    note: str

    @property
    def implemented(self) -> bool:
        return self.implemented_by is not None


def _missing(note: str) -> Criterion:
    return Criterion(implemented_by=None, note=note)


def _done(symbol: str, note: str) -> Criterion:
    return Criterion(implemented_by=symbol, note=note)


@dataclass(frozen=True)
class SubsystemCriteria:
    """Every promotion criterion for one shadow subsystem."""

    subsystem: str
    service_module: str
    adr: str
    calibration: Criterion
    replay: Criterion
    promotion_path: Criterion
    canary: Criterion
    rollback: Criterion
    recertification: Criterion

    def criterion(self, name: str) -> Criterion:
        value = getattr(self, name)
        assert isinstance(value, Criterion)
        return value

    @property
    def implemented_count(self) -> int:
        return sum(1 for n in CRITERIA_NAMES if self.criterion(n).implemented)


_NO_CALIBRATION = "no calibration math exists for this subsystem at all"
_NO_REPLAY = "no harness re-runs this estimator over an athlete's real history"
_NO_PROMOTION = (
    "no production OFF/ON path. app/engine/feature_flags.py records that five ENABLE_* "
    "booleans were deleted in AUD-C9 because a flag read by no production code is a "
    "fictional gate; re-adding one here without a live branch would repeat that"
)
_NO_CANARY = "no staged-rollout mechanism exists anywhere in the repo"
_NO_ROLLBACK = "nothing to roll back to - there is no promoted state"
_NO_RECERT = "no scheduled check would notice this estimator drifting"


CRITERIA: Final[dict[str, SubsystemCriteria]] = {
    "ekf_shadow_service": SubsystemCriteria(
        subsystem="EKF (full-covariance belief)",
        service_module="app.services.ekf_shadow_service",
        adr="ADR-0041",
        calibration=_done(
            "app.services.ekf_calibration_gate_service.evaluate_ekf_calibration_gate",
            "NIS chi-squared over production ekf_shadow_log rows. PARTIAL: ADR-0041 names "
            "two arms and interval coverage is unmeasurable from the log, which stores no "
            "predictive std",
        ),
        replay=_missing(
            "ekf_replay.run_replay is DB-free/synthetic; the production path in "
            "ekf_shadow_service is head data-repair, not estimator re-validation"
        ),
        promotion_path=_missing(_NO_PROMOTION),
        canary=_missing(_NO_CANARY),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(
            "the calibration gate runs only when invoked by hand; nothing schedules it"
        ),
    ),
    "mpc_shadow_service": SubsystemCriteria(
        subsystem="MPC (receding-horizon planner)",
        service_module="app.services.mpc_shadow_service",
        adr="ADR-0042",
        calibration=_missing(_NO_CALIBRATION),
        replay=_missing(
            "ADR-0042 names a shadow-vs-live replay harness as a prerequisite; it does not exist"
        ),
        promotion_path=_missing(_NO_PROMOTION),
        canary=_missing("ADR-0042 names canary + rollback; neither is implemented"),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(_NO_RECERT),
    ),
    "personalization_shadow_service": SubsystemCriteria(
        subsystem="Personalization (hierarchical theta_i)",
        service_module="app.services.personalization_shadow_service",
        adr="ADR-0043",
        calibration=_missing(
            "offline evaluation exists under app/ml/personalization but CI runs no app.ml "
            "step, so it gates nothing"
        ),
        replay=_missing(
            "ADR-0043 names longitudinal trajectory replay as one of four gates; none of the four exist"
        ),
        promotion_path=_missing(_NO_PROMOTION),
        canary=_missing(_NO_CANARY),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(_NO_RECERT),
    ),
    "recovery_shadow_service": SubsystemCriteria(
        subsystem="Recovery priors (Q2 clearance)",
        service_module="app.services.recovery_shadow_service",
        adr="ADR-0043",
        calibration=_missing(_NO_CALIBRATION),
        replay=_missing(_NO_REPLAY),
        promotion_path=_missing(
            "parameter_overrides.apply_parameter_overrides(allow_shadow=True) is an opt-in "
            "artifact loader, not a promotion gate; its own docstring says shadow artifacts "
            "can never reach production"
        ),
        canary=_missing(_NO_CANARY),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(_NO_RECERT),
    ),
    "capacity_floor_shadow_service": SubsystemCriteria(
        subsystem="Capacity floor ratchet (upward_lower_bound)",
        service_module="app.services.capacity_floor_shadow_service",
        adr="ADR-0058",
        calibration=_missing(_NO_CALIBRATION),
        replay=_missing(_NO_REPLAY),
        promotion_path=_missing(
            "observation_authority.FLOOR_NOT_APPLIED_DEFERRED is a marker string stamped on "
            "rows, not a gate. ADR-0058 names idempotency proof, bounded-uplift guards, "
            "canary and rollback; only the deferral marker and the evidence rows exist"
        ),
        canary=_missing(_NO_CANARY),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(_NO_RECERT),
    ),
    "dose_model_shadow_service": SubsystemCriteria(
        subsystem="Dose model v1 (density = work per elapsed time)",
        service_module="app.services.dose_model_shadow_service",
        adr="phase 8 of the engine-coherence plan (8A capture / 8B fit / 8C activate)",
        calibration=_missing(
            "phase 8B: v1's density variable changed meaning, so dose_beta and every "
            "density-dependent coefficient must be re-fit against logged sessions this service "
            "captures. The synthetic grid (app/scripts/compare_dose_models.py) shows where the "
            "models diverge; it is not calibration and must not be fitted against"
        ),
        replay=_missing(
            "v1 has no replay harness; stored states replay under v0 (ekf_replay pins v0), and "
            "8C needs held-out longitudinal replay of v1 before activation"
        ),
        promotion_path=_done(
            "app.logic.dose_model.select_production_dose_model",
            "activation requires a calibration identifier in the DOSE_MODELS registry, and "
            "every live caller resolves through it via app.logic.dose_engine — enforced by "
            "tests/test_production_dose_pinning.py rather than by convention",
        ),
        canary=_missing(_NO_CANARY),
        rollback=_missing(
            "reverting PRODUCTION_DOSE_MODEL in app/logic/dose_model.py is a one-line manual "
            "rollback; nothing automates it or detects the need for it. NOTE: one activation "
            "gate is already implemented and permanent — workload monotonicity, "
            "tests/properties/test_v1_directional_ordering.py: for otherwise-identical "
            "generated strength sessions, more prescribed sets must carry more dose. v0 fails "
            "it (2.89/2.01/1.55, docs/simulations/phase-1.md); v1 must keep passing it to be "
            "activated"
        ),
        recertification=_missing(_NO_RECERT),
    ),
    "dose_routing_shadow_service": SubsystemCriteria(
        subsystem="Dose routing (k_X constants)",
        service_module="app.services.dose_routing_shadow_service",
        adr="ADR-0054",
        calibration=_missing(
            "dose_routing_calibration derives k_X constants from simulation scenarios; it is "
            "constant derivation, not predictive-error calibration, and its own docstring "
            "notes there is no first-party historical session corpus"
        ),
        replay=_missing(_NO_REPLAY),
        promotion_path=_missing(_NO_PROMOTION),
        canary=_missing(_NO_CANARY),
        rollback=_missing(_NO_ROLLBACK),
        recertification=_missing(_NO_RECERT),
    ),
}


def summary() -> dict[str, object]:
    """Fleet-wide promotion-readiness, for a CLI or a status board."""
    total = len(CRITERIA) * len(CRITERIA_NAMES)
    implemented = sum(c.implemented_count for c in CRITERIA.values())
    by_criterion = {
        name: sum(1 for c in CRITERIA.values() if c.criterion(name).implemented)
        for name in CRITERIA_NAMES
    }
    return {
        "subsystems": len(CRITERIA),
        "criteria_per_subsystem": len(CRITERIA_NAMES),
        "implemented": implemented,
        "total": total,
        "by_criterion": by_criterion,
        "fully_promotable": [
            key for key, c in CRITERIA.items() if c.implemented_count == len(CRITERIA_NAMES)
        ],
    }


def format_summary() -> str:
    """Plain-text promotion-readiness matrix."""
    lines = ["Shadow promotion criteria - what each subsystem still needs", ""]
    width = max(len(k) for k in CRITERIA)
    header = " " * (width + 2) + "  ".join(n[:6].ljust(6) for n in CRITERIA_NAMES)
    lines.append(header)
    for key, c in sorted(CRITERIA.items()):
        cells = "  ".join(
            ("yes" if c.criterion(n).implemented else "-").ljust(6) for n in CRITERIA_NAMES
        )
        lines.append(f"{key.ljust(width)}  {cells}")
    s = summary()
    lines.append("")
    lines.append(f"  implemented: {s['implemented']}/{s['total']} criteria")
    lines.append(f"  fully promotable: {s['fully_promotable'] or 'none'}")
    return "\n".join(lines)
