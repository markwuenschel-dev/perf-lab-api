"""Downward strength-decline decision policy (INT-02, ADR-0066).

A single transient low benchmark must never durably regress current strength.
This module is the **pure** numerical heart of the fix: it decides, from an
observation and the prior belief, whether a measured drop is *material* evidence
of decline, how a confirmed decline is applied, and how load is conservatively
constrained while a decline is unresolved. It performs no I/O and touches no
state — callers (`app.services.strength_decline_service`) own persistence.

Locked design (do not substitute):
- **asymmetric downward hysteresis** — a lower observation is preserved but does
  not rewrite canonical capacity on first evidence;
- **variance-aware materiality** — a drop is material only when it exceeds the
  *larger* of the protocol measurement error and the combined state/observation
  uncertainty;
- **confirmed regression via a bounded estimator** — a durable decline is applied
  as ``prior + K·(observation − prior)``, never by overwriting with the low value.

Explicitly rejected (see ADR-0066): an EWMA watermark deciding reality, and a
monotone floor on *current* capacity.

**Threshold provenance is versioned and, absent calibration, provisional.**
``strength_decline_policy_v1`` fallbacks are ``synthetic_and_expert_prior`` — they
are not empirically calibrated and must be shadowed before any retune. No global
percentage threshold is tuned here.

Unit contract: ``prior_mean``, ``observed_value``, the variances, and the resolved
measurement error must all be supplied in **one consistent value space** (raw e1RM
units, or normalized capacity-axis units). This module is unit-agnostic; the caller
guarantees the space is consistent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

POLICY_VERSION = "strength_decline_policy_v1"
CALIBRATION_BASIS_FALLBACK = "synthetic_and_expert_prior"

# --- Provisional, NON-calibrated fallback constants (synthetic_and_expert_prior) ---
# One-sided z for the uncertainty component (~95%).
DEFAULT_Z_DOWN = 1.64
# Within-athlete 1RM coefficient of variation used ONLY as the measurement-error
# fallback when no protocol MDC/SEM exists. Median ≈ 4.2% (Grgic et al., 2020;
# reported range up to ~12.1%). Provisional — shadow before trusting.
FALLBACK_MEASUREMENT_CV = 0.042
# A severe unexplained drop is this multiple of the material threshold; it routes
# to the existing safety subsystem rather than being treated as ordinary decline.
SEVERE_DECLINE_MULTIPLE = 3.0
# Relative gap above which a definition's stated MDC and its SEM-derived MDC are
# flagged as materially inconsistent (warn, MDC still governs).
MDC_SEM_CONSISTENCY_TOLERANCE = 0.5

# Transition classifications.
STABLE = "stable"
DECLINE_CANDIDATE = "decline_candidate"
SEVERE_DECLINE = "severe_decline"

# Measurement-error resolution sources (for audit/observability).
ME_SOURCE_MDC = "definition_mdc"
ME_SOURCE_SEM = "definition_sem_derived_mdc"
ME_SOURCE_FALLBACK = "fallback_cv"


@dataclass(frozen=True)
class MeasurementError:
    """Protocol measurement-error inputs for a benchmark definition.

    ``mdc`` and ``sem`` are absolute, in the value space of the observation. Either,
    both, or neither may be present; precedence is resolved in
    :func:`resolve_measurement_error`.
    """

    mdc: float | None = None
    sem: float | None = None


@dataclass(frozen=True)
class MeasurementErrorResult:
    value: float
    source: str
    calibration_basis: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ThresholdResult:
    """The material-decline threshold and its decomposition (fully auditable)."""

    threshold: float
    measurement_error_component: float
    uncertainty_component: float
    measurement_error_source: str
    z_down: float
    policy_version: str
    calibration_basis: str | None
    warnings: tuple[str, ...]


def mdc95_from_sem(sem: float) -> float:
    """Versioned SEM→MDC95 derivation: ``MDC95 = 1.96 · sqrt(2) · SEM``."""
    return 1.96 * math.sqrt(2.0) * sem


def resolve_measurement_error(
    error: MeasurementError | None,
    reference_value: float,
    *,
    fallback_cv: float = FALLBACK_MEASUREMENT_CV,
) -> MeasurementErrorResult:
    """Resolve the absolute measurement-error component by precedence (fork A).

    1. definition **MDC** present → use directly (if SEM is also present, warn when
       the SEM-derived MDC is materially inconsistent; MDC still governs);
    2. definition **SEM** present, MDC absent → ``mdc95_from_sem``;
    3. neither → a conservative versioned fallback ``fallback_cv · |reference_value|``,
       flagged ``synthetic_and_expert_prior`` (NOT calibrated).

    The validity-profile observation variance is deliberately *not* consumed here —
    it feeds only the uncertainty term of the threshold (see :func:`material_decline_threshold`).
    """
    ref = abs(reference_value)
    if error is not None and error.mdc is not None:
        warnings: list[str] = []
        if error.sem is not None:
            derived = mdc95_from_sem(error.sem)
            if error.mdc > 0 and abs(derived - error.mdc) / error.mdc > MDC_SEM_CONSISTENCY_TOLERANCE:
                warnings.append(
                    f"definition MDC {error.mdc:.4g} materially inconsistent with "
                    f"SEM-derived MDC95 {derived:.4g}; MDC governs"
                )
        return MeasurementErrorResult(error.mdc, ME_SOURCE_MDC, None, tuple(warnings))
    if error is not None and error.sem is not None:
        return MeasurementErrorResult(
            mdc95_from_sem(error.sem), ME_SOURCE_SEM, None, ()
        )
    return MeasurementErrorResult(
        fallback_cv * ref, ME_SOURCE_FALLBACK, CALIBRATION_BASIS_FALLBACK, ()
    )


def material_decline_threshold(
    *,
    prior_mean: float,
    prior_variance: float,
    observation_variance: float,
    error: MeasurementError | None = None,
    z_down: float = DEFAULT_Z_DOWN,
    fallback_cv: float = FALLBACK_MEASUREMENT_CV,
) -> ThresholdResult:
    """The locked variance-aware threshold.

    ``threshold = max( measurement_error, z_down · sqrt(prior_var + obs_var) )``.

    The measurement-error component is resolved by :func:`resolve_measurement_error`
    against ``prior_mean`` (the established level a decline is measured against). The
    hierarchy selects *only* the measurement-error term; it never replaces the
    uncertainty term.
    """
    me = resolve_measurement_error(error, prior_mean, fallback_cv=fallback_cv)
    uncertainty = z_down * math.sqrt(max(0.0, prior_variance) + max(0.0, observation_variance))
    threshold = max(me.value, uncertainty)
    return ThresholdResult(
        threshold=threshold,
        measurement_error_component=me.value,
        uncertainty_component=uncertainty,
        measurement_error_source=me.source,
        z_down=z_down,
        policy_version=POLICY_VERSION,
        calibration_basis=me.calibration_basis,
        warnings=me.warnings,
    )


def downward_residual(prior_mean: float, observed_value: float) -> float:
    """Signed downward delta: ``prior_mean − observed_value`` (positive = a drop)."""
    return prior_mean - observed_value


def is_material_decline(delta_down: float, threshold: ThresholdResult | float) -> bool:
    """True iff the drop is a positive delta at or beyond the material threshold."""
    thr = threshold.threshold if isinstance(threshold, ThresholdResult) else threshold
    return delta_down > 0.0 and delta_down >= thr


def classify_transition(
    delta_down: float,
    threshold: ThresholdResult | float,
    *,
    severe_multiple: float = SEVERE_DECLINE_MULTIPLE,
) -> str:
    """Classify a downward observation against the threshold.

    Returns :data:`STABLE` (no material decline — no candidate, no regression),
    :data:`DECLINE_CANDIDATE` (material — create a candidate, apply nothing yet), or
    :data:`SEVERE_DECLINE` (a severe unexplained drop — the service routes this to
    the existing safety subsystem; this module only *identifies* it).
    """
    thr = threshold.threshold if isinstance(threshold, ThresholdResult) else threshold
    if not is_material_decline(delta_down, thr):
        return STABLE
    if thr > 0.0 and delta_down >= severe_multiple * thr:
        return SEVERE_DECLINE
    return DECLINE_CANDIDATE


def posterior_gain(
    prior_variance: float,
    observation_variance: float,
    *,
    authority_scale: float = 1.0,
    corroboration_scale: float = 1.0,
) -> float:
    """Bounded Kalman-style gain in [0, 1].

    Base gain ``P / (P + R)`` (mirrors ``state_update_v0.kalman_gain``; kept local so
    this policy module stays import-light and independently testable), then narrowed
    by protocol-authority and corroboration factors. A stronger prior (low P) or a
    noisier observation (high R) yields a smaller move; the update is never a
    full overwrite.
    """
    pv = max(0.0, prior_variance)
    base = pv / (pv + max(1e-9, observation_variance))
    scaled = base * max(0.0, authority_scale) * max(0.0, corroboration_scale)
    return max(0.0, min(1.0, scaled))


def bounded_posterior(prior_mean: float, observed_value: float, gain: float) -> float:
    """Bounded estimator update: ``prior + K·(observation − prior)``.

    Never overwrites with ``observed_value``: with ``gain < 1`` the posterior stays
    strictly between the prior and the observation.
    """
    k = max(0.0, min(1.0, gain))
    return prior_mean + k * (observed_value - prior_mean)


def temporary_ceiling(observed_value: float, measurement_error: float) -> float:
    """Conservative prescription ceiling while a decline is unresolved.

    ``observed_value + measurement_error`` — protocol-derived, bracketed between the
    raw low observation (too reactive) and the old maximum (unsafe). The buffer is
    the resolved measurement-error component, not a bare guess.
    """
    return observed_value + max(0.0, measurement_error)
