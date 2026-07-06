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

    # --- Session dose-law shaping constants (app/logic/dose_engine_v0.py) ---
    # Session volume proxy: V = w_dur*duration + w_vol*total_volume_load + w_sets*sets
    dose_volume_weights: dict[str, float] = field(
        default_factory=lambda: {
            "duration": 1.0,
            "volume_load": 0.02,
            "sets": 2.0,
        }
    )

    # Density Δ = clamp(duration / max(min_divisor, sets*sets_multiplier), floor, cap)
    dose_delta_sets_multiplier: float = 5.0
    dose_delta_min_divisor: float = 20.0
    dose_delta_cap: float = 2.5
    dose_delta_floor: float = 0.35

    # Floors applied to dose-law inputs
    dose_novelty_floor: float = 0.2
    dose_w_phi_floor: float = 0.25

    # Human-factor gain: penalty = 1 + max(0, (reference - value) * slope)
    dose_human_factor_reference: float = 5.0
    dose_human_factor_slope: float = 0.2

    # Per-exercise volume proxy weights (_entry_volume_proxy):
    #   proxy = w_vol*vol_load + duration_sec/dur_div + distance_m/dist_div + w_sets_reps*sets*reps
    dose_entry_volume_proxy_weights: dict[str, float] = field(
        default_factory=lambda: {
            "volume_load": 0.005,
            "duration_divisor": 60.0,
            "distance_divisor": 500.0,
            "sets_reps": 0.1,
        }
    )

    # Hand-set per-modality multipliers distributing `base` into the 6 dose axes (_shape_six).
    # Keys: "Running", "strength" (Strength/Hypertrophy/Power/Mixed), "default" (other).
    dose_shape_six_by_modality: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "Running": {
                "volume": 0.35,
                "intensity": 0.45,
                "density": 0.25,
                "impact": 0.55,
                "skill": 0.08,
                "metabolic": 0.9,
            },
            "strength": {
                "volume": 0.5,
                "intensity": 0.65,
                "density": 0.35,
                "impact": 0.25,
                "skill": 0.2,
                "metabolic": 0.35,
            },
            "default": {
                "volume": 0.4,
                "intensity": 0.5,
                "density": 0.3,
                "impact": 0.3,
                "skill": 0.15,
                "metabolic": 0.45,
            },
        }
    )

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
    # (crosstalk_skill_suppressed_above_cns removed — CNS-on-skill interference is now
    #  handled solely by directional_interference_multiplier in app/logic/interference.py)

    # --- Interference parameters (exponential suppression — app/logic/interference.py) ---
    # alpha values: how quickly interference ramps with load fraction [0,1].
    # Larger alpha = steeper suppression at moderate loads.
    # The strength/hypertrophy axes suppress on *excess* concurrent-endurance load
    # (load beyond interference_baseline_z0, the baseline a hard strength block itself
    # produces — ADR-0037 recalibration) so a strength block no longer self-penalizes.
    interference_e_on_strength_alpha: float = 4.0
    interference_e_on_power_alpha: float = 3.34
    interference_cns_on_power_alpha: float = 0.8
    interference_cns_on_skill_alpha: float = 0.6
    interference_structural_on_endurance_quality_alpha: float = 0.3
    # Block-compatible baseline endurance-load fraction: concurrent interference on
    # strength/hypertrophy bites only on load above this (ADR-0037). Set from the
    # observed endurance-load fraction a strength-only block produces (~0.21).
    interference_baseline_z0: float = 0.15
    interference_floor_by_axis: dict[str, float] = field(
        default_factory=lambda: {
            "max_strength": 0.20,
            "power":        0.30,
            "skill":        0.50,
            "aerobic":      0.70,
            "hypertrophy":  0.20,
        }
    )

    # --- Capacity confidence dynamics (ADR-0036) ---
    # Per-capacity-family process noise (variance units per day without benchmark).
    # Power/skill lose observability faster; mobility is slow-changing.
    confidence_process_noise_per_day: dict[str, float] = field(
        default_factory=lambda: {
            "aerobic":       0.0022,
            "glycolytic":    0.0028,
            "max_strength":  0.0025,
            "hypertrophy":   0.0018,
            "power":         0.0035,
            "skill":         0.0035,
            "mobility":      0.0012,
            "work_capacity": 0.0025,
        }
    )
    confidence_max_variance: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(("aerobic", "glycolytic", "max_strength", "hypertrophy", "power", "skill", "mobility", "work_capacity"), 1.5)
    )
    confidence_min_variance: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(("aerobic", "glycolytic", "max_strength", "hypertrophy", "power", "skill", "mobility", "work_capacity"), 0.01)
    )
    # Measurement variance for a full-weight benchmark (lower ⇒ trusts the test more).
    confidence_measured_variance: float = 0.08

    # --- Shadow EKF: full joint covariance over X/F/T (ADR-0041) ---
    # These parameterize the parallel shadow estimator only; nothing here affects the
    # production scalar confidence path. All variances are in normalized per-axis space
    # (axis / scale), matching the relative residual semantics of ADR-0034/0036.
    #
    # Process noise (variance/day) for the fatigue and tissue blocks — larger than
    # capacity's because fatigue/tissue are transient and re-driven every session.
    fatigue_process_noise_per_day: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(
            ("cns", "muscular", "metabolic", "structural", "tendon", "grip"), 0.02
        )
    )
    tissue_process_noise_per_day: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(
            ("shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger"), 0.01
        )
    )
    # Block-diagonal seed variance for fatigue/tissue (the capacity block seeds from the
    # production per-axis ``capacity_confidence``). Weak-ish priors in normalized space.
    ekf_seed_variance_fatigue: float = 0.25
    ekf_seed_variance_tissue: float = 0.25
    # Finite-difference step (normalized state units) for the transition Jacobian.
    ekf_epsilon: float = 1e-4
    # Variance floor/ceiling for the fatigue/tissue blocks (the capacity block reuses
    # confidence_min_variance / confidence_max_variance).
    ekf_min_variance: float = 1e-4
    ekf_max_variance: float = 2.0
    # Measurement noise for a soreness → fatigue observation (soreness is a noisy self-report).
    ekf_soreness_variance: float = 0.12
    # Measurement noise for an HRV/RHR → CNS (autonomic) fatigue observation.
    ekf_autonomic_variance: float = 0.15


def default_parameters() -> EngineParameters:
    return EngineParameters()
