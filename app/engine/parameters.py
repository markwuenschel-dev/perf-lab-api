"""
Literature-oriented parameter tables (Banister-style decays, Foster-like load exponents).

Tunable constants; future: load from DB or YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EngineParameters:
    # Dose law: D_k = w_k * log(1+V) * I^α * Δ^β * N^γ * F^ρ
    dose_alpha: float = 1.2  # intensity exponent
    dose_beta: float = 0.9  # density exponent
    dose_gamma: float = 0.6  # novelty exponent
    dose_rho: float = 1.0  # proximity-to-failure exponent

    # Fatigue decay Λ — half-life style hours (approx exp decay tau in cross_talk)
    tau_fatigue_hours: dict[str, float] = field(
        default_factory=lambda: {
            "cns": 360.0,  # slow (~15d)
            "muscular": 72.0,  # medium (~3d)
            "metabolic": 24.0,  # fast
            "structural": 720.0,  # very slow (~30d)
            "tendon": 960.0,
            "grip": 48.0,
        }
    )

    # Tissue decay Γ (same interpretation as fatigue — hours to decay stress)
    tau_tissue_hours: dict[str, float] = field(
        default_factory=lambda: {
            "shoulder": 168.0,
            "elbow": 168.0,
            "wrist": 120.0,
            "lumbar": 240.0,
            "hip": 168.0,
            "knee": 168.0,
            "ankle": 144.0,
            "finger": 96.0,
        }
    )

    # Superseded by multiplicative clearance (kept for backward compat; unused by engine).
    recovery_sleep_scale: float = 0.08
    recovery_stress_scale: float = 0.06

    # Multiplicative fatigue clearance modifier (replaces additive Ω subtraction).
    # beta[axis][signal]: weight on the z-score of each recovery signal.
    # Neutral (sleep=7, stress=7) → z=0 → multiplier=1.0.
    # Good recovery → z>0 → multiplier>1 (faster). Poor → z<0 → multiplier<1 (slower).
    recovery_clearance_beta: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "cns":        {"sleep": 0.10, "stress": 0.08},
            "muscular":   {"sleep": 0.08, "stress": 0.05},
            "metabolic":  {"sleep": 0.06, "stress": 0.04},
            "structural": {"sleep": 0.05, "stress": 0.04},
            "tendon":     {"sleep": 0.04, "stress": 0.04},
            "grip":       {"sleep": 0.06, "stress": 0.04},
        }
    )
    recovery_clearance_min: float = 0.60
    recovery_clearance_max: float = 1.50
    recovery_zscore_scale: float = 2.0

    # Capacity adaptation (Banister-style bump from hypertrophy signal)
    capacity_signal_threshold: float = 20.0
    capacity_struct_bump: float = 0.05
    capacity_hypertrophy_bump: float = 0.08

    # Cross-talk G on capacity (small off-diagonal nudges)
    crosstalk_metabolic_on_work_capacity: float = 0.02

    # --- Adaptation gain coefficients (W_adapt per capacity axis) ---
    # capacity_gain = adapt_coef[key] * phi_adapt[key] * base_dose. Sized (ADR-0033)
    # so a productive block yields a visible few-point gain while still net-positive
    # against the inter-session detraining decay below. Tuned via the sim harness.
    adapt_coef: dict[str, float] = field(
        default_factory=lambda: {
            "aerobic": 0.45,         # on the 0–650 scale, needs a larger coefficient
            "glycolytic": 0.28,
            "max_strength": 0.22,    # strength builds slowly but visibly
            "hypertrophy": 0.26,
            "power": 0.20,
            "skill": 0.36,           # skill responds fastest to quality work
            "mobility": 0.28,
            "work_capacity": 0.24,
        }
    )

    # --- Detraining: capacities decay toward baseline with disuse (ADR-0033) ---
    # Proportional per-day decay; per-session loss must stay below per-session gain so
    # training nets positive, while a layoff visibly erodes fitness. Aerobic/glycolytic
    # detrain fastest, max strength slowest.
    capacity_decay_per_day: dict[str, float] = field(
        default_factory=lambda: {
            "aerobic": 0.0005,
            "glycolytic": 0.0012,
            "max_strength": 0.0006,
            "hypertrophy": 0.0012,
            "power": 0.0009,
            "skill": 0.0005,
            "mobility": 0.0009,
            "work_capacity": 0.0015,
        }
    )

    # Fatigue-suppression of adaptation: if sum(F) / n_axes > threshold, gains scale down
    adapt_fatigue_suppress_threshold: float = 45.0   # Mean fatigue above this → suppression
    adapt_fatigue_suppress_floor: float = 0.3        # Minimum adaptation efficiency under max fatigue

    # Cross-talk: slow secondary gains from primary adaptation
    crosstalk_aerobic_on_work_capacity: float = 0.008
    crosstalk_hypertrophy_on_max_strength: float = 0.004  # Long-term cross-support
    crosstalk_skill_suppressed_above_cns: float = 55.0    # CNS fatigue above this → skill gains halved

    # --- Capacity confidence dynamics (ADR-0036) ---
    # Per-axis variance grows with elapsed time (process noise) and is capped, so a
    # long-unmeasured axis becomes a weak prior that the next benchmark corrects hard.
    confidence_process_noise_per_day: float = 0.004
    confidence_max_variance: float = 1.5
    # Measurement variance for a full-weight benchmark (lower ⇒ trusts the test more).
    confidence_measured_variance: float = 0.08


def default_parameters() -> EngineParameters:
    return EngineParameters()
