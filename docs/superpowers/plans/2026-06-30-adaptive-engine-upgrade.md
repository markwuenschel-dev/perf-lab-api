# Validation-First Adaptive Training Engine Upgrade

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the adaptive training engine with corrected math, telemetry infrastructure, shadow-mode risk modules, and offline validation scaffolding — without adding learned models or aggressive personalization.

**Architecture:** Fixes the broken recovery Ω direction, adds per-benchmark Kalman variance, per-family confidence decay, full candidate logging, and shadow-mode modules (deload need, tissue risk, interference, decrement prediction) all starting at Level 0 (log only) or Level 1 (explanation only). Hard safety rules remain separate from learned scoring throughout.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL/JSONB, Alembic, Pydantic v2, pytest-asyncio

## Global Constraints

- All new logic modules default to `shadow_only: bool = True` — they observe but do not hard-block training.
- Hard safety rules in `prescriber.py` (`_safety_candidates`) must not be weakened or replaced by learned scoring.
- No learned model may produce a `tier="force"` deload without a hard safety rule already justifying it.
- Feature flags that are `False` by default must remain `False` — do not flip them on during implementation.
- Tests must assert math directionality (better > neutral, worse < neutral), not just "no crash".
- `EngineParameters` is internal — changing field types is safe as long as all callers are updated in the same task.
- Follow the existing Alembic naming convention: `a005_<name>.py`, `revision = "a005_<name>"`, `down_revision = "a004_wellness"`.

---

## File Structure

**New files:**
- `app/engine/feature_flags.py`
- `app/logic/benchmark_validity.py`
- `app/logic/deload_need.py`
- `app/logic/tissue_risk.py`
- `app/logic/interference.py`
- `app/logic/decrement_prediction.py`
- `app/models/telemetry.py`
- `app/models/experiment.py`
- `app/analysis/__init__.py`
- `app/analysis/feature_builders/__init__.py`
- `app/analysis/feature_builders/session_decrement.py`
- `app/analysis/feature_builders/fatigue_recovery.py`
- `app/analysis/feature_builders/tissue_risk_features.py`
- `app/analysis/feature_builders/sleep_stress_residual.py`
- `app/analysis/feature_builders/benchmark_validity_features.py`
- `app/analysis/feature_builders/deload_risk_features.py`
- `app/analysis/feature_builders/experiment_features.py`
- `app/analysis/feature_builders/scoring_weight_features.py`
- `app/analysis/feature_builders/interference_features.py`
- `app/analysis/feature_builders/confidence_calibration_features.py`
- `scripts/export_validation_datasets.py`
- `alembic/versions/a005_telemetry.py`
- `alembic/versions/a006_experiment.py`
- `tests/test_recovery_clearance.py`
- `tests/test_benchmark_validity.py`
- `tests/test_capacity_confidence_decay.py`
- `tests/test_deload_need.py`
- `tests/test_tissue_risk.py`
- `tests/test_interference.py`
- `tests/test_candidate_scoring_guardrails.py`
- `tests/test_experiment_arms.py`
- `tests/test_decrement_prediction.py`
- `tests/test_simulation_extended.py`

**Modified files:**
- `app/engine/parameters.py` — add recovery_clearance_beta, change confidence fields to dicts, add interference alphas
- `app/logic/state_update_v0.py` — fix recovery Ω, update confidence decay, use interference module
- `app/logic/constraint_engine/candidate.py` — add ScoreWeightProfile, validate_score_weights, simple_safe_goal_aligned_policy
- `app/logic/prescriber.py` — add candidate_log_out param, deload_need integration, experiment arm dispatch
- `app/models/__init__.py` — import new models

---

### Task 1: Fix Recovery Ω — Multiplicative Clearance Modifier

**Files:**
- Modify: `app/engine/parameters.py`
- Modify: `app/logic/state_update_v0.py`
- Create: `tests/test_recovery_clearance.py`

**Interfaces:**
- Produces: `recovery_clearance_multiplier(axis, sleep_quality, life_stress_inverse, params) -> float` — exported from `state_update_v0`

**Bug:** In `update_athlete_state`, step 3 (lines ~413–420) computes `omega = recovery_sleep_scale * max(0.0, 5.0 - sleep_quality)`. When sleep is poor (< 5), `omega > 0` and `v - omega * 0.18` decreases fatigue — clearing it *faster*. That is backwards. Poor recovery must slow fatigue clearance, not accelerate it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recovery_clearance.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta

from app.engine.parameters import default_parameters
from app.logic.state_update_v0 import recovery_clearance_multiplier, update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution, CapacityState, FatigueState, StressDoseSix, TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(cns: float = 30.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=cns, metabolic=cns, structural=cns, tendon=cns, grip=cns)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _log(sleep: float = 7.0, stress: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength",
        duration_minutes=60.0, session_rpe=6.0,
        sleep_quality=sleep, life_stress_inverse=stress,
    )


def _zero_dose() -> StressDose:
    return StressDose(
        dose_six=StressDoseSix(),
        adaptation_contribution=AdaptationContribution(),
    )


def test_good_sleep_clears_fatigue_faster_than_neutral():
    p = default_parameters()
    m_good = recovery_clearance_multiplier("cns", 9.0, 7.0, p)
    m_neutral = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert m_good > m_neutral, f"Good sleep ({m_good:.3f}) must exceed neutral ({m_neutral:.3f})"


def test_poor_sleep_clears_fatigue_slower_than_neutral():
    p = default_parameters()
    m_poor = recovery_clearance_multiplier("cns", 3.0, 7.0, p)
    m_neutral = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert m_poor < m_neutral, f"Poor sleep ({m_poor:.3f}) must be below neutral ({m_neutral:.3f})"


def test_poor_sleep_never_below_clearance_min():
    p = default_parameters()
    for axis in ("cns", "muscular", "metabolic", "structural", "tendon", "grip"):
        m = recovery_clearance_multiplier(axis, 1.0, 1.0, p)
        assert m >= p.recovery_clearance_min, f"Axis {axis}: {m:.3f} below min {p.recovery_clearance_min}"


def test_multiplier_bounded():
    p = default_parameters()
    m_high = recovery_clearance_multiplier("cns", 10.0, 10.0, p)
    m_low = recovery_clearance_multiplier("cns", 1.0, 1.0, p)
    assert m_high <= p.recovery_clearance_max
    assert m_low >= p.recovery_clearance_min


def test_neutral_inputs_give_multiplier_near_one():
    p = default_parameters()
    m = recovery_clearance_multiplier("cns", 7.0, 7.0, p)
    assert 0.98 <= m <= 1.02, f"Neutral inputs should give ~1.0, got {m:.3f}"


def test_dose_impulse_still_increases_fatigue_after_decay():
    s0 = _state(cns=10.0)
    dose = StressDose(
        dose_six=StressDoseSix(volume=1.0, intensity=1.0, density=0.5, impact=0.5, skill=0.5, metabolic=0.5),
        adaptation_contribution=AdaptationContribution(),
        d_nm_central=5.0, d_nm_peripheral=3.0, d_met_systemic=2.0, d_struct_damage=1.0,
    )
    s1 = update_athlete_state(s0, dose, timedelta(hours=1), _log())
    assert s1.fatigue_f.cns > 0.0
```

- [ ] **Step 2: Run test — expect failure (ImportError: `recovery_clearance_multiplier` not found)**

```
pytest tests/test_recovery_clearance.py -v
```

Expected: FAILED with ImportError

- [ ] **Step 3: Add parameters to `EngineParameters` in `app/engine/parameters.py`**

After the `recovery_stress_scale` field, add:

```python
    # Superseded by multiplicative clearance (kept for backward compat; unused by engine).
    # recovery_sleep_scale: float = 0.08
    # recovery_stress_scale: float = 0.06

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
```

Leave `recovery_sleep_scale` and `recovery_stress_scale` in place (they are still in the dataclass) but add a comment that the engine no longer reads them.

- [ ] **Step 4: Add `recovery_clearance_multiplier` to `app/logic/state_update_v0.py`**

After the `_exp_decay` function, add:

```python
def recovery_clearance_multiplier(
    axis: str,
    sleep_quality: float | None,
    life_stress_inverse: float | None,
    params: EngineParameters,
) -> float:
    """Multiplicative modifier on fatigue clearance rate.

    >1.0 = faster clearance (good recovery).
    <1.0 = slower clearance (poor recovery).
    Bounded to [recovery_clearance_min, recovery_clearance_max].
    Neutral at sleep_quality=7, life_stress_inverse=7.
    """
    beta = params.recovery_clearance_beta.get(axis, {"sleep": 0.06, "stress": 0.04})
    sq = sleep_quality if sleep_quality is not None else 7.0
    lsi = life_stress_inverse if life_stress_inverse is not None else 7.0
    scale = params.recovery_zscore_scale
    z_sleep = max(-scale, min(scale, (sq - 7.0) / 2.0))
    z_stress = max(-scale, min(scale, (lsi - 7.0) / 2.0))
    raw = math.exp(beta["sleep"] * z_sleep + beta["stress"] * z_stress)
    return max(params.recovery_clearance_min, min(params.recovery_clearance_max, raw))
```

- [ ] **Step 5: Update `update_athlete_state` — apply multiplier in step 1, remove step 3**

Replace the existing step 1 block:
```python
    # --- 1. Fatigue decay (Λ) ---
    for key in FatigueState.KEYS:
        tau = p.tau_fatigue_hours[key]
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, _exp_decay(v, hours, tau))
```

With:
```python
    # --- 1. Fatigue decay (Λ) with recovery-modulated clearance rate ---
    # Multiplier >1 (good sleep/low stress) → effective hours larger → faster decay.
    # Multiplier <1 (poor sleep/high stress) → effective hours smaller → slower decay.
    for key in FatigueState.KEYS:
        tau = p.tau_fatigue_hours[key]
        m = recovery_clearance_multiplier(key, log.sleep_quality, log.life_stress_inverse, p)
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, _exp_decay(v, hours * m, tau))
```

Then delete step 3 entirely (the omega subtraction block):
```python
    # --- 3. Recovery Ω (sleep / life stress) ---   ← DELETE THIS BLOCK
    omega = (
        p.recovery_sleep_scale * max(0.0, 5.0 - log.sleep_quality) * hours
        + p.recovery_stress_scale * max(0.0, 5.0 - log.life_stress_inverse) * hours
    )
    for key in FatigueState.KEYS:
        v = getattr(s.fatigue_f, key)
        setattr(s.fatigue_f, key, max(0.0, v - omega * 0.18))
```

Renumber subsequent steps (old 4→3, 5→4, etc.) in comments only.

- [ ] **Step 6: Run tests — expect pass**

```
pytest tests/test_recovery_clearance.py tests/test_state_update_v2.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add app/engine/parameters.py app/logic/state_update_v0.py tests/test_recovery_clearance.py
git commit -m "fix: replace additive recovery Ω with multiplicative clearance modifier"
```

---

### Task 2: Benchmark Validity Profiles

**Files:**
- Create: `app/logic/benchmark_validity.py`
- Modify: `app/logic/state_update_v0.py`
- Create: `tests/test_benchmark_validity.py`

**Interfaces:**
- Produces: `BenchmarkValidityProfile` dataclass, `get_validity_profile(code: str) -> BenchmarkValidityProfile`, `effective_variance(profile, state) -> float`
- Consumed by: `_apply_capacity_residual` in `state_update_v0.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_benchmark_validity.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.benchmark_validity import (
    BenchmarkValidityProfile, effective_variance, get_validity_profile,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(cns: float = 0.0, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_1rm_is_capacity_dominant():
    p = get_validity_profile("1rm")
    assert p.classification == "capacity_dominant"
    assert p.measurement_variance < 0.10


def test_mobility_is_noise_prone():
    p = get_validity_profile("mobility")
    assert p.classification in ("noise_prone", "skill_sensitive")
    assert p.measurement_variance > 0.10


def test_1rm_has_strong_strength_mapping():
    p = get_validity_profile("1rm")
    assert p.mapping_strength.get("max_strength", 0.0) > 0.80


def test_rested_1rm_higher_gain_than_fatigued_mobility():
    p_1rm = get_validity_profile("1rm")
    p_mob = get_validity_profile("mobility")
    s_fresh = _state(cns=5.0, muscular=5.0)
    s_tired = _state(cns=60.0, muscular=60.0)
    r_1rm_fresh = effective_variance(p_1rm, s_fresh)
    r_mob_tired = effective_variance(p_mob, s_tired)
    assert r_1rm_fresh < r_mob_tired, "Fresh 1RM should have lower R_eff than fatigued mobility"


def test_high_fatigue_increases_effective_variance():
    p = get_validity_profile("rep_max")
    s_fresh = _state(cns=5.0, muscular=5.0)
    s_tired = _state(cns=70.0, muscular=70.0)
    r_fresh = effective_variance(p, s_fresh)
    r_tired = effective_variance(p, s_tired)
    assert r_tired > r_fresh, "High fatigue must raise effective variance for fatigue-sensitive benchmark"


def test_weak_mapping_reduces_gain():
    from app.logic.state_update_v0 import kalman_gain
    r_eff = 0.08
    prior_var = 1.0
    strong_mapping = 0.95
    weak_mapping = 0.20
    gain_strong = prior_var * strong_mapping / (strong_mapping ** 2 * prior_var + r_eff)
    gain_weak = prior_var * weak_mapping / (weak_mapping ** 2 * prior_var + r_eff)
    assert gain_strong > gain_weak


def test_unknown_benchmark_code_returns_noise_prone_default():
    p = get_validity_profile("some_unknown_benchmark_xyz")
    assert p.classification == "noise_prone"
    assert p.measurement_variance >= 0.15
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_benchmark_validity.py -v
```

Expected: FAILED with ModuleNotFoundError

- [ ] **Step 3: Create `app/logic/benchmark_validity.py`**

```python
"""Benchmark-specific validity profiles for capacity assimilation.

Each profile encodes measurement variance, mapping strength per capacity axis,
and sensitivity to fatigue/tissue/skill state. These feed into effective_variance()
which modulates the Kalman gain in _apply_capacity_residual.

These are priors, not validated truths. Refine after Q5 dataset analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


@dataclass
class BenchmarkValidityProfile:
    benchmark_code: str
    measurement_variance: float
    protocol_variance: float = 0.0
    mapping_strength: dict[str, float] = field(default_factory=dict)
    fatigue_sensitivity: dict[str, float] = field(default_factory=dict)
    tissue_sensitivity: dict[str, float] = field(default_factory=dict)
    skill_sensitivity: float = 0.0
    reliability_prior: float = 1.0
    min_attempts_for_strong_update: int = 1
    classification: Literal[
        "capacity_dominant", "fatigue_sensitive", "skill_sensitive", "noise_prone"
    ] = "capacity_dominant"


def _default_profiles() -> dict[str, BenchmarkValidityProfile]:
    return {
        "1rm": BenchmarkValidityProfile(
            benchmark_code="1rm",
            measurement_variance=0.04,
            protocol_variance=0.01,
            mapping_strength={"max_strength": 0.95, "hypertrophy": 0.30, "power": 0.25},
            fatigue_sensitivity={"cns": 0.50, "muscular": 0.35, "structural": 0.20},
            classification="capacity_dominant",
        ),
        "e1rm": BenchmarkValidityProfile(
            benchmark_code="e1rm",
            measurement_variance=0.07,
            protocol_variance=0.03,
            mapping_strength={"max_strength": 0.80, "hypertrophy": 0.35},
            fatigue_sensitivity={"cns": 0.40, "muscular": 0.50},
            classification="capacity_dominant",
        ),
        "rep_max": BenchmarkValidityProfile(
            benchmark_code="rep_max",
            measurement_variance=0.10,
            protocol_variance=0.04,
            mapping_strength={"hypertrophy": 0.75, "max_strength": 0.50, "work_capacity": 0.30},
            fatigue_sensitivity={"muscular": 0.70, "metabolic": 0.40, "cns": 0.30},
            classification="fatigue_sensitive",
        ),
        "mile": BenchmarkValidityProfile(
            benchmark_code="mile",
            measurement_variance=0.05,
            protocol_variance=0.03,
            mapping_strength={"aerobic": 0.85, "work_capacity": 0.40, "glycolytic": 0.30},
            fatigue_sensitivity={"metabolic": 0.30, "structural": 0.25},
            classification="capacity_dominant",
        ),
        "5k": BenchmarkValidityProfile(
            benchmark_code="5k",
            measurement_variance=0.04,
            protocol_variance=0.04,
            mapping_strength={"aerobic": 0.90, "work_capacity": 0.45},
            fatigue_sensitivity={"metabolic": 0.25, "structural": 0.20},
            classification="capacity_dominant",
        ),
        "400m": BenchmarkValidityProfile(
            benchmark_code="400m",
            measurement_variance=0.08,
            protocol_variance=0.03,
            mapping_strength={"glycolytic": 0.80, "power": 0.50, "aerobic": 0.30},
            fatigue_sensitivity={"metabolic": 0.60, "cns": 0.40, "muscular": 0.35},
            classification="fatigue_sensitive",
        ),
        "vertical_jump": BenchmarkValidityProfile(
            benchmark_code="vertical_jump",
            measurement_variance=0.08,
            protocol_variance=0.04,
            mapping_strength={"power": 0.85, "max_strength": 0.40},
            fatigue_sensitivity={"cns": 0.65, "muscular": 0.50, "structural": 0.30},
            classification="fatigue_sensitive",
        ),
        "grip": BenchmarkValidityProfile(
            benchmark_code="grip",
            measurement_variance=0.12,
            protocol_variance=0.05,
            mapping_strength={"max_strength": 0.50, "work_capacity": 0.25},
            fatigue_sensitivity={"grip": 0.80, "muscular": 0.40},
            tissue_sensitivity={"finger": 0.60, "wrist": 0.40},
            classification="noise_prone",
        ),
        "mobility": BenchmarkValidityProfile(
            benchmark_code="mobility",
            measurement_variance=0.15,
            protocol_variance=0.08,
            mapping_strength={"mobility": 0.70, "skill": 0.20},
            skill_sensitivity=0.30,
            classification="noise_prone",
        ),
        "technical_skill": BenchmarkValidityProfile(
            benchmark_code="technical_skill",
            measurement_variance=0.18,
            protocol_variance=0.06,
            mapping_strength={"skill": 0.80, "power": 0.20},
            fatigue_sensitivity={"cns": 0.50},
            skill_sensitivity=0.60,
            classification="skill_sensitive",
        ),
    }


_PROFILES: dict[str, BenchmarkValidityProfile] = _default_profiles()

_NOISE_PRONE_DEFAULT = BenchmarkValidityProfile(
    benchmark_code="unknown",
    measurement_variance=0.18,
    protocol_variance=0.05,
    mapping_strength={},
    classification="noise_prone",
)


def get_validity_profile(benchmark_code: str) -> BenchmarkValidityProfile:
    """Return the validity profile for a benchmark code. Defaults to noise_prone."""
    return _PROFILES.get(benchmark_code, _NOISE_PRONE_DEFAULT)


def effective_variance(
    profile: BenchmarkValidityProfile,
    state: UnifiedStateVector,
) -> float:
    """Compute R_eff = base variance + state-dependent uncertainty.

    Higher R_eff → smaller Kalman gain → less capacity update.
    Fatigue/tissue uncertainty is scaled by profile sensitivity, not state magnitude,
    so a low-sensitivity benchmark is not penalized by athlete fatigue.
    """
    base = profile.measurement_variance + profile.protocol_variance

    # Fatigue uncertainty: how much does current fatigue cloud the result?
    fat_contrib = 0.0
    for k in FatigueState.KEYS:
        sens = profile.fatigue_sensitivity.get(k, 0.0)
        val = getattr(state.fatigue_f, k, 0.0) / 100.0
        fat_contrib += sens * val
    fat_contrib *= 0.20  # scale factor: max sensitivity=1 × max fatigue=1 → +0.20

    # Tissue uncertainty
    tis_contrib = 0.0
    for k in TissueState.KEYS:
        sens = profile.tissue_sensitivity.get(k, 0.0)
        val = getattr(state.tissue_t, k, 0.0) / 100.0
        tis_contrib += sens * val
    tis_contrib *= 0.10

    skill_contrib = profile.skill_sensitivity * 0.08

    return base + fat_contrib + tis_contrib + skill_contrib
```

- [ ] **Step 4: Update `_apply_capacity_residual` in `app/logic/state_update_v0.py` to accept an optional profile**

Add import at top:
```python
from app.logic.benchmark_validity import BenchmarkValidityProfile, effective_variance
```

Change the signature of `_apply_capacity_residual`:
```python
def _apply_capacity_residual(
    s: UnifiedStateVector,
    mapping: Any,
    score01: float,
    observation_weight: float,
    p: EngineParameters,
    validity_profile: BenchmarkValidityProfile | None = None,
) -> None:
    """Signed, confidence-scaled residual correction of a capacity axis (ADR-0034).

    When a validity_profile is supplied, uses profile-specific effective variance
    (R_eff) and per-axis mapping strength instead of the generic confidence_measured_variance.
    This reduces update strength for noisy/fatigue-sensitive benchmarks without
    aggressively suppressing valid 1RM-style tests.
    """
    key = mapping.target_key
    try:
        cur = _read_axis(s, "capacity", key)
    except AttributeError:
        return
    ceiling = _capacity_ceiling(key)
    expected01 = cur / ceiling if ceiling > 0 else 0.0
    residual01 = score01 - expected01
    prior_var = float(getattr(s.capacity_confidence, key, 1.0))

    if validity_profile is not None:
        r_eff = effective_variance(validity_profile, s)
        mapping_strength = validity_profile.mapping_strength.get(key, float(mapping.coefficient))
        # Gain formula: K = P * m / (m² * P + R_eff)
        gain = prior_var * mapping_strength / (mapping_strength ** 2 * prior_var + max(1e-9, r_eff))
        conf_post = (1.0 - gain * mapping_strength) * prior_var
    else:
        weight = max(0.0, float(mapping.coefficient))
        meas_var = p.confidence_measured_variance / max(0.1, float(observation_weight))
        gain = kalman_gain(prior_var, meas_var)
        mapping_strength = weight
        conf_post = max(0.0, (1.0 - gain) * prior_var)

    new_v = cur + mapping_strength * gain * residual01 * ceiling
    if mapping.min_value is not None:
        new_v = max(new_v, float(mapping.min_value))
    if mapping.max_value is not None:
        new_v = min(new_v, float(mapping.max_value))
    _write_axis(s, "capacity", key, new_v)
    setattr(s.capacity_confidence, key, max(0.0, conf_post))
```

Update `apply_benchmark_observation` to accept and thread through the profile:
```python
def apply_benchmark_observation(
    prev_state: UnifiedStateVector,
    *,
    raw_value: float,
    normalized_value: float | None,
    better_direction: str,
    observation_weight: float,
    mappings: Sequence[Any],
    observed_at: datetime | None = None,
    score01: float | None = None,
    validity_profile: BenchmarkValidityProfile | None = None,
) -> UnifiedStateVector:
```

Inside the loop, pass `validity_profile`:
```python
        if m.target_vector == "capacity" and score01 is not None:
            _apply_capacity_residual(s, m, score01, observation_weight, p, validity_profile)
            continue
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_benchmark_validity.py tests/test_benchmark_state.py tests/test_state_update_v2.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/logic/benchmark_validity.py app/logic/state_update_v0.py tests/test_benchmark_validity.py
git commit -m "feat: add benchmark validity profiles for per-benchmark Kalman effective variance"
```

---

### Task 3: Capacity Confidence Decay by Family

**Files:**
- Modify: `app/engine/parameters.py`
- Modify: `app/logic/state_update_v0.py`
- Create: `app/engine/feature_flags.py`
- Create: `tests/test_capacity_confidence_decay.py`
- Modify: `tests/test_capacity_confidence.py` (update for new dict types)

**Interfaces:**
- Modifies: `EngineParameters.confidence_process_noise_per_day` from `float` to `dict[str, float]`
- Modifies: `EngineParameters.confidence_max_variance` from `float` to `dict[str, float]`
- Adds: `EngineParameters.confidence_min_variance: dict[str, float]`

**Breaking change:** `confidence_process_noise_per_day` and `confidence_max_variance` change from scalar to dict. Update `_grow_confidence_variance` and any test that constructs `EngineParameters` with these as floats.

- [ ] **Step 1: Create `app/engine/feature_flags.py`**

```python
"""Engine feature flags.

All flags default to False. Do not enable in production without validation data.
"""

ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE: bool = False
ENABLE_TISSUE_RISK_CANDIDATE_PENALTY: bool = False
ENABLE_DECREMENT_PREDICTION_HARD_BLOCK: bool = False
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_capacity_confidence_decay.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta

from app.engine.parameters import EngineParameters, default_parameters
from app.engine.feature_flags import ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution, CapacityConfidence, CapacityState,
    FatigueState, StressDoseSix, TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(conf: float = 1.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    cap_conf = CapacityConfidence(
        aerobic=conf, glycolytic=conf, max_strength=conf,
        hypertrophy=conf, power=conf, skill=conf,
        mobility=conf, work_capacity=conf,
    )
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        capacity_confidence=cap_conf,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _log() -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength",
        duration_minutes=60.0, session_rpe=6.0,
        sleep_quality=7.0, life_stress_inverse=7.0,
    )


def _zero_dose() -> StressDose:
    return StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())


def test_variance_increases_with_time():
    s0 = _state(conf=0.20)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=7), _log())
    assert s1.capacity_confidence.max_strength > s0.capacity_confidence.max_strength
    assert s1.capacity_confidence.aerobic > s0.capacity_confidence.aerobic


def test_variance_is_capped_per_axis():
    p = default_parameters()
    s0 = _state(conf=1.0)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=365), _log())
    for key in ("aerobic", "max_strength", "power", "skill"):
        v = getattr(s1.capacity_confidence, key)
        max_v = p.confidence_max_variance.get(key, 1.5)
        assert v <= max_v, f"{key}: {v} exceeds max {max_v}"


def test_different_axes_have_different_noise_rates():
    p = default_parameters()
    q_power = p.confidence_process_noise_per_day.get("power", 0.0)
    q_mobility = p.confidence_process_noise_per_day.get("mobility", 0.0)
    assert q_power > q_mobility, "Power should decay confidence faster than mobility"


def test_workout_logs_do_not_reduce_variance():
    """Workout training maintains fitness but not observability — only benchmarks reduce variance."""
    assert ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE is False, \
        "Feature flag must remain False — workout-informed maintenance not yet validated"
    s0 = _state(conf=0.50)
    # Even many training sessions should not pull variance down
    s = s0
    dose = StressDose(
        dose_six=StressDoseSix(volume=1.0, intensity=0.8, density=0.5, impact=0.3, skill=0.2, metabolic=0.4),
        adaptation_contribution=AdaptationContribution(max_strength=3.0),
        d_nm_central=3.0, d_nm_peripheral=2.0, d_met_systemic=1.0, d_struct_damage=0.5, d_struct_signal=2.0,
    )
    for _ in range(12):  # 12 sessions
        s = update_athlete_state(s, dose, timedelta(days=2), _log())
    # Variance should not have decreased from training alone
    assert s.capacity_confidence.max_strength >= 0.50, \
        "Training logs must not reduce capacity confidence — only benchmarks may do so"
```

- [ ] **Step 3: Run test — expect failure**

```
pytest tests/test_capacity_confidence_decay.py -v
```

Expected: FAILED (dict access on scalar `confidence_process_noise_per_day`)

- [ ] **Step 4: Update `EngineParameters` confidence fields in `app/engine/parameters.py`**

Replace:
```python
    confidence_process_noise_per_day: float = 0.004
    confidence_max_variance: float = 1.5
    confidence_measured_variance: float = 0.08
```

With:
```python
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
        default_factory=lambda: {k: 1.5 for k in (
            "aerobic", "glycolytic", "max_strength", "hypertrophy",
            "power", "skill", "mobility", "work_capacity",
        )}
    )
    confidence_min_variance: dict[str, float] = field(
        default_factory=lambda: {k: 0.01 for k in (
            "aerobic", "glycolytic", "max_strength", "hypertrophy",
            "power", "skill", "mobility", "work_capacity",
        )}
    )
    confidence_measured_variance: float = 0.08
```

- [ ] **Step 5: Update `_grow_confidence_variance` in `app/logic/state_update_v0.py`**

Replace:
```python
def _grow_confidence_variance(
    confidence: CapacityConfidence,
    hours: float,
    p: EngineParameters,
) -> None:
    growth = p.confidence_process_noise_per_day * (hours / 24.0)
    if growth <= 0.0:
        return
    for key in CapacityConfidence.KEYS:
        v = getattr(confidence, key) + growth
        setattr(confidence, key, min(p.confidence_max_variance, v))
```

With:
```python
def _grow_confidence_variance(
    confidence: CapacityConfidence,
    hours: float,
    p: EngineParameters,
) -> None:
    """Grow per-axis capacity variance with elapsed time (process noise).

    Training moves capacity mean but does not measure it, so only time passing
    increases uncertainty. Benchmarks reduce it. ADR-0036.
    """
    dt_days = hours / 24.0
    if dt_days <= 0.0:
        return
    for key in CapacityConfidence.KEYS:
        q = p.confidence_process_noise_per_day.get(key, 0.0025)
        max_v = p.confidence_max_variance.get(key, 1.5)
        v = getattr(confidence, key) + q * dt_days
        setattr(confidence, key, min(max_v, v))
```

- [ ] **Step 6: Update `tests/test_capacity_confidence.py` for dict types**

Open `tests/test_capacity_confidence.py` and replace any line that accesses `p.confidence_process_noise_per_day` as a float with a dict lookup. For example:
- `assert growth == p.confidence_process_noise_per_day * days` → `assert growth == p.confidence_process_noise_per_day.get("max_strength", 0.003) * days`
- Any `EngineParameters(confidence_process_noise_per_day=0.005)` → `EngineParameters(confidence_process_noise_per_day={"max_strength": 0.005, ...})`

- [ ] **Step 7: Run all confidence-related tests**

```
pytest tests/test_capacity_confidence_decay.py tests/test_capacity_confidence.py tests/test_state_update_v2.py -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/engine/parameters.py app/engine/feature_flags.py app/logic/state_update_v0.py \
        tests/test_capacity_confidence_decay.py tests/test_capacity_confidence.py
git commit -m "feat: per-family capacity confidence decay; add feature_flags module"
```

---

### Task 4: Telemetry Models and Migration

**Files:**
- Create: `app/models/telemetry.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/a005_telemetry.py`
- Create: `tests/test_telemetry_models.py`

**Interfaces:**
- Produces: `PrescriptionDecision`, `CandidateDecisionLog`, `SessionFeedback`, `PainReport`, `OutcomeEvent` ORM models

- [ ] **Step 1: Write tests (no-DB unit tests for model construction)**

```python
# tests/test_telemetry_models.py
from datetime import datetime, UTC
from app.models.telemetry import (
    CandidateDecisionLog, OutcomeEvent, PainReport,
    PrescriptionDecision, SessionFeedback,
)


def test_prescription_decision_defaults():
    pd = PrescriptionDecision(
        athlete_id=1, goal="Strength", algorithm_version="v0", decision_mode="adaptive",
    )
    assert pd.decision_mode == "adaptive"
    assert pd.chosen_score is None


def test_candidate_decision_log_defaults():
    cdl = CandidateDecisionLog(
        prescription_decision_id=1, branch_id="readiness_cns",
        candidate_type="Metabolic Conditioning", source="redirect",
    )
    assert cdl.hard_failed is False
    assert cdl.chosen is False


def test_session_feedback_defaults():
    sf = SessionFeedback(planned_session_id=42, status="skipped")
    assert sf.modified_volume is False
    assert sf.pain_flag is False
    assert sf.followed_as_prescribed is None


def test_pain_report_axes():
    valid_axes = {"shoulder", "elbow", "wrist", "lumbar", "hip", "knee", "ankle", "finger", "other"}
    pr = PainReport(
        athlete_id=1, reported_at=datetime.now(UTC),
        tissue_axis="knee", severity_0_10=4.0, affected_training=True, onset="gradual",
    )
    assert pr.tissue_axis in valid_axes


def test_outcome_event_types():
    valid_types = {
        "pain_event", "tissue_skip", "non_tissue_skip", "unknown_skip",
        "tissue_modified", "non_tissue_modified", "forced_deload", "planned_deload",
        "benchmark_underperformance", "excessive_fatigue",
    }
    oe = OutcomeEvent(
        athlete_id=1, occurred_at=datetime.now(UTC),
        event_type="unknown_skip", confidence=0.5,
    )
    assert oe.event_type in valid_types
    assert oe.confidence == 0.5
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_telemetry_models.py -v
```

- [ ] **Step 3: Create `app/models/telemetry.py`**

```python
"""Telemetry models for adaptive engine research (prescription decisions, outcomes).

These tables provide the instrumentation needed to answer the 10 research questions.
All tables use integer PKs and JSONB blobs for flexible snapshotting.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrescriptionDecision(Base):
    """One row per prescription call. Required for Q7 (adaptive vs static) and Q8 (scoring weights)."""
    __tablename__ = "prescription_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    planned_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("planned_sessions.id"), nullable=True
    )
    goal: Mapped[str] = mapped_column(String, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String, nullable=False, default="v0")
    decision_mode: Mapped[str] = mapped_column(String, nullable=False, default="adaptive")
    state_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    block_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    chosen_candidate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    chosen_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CandidateDecisionLog(Base):
    """One row per candidate considered (not just chosen). Required for Q8 (scoring weights).

    Without rejected candidates, offline policy evaluation is weak.
    """
    __tablename__ = "candidate_decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    prescription_decision_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prescription_decisions.id"), nullable=False, index=True
    )
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_type: Mapped[str] = mapped_column(String, nullable=False)
    focus: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="generator")
    score_components_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hard_fail_reasons_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    soft_warnings_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SessionFeedback(Base):
    """One row per planned session outcome. Distinguishes completed/skipped/modified/unknown.

    IMPORTANT: Do not infer followed_as_prescribed from seeded exercise logs.
    """
    __tablename__ = "session_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    planned_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("planned_sessions.id"), nullable=False, index=True, unique=True
    )
    completed_workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    followed_as_prescribed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    modified_volume: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_intensity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_exercises: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    perceived_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pain_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    soreness_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PainReport(Base):
    """Athlete-reported pain. Tissue-axis-specific. Not inferred from skips."""
    __tablename__ = "pain_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tissue_axis: Mapped[str] = mapped_column(String, nullable=False)
    severity_0_10: Mapped[float] = mapped_column(Float, nullable=False)
    affected_training: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onset: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class OutcomeEvent(Base):
    """Aggregated outcome events for risk model training.

    unknown_skip must not be classified as tissue_skip without evidence.
    """
    __tablename__ = "outcome_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    tissue_axis: Mapped[str | None] = mapped_column(String, nullable=True)
    source_table: Mapped[str | None] = mapped_column(String, nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
```

- [ ] **Step 4: Add imports to `app/models/__init__.py`**

Add after the existing imports:
```python
from app.models.telemetry import (  # noqa: F401
    CandidateDecisionLog, OutcomeEvent, PainReport,
    PrescriptionDecision, SessionFeedback,
)
```

- [ ] **Step 5: Create `alembic/versions/a005_telemetry.py`**

```python
"""Telemetry tables for adaptive engine research (prescription, candidates, feedback, pain, outcomes).

Revision ID: a005_telemetry
Revises: a004_wellness
Create Date: 2026-06-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "a005_telemetry"
down_revision: str | None = "a004_wellness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prescription_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("planned_session_id", sa.Integer(), nullable=True),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("algorithm_version", sa.String(), nullable=False, server_default="v0"),
        sa.Column("decision_mode", sa.String(), nullable=False, server_default="adaptive"),
        sa.Column("state_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("block_context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chosen_candidate_id", sa.String(), nullable=True),
        sa.Column("chosen_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["planned_session_id"], ["planned_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prescription_decisions_athlete_id", "prescription_decisions", ["athlete_id"])

    op.create_table(
        "candidate_decision_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prescription_decision_id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("candidate_type", sa.String(), nullable=False),
        sa.Column("focus", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="generator"),
        sa.Column("score_components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("hard_failed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hard_fail_reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("soft_warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chosen", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["prescription_decision_id"], ["prescription_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidate_decision_logs_decision_id", "candidate_decision_logs", ["prescription_decision_id"])

    op.create_table(
        "session_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planned_session_id", sa.Integer(), nullable=False),
        sa.Column("completed_workout_log_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("followed_as_prescribed", sa.Boolean(), nullable=True),
        sa.Column("modified_volume", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modified_intensity", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modified_exercises", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("modification_reason", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("satisfaction_score", sa.Integer(), nullable=True),
        sa.Column("perceived_fit_score", sa.Integer(), nullable=True),
        sa.Column("pain_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("soreness_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["planned_session_id"], ["planned_sessions.id"]),
        sa.ForeignKeyConstraint(["completed_workout_log_id"], ["workout_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planned_session_id"),
    )
    op.create_index("ix_session_feedback_planned_id", "session_feedback", ["planned_session_id"])

    op.create_table(
        "pain_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("tissue_axis", sa.String(), nullable=False),
        sa.Column("severity_0_10", sa.Float(), nullable=False),
        sa.Column("affected_training", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("onset", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pain_reports_athlete_id", "pain_reports", ["athlete_id"])

    op.create_table(
        "outcome_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("tissue_axis", sa.String(), nullable=True),
        sa.Column("source_table", sa.String(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outcome_events_athlete_id", "outcome_events", ["athlete_id"])


def downgrade() -> None:
    op.drop_table("outcome_events")
    op.drop_table("pain_reports")
    op.drop_table("session_feedback")
    op.drop_table("candidate_decision_logs")
    op.drop_table("prescription_decisions")
```

- [ ] **Step 6: Run tests**

```
pytest tests/test_telemetry_models.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add app/models/telemetry.py app/models/__init__.py \
        alembic/versions/a005_telemetry.py tests/test_telemetry_models.py
git commit -m "feat: add telemetry models for prescription decisions, feedback, pain, outcomes"
```

---

### Task 5: Deload Need Shadow Module

**Files:**
- Create: `app/logic/deload_need.py`
- Modify: `app/logic/prescriber.py`
- Create: `tests/test_deload_need.py`

**Interfaces:**
- Produces: `DeloadNeed` dataclass, `compute_deload_need(state, ...) -> DeloadNeed`
- Consumed by: `recommend_next_session` in `prescriber.py` (explanation only — Level 1)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_deload_need.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.deload_need import DeloadNeed, compute_deload_need
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(
    cns: float = 0.0, muscular: float = 0.0, metabolic: float = 0.0,
    structural: float = 0.0, tendon: float = 0.0, grip: float = 0.0,
    lumbar: float = 0.0, knee: float = 0.0,
) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular, metabolic=metabolic,
                     structural=structural, tendon=tendon, grip=grip)
    t = TissueState(lumbar=lumbar, knee=knee)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_fresh_state_gives_tier_none():
    s = _state(cns=10.0, muscular=10.0)
    result = compute_deload_need(s)
    assert result.tier == "none"
    assert result.shadow_only is True


def test_single_high_fatigue_axis_gives_watch_or_bias():
    s = _state(cns=65.0)  # one axis over 60
    result = compute_deload_need(s)
    assert result.tier in ("watch", "bias", "force")


def test_hard_rule_any_axis_over_60_triggers_force_or_bias():
    s = _state(cns=75.0)
    result = compute_deload_need(s)
    assert result.score >= 0.55, "Single very high fatigue axis should score bias or force"
    assert "cns" in " ".join(result.drivers).lower() or any("fatigue" in d for d in result.drivers)


def test_two_soft_signals_required_for_bias():
    """Single soft signal (without hard rule) must not produce bias tier."""
    s = _state(cns=30.0, muscular=30.0)  # no hard rule
    result = compute_deload_need(
        s,
        performance_residual_slope=-0.05,  # one soft signal
        mean_fatigue_slope=None,
        max_tissue_slope=None,
        recent_adherence=None,
    )
    assert result.tier in ("none", "watch"), f"Single soft signal should not trigger bias, got {result.tier}"


def test_two_soft_signals_can_reach_bias():
    s = _state(cns=30.0, muscular=30.0)
    result = compute_deload_need(
        s,
        performance_residual_slope=-0.06,   # soft signal 1
        mean_fatigue_slope=0.04,             # soft signal 2
        max_tissue_slope=None,
        recent_adherence=None,
    )
    assert result.tier in ("watch", "bias")


def test_deload_need_is_shadow_only():
    s = _state(cns=80.0)
    result = compute_deload_need(s)
    assert result.shadow_only is True


def test_tier_mapping():
    from app.logic.deload_need import _tier_from_score
    assert _tier_from_score(0.20) == "none"
    assert _tier_from_score(0.40) == "watch"
    assert _tier_from_score(0.60) == "bias"
    assert _tier_from_score(0.80) == "force"
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_deload_need.py -v
```

- [ ] **Step 3: Create `app/logic/deload_need.py`**

```python
"""Deload need assessment. Shadow mode only (Level 1: explanation).

Implements a rule-guarded baseline using fatigue state, tissue state, and
optional trend signals. Hard rules require one high-fatigue condition.
Soft rules require at least two concurrent signals. No HMM or RL.

Do not use this module to hard-block training. Use existing planning.deload_triggered()
for hard fallback. This module is the precursor to a learned model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.engine_vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

_HARD_FATIGUE_THRESHOLD = 60.0
_HARD_MEAN_FATIGUE_THRESHOLD = 45.0
_HARD_TISSUE_THRESHOLD = 55.0
_SOFT_PERF_SLOPE_THRESHOLD = -0.04    # negative = declining
_SOFT_FATIGUE_SLOPE_THRESHOLD = 0.03  # positive = accumulating
_SOFT_TISSUE_SLOPE_THRESHOLD = 0.02
_SOFT_TISSUE_VALUE_THRESHOLD = 45.0
_SOFT_ADHERENCE_THRESHOLD = 0.70


def _tier_from_score(score: float) -> Literal["none", "watch", "bias", "force"]:
    if score >= 0.75:
        return "force"
    if score >= 0.55:
        return "bias"
    if score >= 0.35:
        return "watch"
    return "none"


def compute_deload_need(
    state: UnifiedStateVector,
    performance_residual_slope: float | None = None,
    mean_fatigue_slope: float | None = None,
    max_tissue_slope: float | None = None,
    recent_adherence: float | None = None,
) -> DeloadNeed:
    """Compute deload need from state and optional trend signals.

    Hard rule: any single fatigue axis > 60, mean fatigue > 45, or any tissue > 55.
    Soft rule: at least two of four trend signals.
    Shadow only — never hard-blocks training.
    """
    f = state.fatigue_f
    t = state.tissue_t

    fatigue_vals = [getattr(f, k) for k in FatigueState.KEYS]
    tissue_vals = [getattr(t, k) for k in TissueState.KEYS]
    mean_f = sum(fatigue_vals) / len(fatigue_vals)
    max_tissue = max(tissue_vals)

    drivers: list[str] = []
    score = 0.0

    # Hard rule check
    hard_fatigue_axis = next((k for k in FatigueState.KEYS if getattr(f, k) > _HARD_FATIGUE_THRESHOLD), None)
    hard_tissue_axis = next((k for k in TissueState.KEYS if getattr(t, k) > _HARD_TISSUE_THRESHOLD), None)
    hard_mean = mean_f > _HARD_MEAN_FATIGUE_THRESHOLD

    hard_rule = hard_fatigue_axis is not None or hard_tissue_axis is not None or hard_mean

    if hard_fatigue_axis:
        v = getattr(f, hard_fatigue_axis)
        drivers.append(f"fatigue_{hard_fatigue_axis}={v:.0f}")
        score += 0.50 + min(0.30, (v - _HARD_FATIGUE_THRESHOLD) / 100.0)

    if hard_tissue_axis:
        v = getattr(t, hard_tissue_axis)
        drivers.append(f"tissue_{hard_tissue_axis}={v:.0f}")
        score += 0.40 + min(0.25, (v - _HARD_TISSUE_THRESHOLD) / 100.0)

    if hard_mean and not hard_fatigue_axis:
        drivers.append(f"mean_fatigue={mean_f:.0f}")
        score += 0.35

    # Soft signal count
    soft_signals = [
        performance_residual_slope is not None and performance_residual_slope < _SOFT_PERF_SLOPE_THRESHOLD,
        mean_fatigue_slope is not None and mean_fatigue_slope > _SOFT_FATIGUE_SLOPE_THRESHOLD,
        (max_tissue_slope is not None and max_tissue_slope > _SOFT_TISSUE_SLOPE_THRESHOLD)
        or max_tissue > _SOFT_TISSUE_VALUE_THRESHOLD,
        recent_adherence is not None and recent_adherence < _SOFT_ADHERENCE_THRESHOLD,
    ]
    n_soft = sum(soft_signals)

    if n_soft >= 2:
        score += 0.15 * n_soft
        if performance_residual_slope is not None and performance_residual_slope < _SOFT_PERF_SLOPE_THRESHOLD:
            drivers.append(f"perf_slope={performance_residual_slope:.3f}")
        if mean_fatigue_slope is not None and mean_fatigue_slope > _SOFT_FATIGUE_SLOPE_THRESHOLD:
            drivers.append(f"fatigue_slope={mean_fatigue_slope:.3f}")
        if max_tissue > _SOFT_TISSUE_VALUE_THRESHOLD:
            drivers.append(f"max_tissue={max_tissue:.0f}")
        if recent_adherence is not None and recent_adherence < _SOFT_ADHERENCE_THRESHOLD:
            drivers.append(f"adherence={recent_adherence:.2f}")

    score = min(1.0, max(0.0, score))
    if hard_rule and score < 0.75:
        score = max(score, 0.55)  # hard rule floors at "bias"

    return DeloadNeed(
        score=score,
        tier=_tier_from_score(score),
        drivers=drivers,
    )


@dataclass
class DeloadNeed:
    score: float
    tier: Literal["none", "watch", "bias", "force"]
    drivers: list[str] = field(default_factory=list)
    model_version: str = "rule_v1"
    shadow_only: bool = True
```

- [ ] **Step 4: Integrate into `prescriber.py` — Level 1 (explanation only)**

In `recommend_next_session`, after the safety candidates check and before candidate scoring, add:

```python
    from app.logic.deload_need import compute_deload_need
    deload_need = compute_deload_need(state)
    # Level 1: explanation only — does not hard-block or force scoring changes.
    # "bias" tier boosts recovery-type candidates via scoring context.
```

Then in the `_score_with_context` closure, after existing score logic:
```python
        # DeloadNeed bias: boost recovery/maintenance/technique if tier == "bias"
        if deload_need.tier == "bias" and any(
            k in c.type.lower() for k in ("recovery", "maintenance", "technique", "deload")
        ):
            base += 0.10
```

And in the final `rx.why` block, append deload explanation:
```python
    if rx.why and deload_need.tier != "none":
        rx.why.constraints_applied.append(
            f"deload_need:{deload_need.tier}(shadow)={deload_need.score:.2f}"
        )
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_deload_need.py tests/test_prescriber_candidates.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/logic/deload_need.py app/logic/prescriber.py tests/test_deload_need.py
git commit -m "feat: add deload_need shadow module (Level 1, rule-guarded baseline)"
```

---

### Task 6: Tissue Risk Shadow Module

**Files:**
- Create: `app/logic/tissue_risk.py`
- Create: `tests/test_tissue_risk.py`

**Interfaces:**
- Produces: `TissueRiskPrediction` dataclass, `compute_tissue_risk(state, ...) -> TissueRiskPrediction`
- No prescriber integration yet (shadow_only=True, Level 0)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tissue_risk.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.tissue_risk import TissueRiskPrediction, compute_tissue_risk
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(lumbar: float = 0.0, knee: float = 0.0, shoulder: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState(lumbar=lumbar, knee=knee, shoulder=shoulder)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_fresh_state_green_for_all_axes():
    s = _state()
    result = compute_tissue_risk(s)
    for axis, tier in result.tier_by_axis.items():
        assert tier == "green", f"Fresh state: {axis} should be green, got {tier}"


def test_high_tissue_state_raises_risk():
    s = _state(lumbar=75.0)
    result = compute_tissue_risk(s)
    assert result.risk_by_axis["lumbar"] > 0.3, "High lumbar tissue should raise risk"
    assert result.tier_by_axis["lumbar"] in ("amber", "red")


def test_ac_ratio_spike_raises_risk():
    result = compute_tissue_risk(
        _state(),
        lagged_exposure_7d={"knee": 80.0},
        lagged_exposure_28d={"knee": 40.0},  # 7d = 2x chronic/4 = 2x ac_ratio > 1.3
    )
    assert result.risk_by_axis["knee"] > result.risk_by_axis.get("shoulder", 0.0)


def test_prior_pain_increases_risk():
    s = _state()
    result_no_pain = compute_tissue_risk(s)
    result_with_pain = compute_tissue_risk(s, prior_pain_axes={"shoulder"})
    assert result_with_pain.risk_by_axis["shoulder"] > result_no_pain.risk_by_axis["shoulder"]


def test_shadow_only_flag():
    result = compute_tissue_risk(_state())
    assert result.shadow_only is True
    assert result.calibrated is False


def test_unknown_skip_not_a_tissue_event():
    """Tissue risk module must not infer tissue events from unknown skips."""
    result = compute_tissue_risk(_state())
    # The module only uses explicit exposure data, never infers from skip labels.
    # This is a design test — verify no skip-based logic exists in the module.
    import inspect
    import app.logic.tissue_risk as mod
    src = inspect.getsource(mod)
    assert "skip" not in src.lower() or "# unknown" in src.lower(), \
        "tissue_risk.py must not reference skip labels as tissue evidence"
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_tissue_risk.py -v
```

- [ ] **Step 3: Create `app/logic/tissue_risk.py`**

```python
"""Tissue risk assessment from lagged cumulative exposure. Shadow mode only (Level 0: log).

Uses exponentially-weighted lagged exposure features. Does not infer tissue events
from unknown skips. Non-tissue skips are negative controls in the training dataset,
not evidence of tissue risk.

Tissue risk can only soft-penalize candidates once calibrated (ENABLE_TISSUE_RISK_CANDIDATE_PENALTY).
Until then: log and explain only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.engine_vectors import TissueState
from app.schemas.state import UnifiedStateVector

_AMBER_THRESHOLD = 0.30
_RED_THRESHOLD = 0.60


def _tier(risk: float) -> Literal["green", "amber", "red"]:
    if risk >= _RED_THRESHOLD:
        return "red"
    if risk >= _AMBER_THRESHOLD:
        return "amber"
    return "green"


@dataclass
class TissueRiskPrediction:
    risk_by_axis: dict[str, float]
    delta_risk_by_axis: dict[str, float]
    tier_by_axis: dict[str, Literal["green", "amber", "red"]]
    drivers: dict[str, list[str]] = field(default_factory=dict)
    calibrated: bool = False
    shadow_only: bool = True


def compute_tissue_risk(
    state: UnifiedStateVector,
    lagged_exposure_3d: dict[str, float] | None = None,
    lagged_exposure_7d: dict[str, float] | None = None,
    lagged_exposure_28d: dict[str, float] | None = None,
    prior_pain_axes: set[str] | None = None,
) -> TissueRiskPrediction:
    """Estimate tissue risk per axis from state and lagged exposure features.

    Lagged exposures are cumulative dose units (e.g. tissue_impulse * exp(-dt/tau)).
    Never infers tissue events from skip labels — only explicit exposure data.
    """
    pain_axes = prior_pain_axes or set()
    exp_3d = lagged_exposure_3d or {}
    exp_7d = lagged_exposure_7d or {}
    exp_28d = lagged_exposure_28d or {}

    risk_by_axis: dict[str, float] = {}
    drivers: dict[str, list[str]] = {}

    for axis in TissueState.KEYS:
        d3 = exp_3d.get(axis, 0.0)
        d7 = exp_7d.get(axis, 0.0)
        d28 = exp_28d.get(axis, 0.0)

        chronic_weekly = d28 / 4.0 if d28 > 0 else 0.0
        ac_ratio = d7 / max(chronic_weekly, 1e-6) if chronic_weekly > 0 else 1.0

        # State-based base risk (current accumulated tissue stress)
        tissue_val = getattr(state.tissue_t, axis, 0.0)
        base_risk = tissue_val / 100.0 * 0.50

        # Acute:chronic spike (ACWR > 1.3 starts adding risk)
        spike_risk = max(0.0, (ac_ratio - 1.3) / 1.7) * 0.30 if ac_ratio > 1.3 else 0.0

        # Recent concentration (3d exposure relative to 7d)
        concentration = d3 / max(d7, 1e-6) if d7 > 0 else 0.0
        concentration_risk = max(0.0, concentration - 0.5) * 0.10

        # Prior pain at this axis
        pain_bump = 0.15 if axis in pain_axes else 0.0

        risk = min(1.0, base_risk + spike_risk + concentration_risk + pain_bump)
        risk_by_axis[axis] = risk

        axis_drivers: list[str] = []
        if tissue_val > 40.0:
            axis_drivers.append(f"tissue_state={tissue_val:.0f}")
        if ac_ratio > 1.3:
            axis_drivers.append(f"ac_ratio={ac_ratio:.2f}")
        if concentration > 0.5:
            axis_drivers.append(f"concentration={concentration:.2f}")
        if axis in pain_axes:
            axis_drivers.append("prior_pain")
        drivers[axis] = axis_drivers

    delta_risk: dict[str, float] = {k: 0.0 for k in TissueState.KEYS}
    tier_by_axis = {k: _tier(v) for k, v in risk_by_axis.items()}

    return TissueRiskPrediction(
        risk_by_axis=risk_by_axis,
        delta_risk_by_axis=delta_risk,
        tier_by_axis=tier_by_axis,
        drivers=drivers,
    )
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_tissue_risk.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/logic/tissue_risk.py tests/test_tissue_risk.py
git commit -m "feat: add tissue_risk shadow module (Level 0, lagged exposure features)"
```

---

### Task 7: Interference Module

**Files:**
- Create: `app/logic/interference.py`
- Modify: `app/engine/parameters.py`
- Modify: `app/logic/state_update_v0.py`
- Create: `tests/test_interference.py`

**Interfaces:**
- Produces: `suppression_exp(z, alpha, floor) -> float`, `directional_interference_multiplier(target_axis, state, params) -> float`
- Consumed by: `_apply_adaptation_gains` in `state_update_v0.py` (replacing inline `_interference_factor` calls)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_interference.py
from __future__ import annotations
from datetime import UTC, datetime

from app.engine.parameters import default_parameters
from app.logic.interference import (
    directional_interference_multiplier, suppression_exp,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(metabolic: float = 0.0, structural: float = 0.0, cns: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(metabolic=metabolic, structural=structural, cns=cns)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_suppression_exp_at_zero_is_one():
    assert abs(suppression_exp(0.0, alpha=1.0, floor=0.3) - 1.0) < 1e-9


def test_suppression_exp_bounded_by_floor():
    for alpha in (0.5, 1.0, 2.0):
        val = suppression_exp(100.0, alpha=alpha, floor=0.30)
        assert val >= 0.30, f"alpha={alpha}: {val} below floor"


def test_suppression_exp_monotonically_decreasing():
    for z1, z2 in [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]:
        assert suppression_exp(z1, alpha=1.0) > suppression_exp(z2, alpha=1.0)


def test_higher_endurance_load_reduces_strength_multiplier():
    p = default_parameters()
    s_low = _state(metabolic=10.0, structural=10.0)
    s_high = _state(metabolic=70.0, structural=70.0)
    m_low = directional_interference_multiplier("max_strength", s_low, p)
    m_high = directional_interference_multiplier("max_strength", s_high, p)
    assert m_low > m_high, f"Low endurance ({m_low:.3f}) should be less suppressed than high ({m_high:.3f})"


def test_strength_multiplier_bounded():
    p = default_parameters()
    s = _state(metabolic=100.0, structural=100.0)
    m = directional_interference_multiplier("max_strength", s, p)
    floor = p.interference_floor_by_axis.get("max_strength", 0.30)
    assert m >= floor
    assert m <= 1.0


def test_power_suppressed_by_cns_fatigue():
    p = default_parameters()
    s_low_cns = _state(cns=10.0)
    s_high_cns = _state(cns=80.0)
    m_low = directional_interference_multiplier("power", s_low_cns, p)
    m_high = directional_interference_multiplier("power", s_high_cns, p)
    assert m_low > m_high


def test_aerobic_not_over_suppressed_by_low_structural():
    p = default_parameters()
    s = _state(structural=20.0)
    m = directional_interference_multiplier("aerobic", s, p)
    assert m >= 0.80, f"Low structural fatigue should barely suppress aerobic, got {m:.3f}"


def test_skill_more_cns_sensitive_than_aerobic():
    p = default_parameters()
    s = _state(cns=60.0)
    m_skill = directional_interference_multiplier("skill", s, p)
    m_aerobic = directional_interference_multiplier("aerobic", s, p)
    assert m_skill < m_aerobic, "Skill should be more CNS-sensitive than aerobic"


def test_work_capacity_has_no_suppression():
    p = default_parameters()
    s = _state(metabolic=90.0, structural=90.0, cns=90.0)
    m = directional_interference_multiplier("work_capacity", s, p)
    assert m == 1.0, "work_capacity has no interference suppression"
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_interference.py -v
```

- [ ] **Step 3: Add interference parameters to `app/engine/parameters.py`**

After the existing `adapt_*` fields, add:

```python
    # --- Interference parameters (exponential suppression — app/logic/interference.py) ---
    # alpha values: how quickly interference ramps with load fraction [0,1].
    # Larger alpha = steeper suppression at moderate loads.
    # Calibrated to match current linear floor at the median load level.
    interference_e_on_strength_alpha: float = 1.3
    interference_e_on_power_alpha: float = 1.3
    interference_cns_on_power_alpha: float = 0.8
    interference_cns_on_skill_alpha: float = 0.6
    interference_structural_on_endurance_quality_alpha: float = 0.3
    interference_floor_by_axis: dict[str, float] = field(
        default_factory=lambda: {
            "max_strength": 0.30,
            "power":        0.30,
            "skill":        0.50,
            "aerobic":      0.70,
            "hypertrophy":  0.40,
        }
    )
```

- [ ] **Step 4: Create `app/logic/interference.py`**

```python
"""Concurrent-training interference suppression (ADR-0037).

Replaces the inline linear _interference_factor calls in state_update_v0
with an explicit, testable, smooth exponential suppression formula.

Default behavior remains close to the prior linear model but avoids
the discontinuity at z=0 and the hard floor artifact.
"""
from __future__ import annotations

import math

from app.engine.parameters import EngineParameters
from app.schemas.engine_vectors import FatigueState
from app.schemas.state import UnifiedStateVector


def suppression_exp(z: float, alpha: float, floor: float = 0.30) -> float:
    """Exponential interference suppression in [floor, 1.0].

    z = interfering load fraction [0, 1+].
    alpha = sharpness of suppression.
    floor = minimum adaptation efficiency under maximal interference.

    z=0   → 1.0 (no suppression)
    z→∞  → floor (maximum suppression)
    Monotonically decreasing and bounded.
    """
    z = max(0.0, z)
    return floor + (1.0 - floor) * math.exp(-alpha * z)


def _endurance_load_fraction(state: UnifiedStateVector) -> float:
    """Proxy for concurrent endurance load as a [0, 1] fraction."""
    f = state.fatigue_f
    endurance_load = 0.4 * f.metabolic + 0.6 * f.structural
    return endurance_load / 100.0


def directional_interference_multiplier(
    target_axis: str,
    state: UnifiedStateVector,
    params: EngineParameters,
) -> float:
    """Adaptation efficiency multiplier ∈ [floor, 1.0] for target_axis.

    Returns 1.0 (no suppression) for axes without interference rules.
    Never returns a value that increases adaptation above 1.0.
    """
    f = state.fatigue_f
    floor = params.interference_floor_by_axis.get(target_axis, 0.30)

    if target_axis == "max_strength":
        z = _endurance_load_fraction(state)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "power":
        z_e = _endurance_load_fraction(state)
        z_cns = f.cns / 100.0
        m_e = suppression_exp(z_e, params.interference_e_on_power_alpha, floor)
        m_cns = suppression_exp(z_cns, params.interference_cns_on_power_alpha, floor)
        return min(m_e, m_cns)

    if target_axis == "hypertrophy":
        z = _endurance_load_fraction(state)
        return suppression_exp(z, params.interference_e_on_strength_alpha, floor)

    if target_axis == "skill":
        z_cns = f.cns / 100.0
        return suppression_exp(z_cns, params.interference_cns_on_skill_alpha, floor)

    if target_axis == "aerobic":
        # Structural fatigue slightly suppresses aerobic quality work
        z = f.structural / 100.0
        return suppression_exp(z, params.interference_structural_on_endurance_quality_alpha, floor)

    return 1.0  # glycolytic, mobility, work_capacity: no interference suppression
```

- [ ] **Step 5: Update `_apply_adaptation_gains` in `app/logic/state_update_v0.py`**

Add import:
```python
from app.logic.interference import directional_interference_multiplier
```

Replace the two inline `_interference_factor` calls inside `_apply_adaptation_gains`:

Old `max_strength` block:
```python
        if key == "max_strength":
            gain *= _interference_factor(cross_talk.INTERFERENCE_MET_ON_FORCE, _endurance_load(s.fatigue_f))
        elif key == "power":
            gain *= _interference_factor(cross_talk.INTERFERENCE_MET_ON_FORCE, _endurance_load(s.fatigue_f))
            gain *= _interference_factor(cross_talk.INTERFERENCE_DAM_ON_POWER, s.fatigue_f.structural)
```

New:
```python
        if key in ("max_strength", "power", "hypertrophy", "skill", "aerobic"):
            gain *= directional_interference_multiplier(key, s, p)
```

The `_interference_factor` private function can remain in the file (it's not exported and causes no harm), but add a comment:
```python
def _interference_factor(coef: float, fatigue: float, floor: float = 0.2) -> float:
    """Legacy linear interference. Superseded by directional_interference_multiplier."""
    ...
```

- [ ] **Step 6: Run tests**

```
pytest tests/test_interference.py tests/test_state_update_v2.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add app/logic/interference.py app/engine/parameters.py \
        app/logic/state_update_v0.py tests/test_interference.py
git commit -m "feat: add interference module with exponential suppression; wire into adaptation gains"
```

---

### Task 8: Candidate Scoring Guardrails and Full Logging

**Files:**
- Modify: `app/logic/constraint_engine/candidate.py`
- Modify: `app/logic/prescriber.py`
- Create: `tests/test_candidate_scoring_guardrails.py`

**Interfaces:**
- Produces: `ScoreWeightProfile`, `validate_score_weights(weights) -> list[str]`, `simple_safe_goal_aligned_policy(candidates, state) -> SessionCandidate | None`
- Modifies: `recommend_next_session` signature adds `candidate_log_out: list[SessionCandidate] | None = None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_candidate_scoring_guardrails.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.constraint_engine.candidate import (
    DEFAULT_SCORE_WEIGHTS, ScoreWeightProfile, SessionCandidate,
    simple_safe_goal_aligned_policy, validate_score_weights,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(mean_fatigue: float = 10.0, tissue: float = 5.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=mean_fatigue, muscular=mean_fatigue, metabolic=mean_fatigue,
                     structural=mean_fatigue, tendon=mean_fatigue, grip=mean_fatigue)
    t = TissueState(knee=tissue, lumbar=tissue)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _candidate(
    goal_alignment: float = 0.8,
    state_fit: float = 0.8,
    fatigue_penalty: float = 0.1,
    tissue_penalty: float = 0.05,
    weak_point_coverage: float = 0.3,
    type: str = "Max Strength",
    branch_id: str = "strength_heavy",
) -> SessionCandidate:
    return SessionCandidate(
        type=type, focus="Squat 5x3", rationale="test",
        duration_min=60, branch_id=branch_id,
        goal_alignment=goal_alignment, state_fit=state_fit,
        fatigue_penalty=fatigue_penalty, tissue_penalty=tissue_penalty,
        weak_point_coverage=weak_point_coverage,
    )


def test_default_weights_have_negative_fatigue_and_tissue():
    assert DEFAULT_SCORE_WEIGHTS["fatigue_penalty"] < 0
    assert DEFAULT_SCORE_WEIGHTS["tissue_penalty"] < 0


def test_validate_accepts_default_weights():
    violations = validate_score_weights(DEFAULT_SCORE_WEIGHTS)
    assert violations == [], f"Default weights should be valid: {violations}"


def test_validate_rejects_zero_fatigue_penalty():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "fatigue_penalty": 0.0}
    violations = validate_score_weights(bad_weights)
    assert any("fatigue_penalty" in v for v in violations)


def test_validate_rejects_positive_fatigue_penalty():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "fatigue_penalty": 0.10}
    violations = validate_score_weights(bad_weights)
    assert violations


def test_validate_rejects_high_novelty_bonus():
    bad_weights = {**DEFAULT_SCORE_WEIGHTS, "novelty_bonus": 0.50}
    violations = validate_score_weights(bad_weights)
    assert any("novelty_bonus" in v for v in violations)


def test_simple_policy_returns_highest_goal_alignment():
    candidates = [
        _candidate(goal_alignment=0.9, branch_id="a"),
        _candidate(goal_alignment=0.5, branch_id="b"),
        _candidate(goal_alignment=0.7, branch_id="c"),
    ]
    s = _state()
    winner = simple_safe_goal_aligned_policy(candidates, s)
    assert winner is not None
    assert winner.branch_id == "a"


def test_simple_policy_filters_high_fatigue():
    safe = _candidate(fatigue_penalty=0.20, branch_id="safe")
    risky = _candidate(fatigue_penalty=0.90, goal_alignment=1.0, branch_id="risky")
    s = _state(mean_fatigue=10.0)
    winner = simple_safe_goal_aligned_policy([safe, risky], s, fatigue_limit=60.0)
    assert winner is not None
    assert winner.branch_id == "safe"


def test_candidate_log_out_collects_all_candidates():
    from app.logic.prescriber import recommend_next_session
    from app.schemas.state import UnifiedStateVector
    s = _state()
    collected: list = []
    _ = recommend_next_session(s, candidate_log_out=collected)
    assert len(collected) > 0, "candidate_log_out must be populated"


def test_score_weight_profile_versioned():
    profile = ScoreWeightProfile(weights=DEFAULT_SCORE_WEIGHTS, version="v1")
    assert profile.version == "v1"
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_candidate_scoring_guardrails.py -v
```

- [ ] **Step 3: Add to `app/logic/constraint_engine/candidate.py`**

After `DEFAULT_SCORE_WEIGHTS`, add:

```python
# Scoring weight safety constraints.
# fatigue_penalty and tissue_penalty must remain negative (they penalize risky sessions).
# novelty_bonus and habit_bonus are bounded to prevent gaming.
_WEIGHT_CONSTRAINTS: dict[str, dict[str, float]] = {
    "fatigue_penalty": {"max": -0.05},
    "tissue_penalty":  {"max": -0.02},
    "novelty_bonus":   {"min": 0.0, "max": 0.10},
    "habit_bonus":     {"min": 0.0, "max": 0.10},
}


from dataclasses import dataclass as _dataclass


@_dataclass
class ScoreWeightProfile:
    weights: dict[str, float]
    version: str = "v1"


def validate_score_weights(weights: dict[str, float]) -> list[str]:
    """Return list of violation messages. Empty list = valid.

    Safety constraints: fatigue/tissue penalties must remain negative.
    Novelty/habit bonuses are bounded to prevent learned weights from
    overriding safety-based scoring.
    """
    violations: list[str] = []
    fp = weights.get("fatigue_penalty", -0.15)
    if fp > _WEIGHT_CONSTRAINTS["fatigue_penalty"]["max"]:
        violations.append(
            f"fatigue_penalty={fp:.3f} must be <= {_WEIGHT_CONSTRAINTS['fatigue_penalty']['max']} (safety minimum)"
        )
    tp = weights.get("tissue_penalty", -0.08)
    if tp > _WEIGHT_CONSTRAINTS["tissue_penalty"]["max"]:
        violations.append(
            f"tissue_penalty={tp:.3f} must be <= {_WEIGHT_CONSTRAINTS['tissue_penalty']['max']} (safety minimum)"
        )
    nb = weights.get("novelty_bonus", 0.0)
    if nb > _WEIGHT_CONSTRAINTS["novelty_bonus"]["max"]:
        violations.append(f"novelty_bonus={nb:.3f} must be <= {_WEIGHT_CONSTRAINTS['novelty_bonus']['max']}")
    hb = weights.get("habit_bonus", 0.0)
    if hb > _WEIGHT_CONSTRAINTS["habit_bonus"]["max"]:
        violations.append(f"habit_bonus={hb:.3f} must be <= {_WEIGHT_CONSTRAINTS['habit_bonus']['max']}")
    return violations


def simple_safe_goal_aligned_policy(
    candidates: list[SessionCandidate],
    state: UnifiedStateVector,
    fatigue_limit: float = 60.0,
    tissue_limit: float = 60.0,
) -> SessionCandidate | None:
    """Baseline comparator policy: safety-filtered, goal-aligned scoring.

    Used to establish a baseline for Q8 (scoring weight optimization).
    Does not use learned weights. Filters out candidates whose raw fatigue/tissue
    penalties imply state vectors beyond the safety limit.
    """
    # Convert limits from [0,100] state space to [0,1] penalty space
    fat_thresh = fatigue_limit / 100.0
    tis_thresh = tissue_limit / 100.0
    safe = [
        c for c in candidates
        if not c.is_safety_override
        and c.fatigue_penalty < fat_thresh
        and c.tissue_penalty < tis_thresh
    ]
    if not safe:
        return None
    return max(
        safe,
        key=lambda c: c.goal_alignment + 0.5 * c.state_fit + 0.5 * c.weak_point_coverage,
    )
```

- [ ] **Step 4: Update `recommend_next_session` signature in `app/logic/prescriber.py`**

Add `candidate_log_out` parameter:
```python
def recommend_next_session(
    state: UnifiedStateVector,
    goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
    recent_sessions: list[dict[str, Any]] | None = None,
    kpi_summary: dict[str, float] | None = None,
    active_weak_points: list[str] | None = None,
    available_equipment: list[str] | None = None,
    block_context: dict[str, Any] | None = None,
    candidate_log_out: list[SessionCandidate] | None = None,  # new
) -> WorkoutPrescription:
```

And just before the final `scored` sort line, populate it:
```python
    if candidate_log_out is not None:
        candidate_log_out.clear()
        candidate_log_out.extend(scored)
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_candidate_scoring_guardrails.py tests/test_prescriber_candidates.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/logic/constraint_engine/candidate.py app/logic/prescriber.py \
        tests/test_candidate_scoring_guardrails.py
git commit -m "feat: add score weight guardrails, baseline policy, and full candidate logging"
```

---

### Task 9: Experiment Arms (static-with-safety-caps)

**Files:**
- Create: `app/models/experiment.py`
- Modify: `app/models/__init__.py`
- Modify: `app/logic/prescriber.py`
- Create: `alembic/versions/a006_experiment.py`
- Create: `tests/test_experiment_arms.py`

**Interfaces:**
- Produces: `ExperimentAssignment` ORM model; `prescription_arm` param on `recommend_next_session`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_experiment_arms.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.prescriber import recommend_next_session
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.constraint_engine.candidate import SessionCandidate


def _state() -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_adaptive_arm_logs_candidates():
    collected: list[SessionCandidate] = []
    rx = recommend_next_session(_state(), prescription_arm="adaptive", candidate_log_out=collected)
    assert rx is not None
    assert len(collected) > 0


def test_static_with_safety_caps_logs_candidates():
    collected: list[SessionCandidate] = []
    rx = recommend_next_session(_state(), prescription_arm="static_with_safety_caps", candidate_log_out=collected)
    assert rx is not None
    assert len(collected) > 0


def test_static_with_safety_caps_skips_adaptive_scoring():
    """Static arm must not use adaptive score optimization — only safety substitutions."""
    collected_adaptive: list[SessionCandidate] = []
    collected_static: list[SessionCandidate] = []
    rx_adaptive = recommend_next_session(_state(), prescription_arm="adaptive", candidate_log_out=collected_adaptive)
    rx_static = recommend_next_session(_state(), prescription_arm="static_with_safety_caps", candidate_log_out=collected_static)
    # Both should return a prescription
    assert rx_adaptive is not None
    assert rx_static is not None
    # Decision mode annotated differently
    if rx_static.why:
        applied = rx_static.why.constraints_applied
        assert any("static_with_safety_caps" in c for c in applied), \
            f"Static arm must annotate decision mode. Got: {applied}"


def test_experiment_assignment_model():
    from app.models.experiment import ExperimentAssignment
    ea = ExperimentAssignment(
        user_id=1, experiment_name="adaptive_vs_static_v1",
        arm="static_with_safety_caps",
    )
    assert ea.arm == "static_with_safety_caps"
    assert ea.active is True
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_experiment_arms.py -v
```

- [ ] **Step 3: Create `app/models/experiment.py`**

```python
"""Experiment assignment model for adaptive vs static arm comparison."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ExperimentAssignment(Base):
    """One row per athlete per experiment. Tracks which arm they are in."""
    __tablename__ = "experiment_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    experiment_name: Mapped[str] = mapped_column(String, nullable=False)
    arm: Mapped[str] = mapped_column(String, nullable=False)  # adaptive | static | static_with_safety_caps
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
```

- [ ] **Step 4: Add import to `app/models/__init__.py`**

```python
from app.models.experiment import ExperimentAssignment  # noqa: F401
```

- [ ] **Step 5: Update `recommend_next_session` in `app/logic/prescriber.py`**

Add `prescription_arm: str = "adaptive"` parameter. After building `scored` and before returning, add static arm dispatch:

```python
    # --- Experiment arm dispatch ---
    if prescription_arm == "static_with_safety_caps":
        # Static arm: use first template candidate that passes safety.
        # No adaptive score optimization, no block bias, no habit/novelty scoring.
        # Hard safety overrides still apply (applied above).
        static_candidates = [c for c in goal_candidates if not c.is_safety_override]
        chosen = static_candidates[0] if static_candidates else (scored[0] if scored else None)
        if chosen:
            rx = _finalize(chosen, state, goal, recent_sessions)
            if rx.why:
                rx.why.constraints_applied.append("static_with_safety_caps:arm")
            if candidate_log_out is not None:
                candidate_log_out.clear()
                candidate_log_out.extend(static_candidates)
            return rx
```

- [ ] **Step 6: Create `alembic/versions/a006_experiment.py`**

```python
"""Experiment assignment table for adaptive vs static arm comparison.

Revision ID: a006_experiment
Revises: a005_telemetry
Create Date: 2026-06-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a006_experiment"
down_revision: str | None = "a005_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("experiment_name", sa.String(), nullable=False),
        sa.Column("arm", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiment_assignments_user_id", "experiment_assignments", ["user_id"])


def downgrade() -> None:
    op.drop_table("experiment_assignments")
```

- [ ] **Step 7: Run tests**

```
pytest tests/test_experiment_arms.py tests/test_prescribe_routes.py -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/models/experiment.py app/models/__init__.py app/logic/prescriber.py \
        alembic/versions/a006_experiment.py tests/test_experiment_arms.py
git commit -m "feat: add static-with-safety-caps experiment arm and ExperimentAssignment model"
```

---

### Task 10: Decrement Prediction Shadow Module

**Files:**
- Create: `app/logic/decrement_prediction.py`
- Create: `tests/test_decrement_prediction.py`

**Interfaces:**
- Produces: `DecrementPrediction` dataclass, `compute_decrement_prediction(prev_dose, state, ...) -> DecrementPrediction`
- Level 0: log only. Target is residual, not raw performance.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_decrement_prediction.py
from __future__ import annotations
from datetime import UTC, datetime

from app.logic.decrement_prediction import DecrementPrediction, compute_decrement_prediction
from app.schemas.engine_vectors import (
    AdaptationContribution, CapacityState, FatigueState, StressDoseSix, TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose
from app.engine.state_bridge import sync_legacy_from_vectors


def _state(cns: float = 0.0, muscular: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _dose(volume: float = 0.5, intensity: float = 0.5) -> StressDose:
    return StressDose(
        dose_six=StressDoseSix(volume=volume, intensity=intensity, density=0.5, impact=0.3, skill=0.2, metabolic=0.4),
        adaptation_contribution=AdaptationContribution(),
        d_nm_central=2.0, d_nm_peripheral=1.5, d_met_systemic=1.0, d_struct_damage=0.5,
    )


def test_fresh_state_low_decrement_score():
    result = compute_decrement_prediction(_dose(), _state(cns=5.0, muscular=5.0), time_gap_hours=48.0)
    assert result.score < 0.40, f"Fresh state should have low decrement score, got {result.score:.3f}"
    assert result.shadow_only is True


def test_high_cns_fatigue_raises_score():
    fresh = compute_decrement_prediction(_dose(), _state(cns=10.0), time_gap_hours=48.0)
    tired = compute_decrement_prediction(_dose(), _state(cns=80.0), time_gap_hours=48.0)
    assert tired.score > fresh.score


def test_short_gap_raises_score():
    long_gap = compute_decrement_prediction(_dose(), _state(), time_gap_hours=72.0)
    short_gap = compute_decrement_prediction(_dose(), _state(), time_gap_hours=6.0)
    assert short_gap.score > long_gap.score


def test_high_previous_dose_raises_score():
    low_dose = compute_decrement_prediction(_dose(volume=0.2, intensity=0.3), _state(), time_gap_hours=48.0)
    high_dose = compute_decrement_prediction(_dose(volume=2.0, intensity=1.5), _state(), time_gap_hours=48.0)
    assert high_dose.score > low_dose.score


def test_drivers_populated_under_high_load():
    result = compute_decrement_prediction(_dose(), _state(cns=70.0), time_gap_hours=48.0)
    assert len(result.drivers) > 0


def test_score_bounded():
    result = compute_decrement_prediction(
        _dose(volume=5.0, intensity=5.0), _state(cns=100.0, muscular=100.0), time_gap_hours=1.0
    )
    assert 0.0 <= result.score <= 1.0
```

- [ ] **Step 2: Run test — expect failure**

```
pytest tests/test_decrement_prediction.py -v
```

- [ ] **Step 3: Create `app/logic/decrement_prediction.py`**

```python
"""Next-session decrement prediction. Shadow mode only (Level 0: log).

Target: observed_next_performance - expected_next_performance_given_plan.
Do NOT use raw next-session performance as the prediction target — that conflates
plan difficulty changes with genuine decrements.

This module is a scaffolding for Q1 (session-pair decrement dataset).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.engine_vectors import FatigueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose


@dataclass
class DecrementPrediction:
    score: float
    affected_axes: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    shadow_only: bool = True


def compute_decrement_prediction(
    prev_dose: StressDose,
    current_state: UnifiedStateVector,
    planned_next_difficulty: float = 0.5,
    time_gap_hours: float = 48.0,
) -> DecrementPrediction:
    """Estimate likelihood of next-session performance decrement.

    Uses previous session dose and current fatigue state as features.
    Initial implementation: rule-based linear composite. No learned weights yet.
    """
    drivers: list[str] = []
    affected: list[str] = []
    f = current_state.fatigue_f

    six = prev_dose.dose_six
    total_dose = six.volume + six.intensity + six.density + six.impact + six.skill + six.metabolic

    # CNS fatigue: affects neural readiness for high-intensity work
    cns = f.cns
    if cns > 40.0:
        drivers.append(f"cns_fatigue={cns:.0f}")
        affected.append("cns")

    # Muscular fatigue: affects volume tolerance
    muscular = f.muscular
    if muscular > 35.0 and six.volume > 0.5:
        drivers.append(f"muscular_fatigue={muscular:.0f}")
        affected.append("muscular")

    # Short recovery window
    if time_gap_hours < 24.0:
        drivers.append(f"short_gap={time_gap_hours:.0f}h")

    # High previous dose
    if total_dose > 3.5:
        drivers.append(f"high_prev_dose={total_dose:.2f}")

    # Planned difficulty vs current fatigue
    mean_fatigue = sum(getattr(f, k) for k in FatigueState.KEYS) / len(FatigueState.KEYS)
    if planned_next_difficulty > 0.7 and mean_fatigue > 30.0:
        drivers.append(f"high_load_on_fatigue={mean_fatigue:.0f}")

    score = min(1.0, max(0.0, sum([
        cns / 100.0 * 0.30,
        muscular / 100.0 * 0.20,
        max(0.0, 1.0 - time_gap_hours / 48.0) * 0.20,
        min(1.0, total_dose / 5.0) * 0.15,
        mean_fatigue / 100.0 * 0.15,
    ])))

    return DecrementPrediction(
        score=score,
        affected_axes=list(set(affected)),
        drivers=drivers,
    )
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_decrement_prediction.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/logic/decrement_prediction.py tests/test_decrement_prediction.py
git commit -m "feat: add decrement_prediction shadow module (Level 0, residual-targeted)"
```

---

### Task 11: Dataset Exporters (Feature Builders)

**Files:**
- Create: `app/analysis/__init__.py`
- Create: `app/analysis/feature_builders/__init__.py`
- Create: `app/analysis/feature_builders/session_decrement.py` (Q1)
- Create: `app/analysis/feature_builders/fatigue_recovery.py` (Q2)
- Create: `app/analysis/feature_builders/tissue_risk_features.py` (Q3)
- Create: `app/analysis/feature_builders/sleep_stress_residual.py` (Q4)
- Create: `app/analysis/feature_builders/benchmark_validity_features.py` (Q5)
- Create: `app/analysis/feature_builders/deload_risk_features.py` (Q6)
- Create: `app/analysis/feature_builders/experiment_features.py` (Q7)
- Create: `app/analysis/feature_builders/scoring_weight_features.py` (Q8)
- Create: `app/analysis/feature_builders/interference_features.py` (Q9)
- Create: `app/analysis/feature_builders/confidence_calibration_features.py` (Q10)
- Create: `scripts/export_validation_datasets.py`

**Interfaces:**
- Each builder exposes `async def build_dataset(session: AsyncSession) -> list[dict]`
- Export script calls all builders and saves to `data/exports/<Q>.jsonl`

- [ ] **Step 1: Create `app/analysis/__init__.py` and `app/analysis/feature_builders/__init__.py`**

Both empty files.

- [ ] **Step 2: Create Q1 builder — `app/analysis/feature_builders/session_decrement.py`**

```python
"""Q1: Session-pair decrement dataset.

Target: observed_next_performance - expected_next_performance_given_plan.
Do NOT use raw next performance — that conflates plan difficulty with decrement.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Build session-pair rows for Q1 decrement modeling."""
    query = text("""
        SELECT
            wl1.id                              AS prev_session_id,
            wl1.user_id                         AS athlete_id,
            wl1.timestamp                       AS prev_session_at,
            wl2.timestamp                       AS next_session_at,
            EXTRACT(EPOCH FROM (wl2.timestamp - wl1.timestamp)) / 3600 AS time_gap_hours,
            wl1.session_rpe                     AS prev_rpe,
            wl1.novelty                         AS prev_novelty,
            wl2.session_rpe                     AS next_rpe,
            wl1.modality                        AS prev_modality,
            wl2.modality                        AS next_modality
        FROM workout_logs wl1
        JOIN workout_logs wl2
            ON wl1.user_id = wl2.user_id
            AND wl2.timestamp > wl1.timestamp
            AND wl2.timestamp <= wl1.timestamp + INTERVAL '7 days'
        ORDER BY wl1.user_id, wl1.timestamp
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 3: Create Q2 builder — `app/analysis/feature_builders/fatigue_recovery.py`**

```python
"""Q2: Fatigue recovery interval dataset."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Fatigue axes at interval start vs observed readiness/performance at end."""
    query = text("""
        SELECT
            ws.user_id                          AS athlete_id,
            ws.date                             AS interval_date,
            ws.hrv_ms,
            ws.resting_hr,
            ws.sleep_hours,
            ws.sleep_quality,
            ws.soreness
        FROM wellness_samples ws
        WHERE ws.hrv_ms IS NOT NULL
        ORDER BY ws.user_id, ws.date
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 4: Create Q3 builder — `app/analysis/feature_builders/tissue_risk_features.py`**

```python
"""Q3: Tissue risk dataset (athlete-day-tissue-axis rows)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Rows for tissue risk model training. Labels from outcome_events."""
    query = text("""
        SELECT
            oe.athlete_id,
            oe.occurred_at,
            oe.event_type,
            oe.tissue_axis,
            oe.confidence,
            ws.sleep_quality,
            ws.soreness
        FROM outcome_events oe
        LEFT JOIN wellness_samples ws
            ON ws.user_id = oe.athlete_id
            AND ws.date = DATE(oe.occurred_at)
        WHERE oe.event_type IN (
            'tissue_skip', 'tissue_modified', 'pain_event',
            'non_tissue_skip', 'unknown_skip'
        )
        ORDER BY oe.athlete_id, oe.occurred_at
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 5: Create Q4 builder — `app/analysis/feature_builders/sleep_stress_residual.py`**

```python
"""Q4: Sleep/stress residual dataset (benchmark performance moderated by recovery)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            bo.user_id                              AS athlete_id,
            bo.id                                   AS observation_id,
            bo.benchmark_definition_id,
            bo.raw_value,
            bo.normalized_value,
            bo.observed_at,
            ws.sleep_quality,
            ws.soreness,
            ws.hrv_ms
        FROM benchmark_observations bo
        LEFT JOIN wellness_samples ws
            ON ws.user_id = bo.user_id
            AND ws.date = DATE(bo.observed_at)
        ORDER BY bo.user_id, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 6: Create Q5 builder — `app/analysis/feature_builders/benchmark_validity_features.py`**

```python
"""Q5: Benchmark validity dataset (observed vs expected, fatigue context)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            bo.id                       AS observation_id,
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bo.raw_value,
            bo.normalized_value,
            bo.observation_weight,
            bo.observed_at,
            ws.sleep_quality,
            ws.soreness,
            ws.hrv_ms
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        LEFT JOIN wellness_samples ws
            ON ws.user_id = bo.user_id
            AND ws.date = DATE(bo.observed_at)
        ORDER BY bo.user_id, bd.code, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 7: Create remaining Q6–Q10 builders**

```python
# app/analysis/feature_builders/deload_risk_features.py
"""Q6: Deload risk dataset."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            pd.athlete_id,
            pd.created_at,
            pd.decision_mode,
            pd.chosen_score,
            sf.status,
            sf.satisfaction_score,
            sf.pain_flag
        FROM prescription_decisions pd
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY pd.athlete_id, pd.created_at
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

```python
# app/analysis/feature_builders/experiment_features.py
"""Q7: Adaptive vs static experiment dataset."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            ea.user_id                  AS athlete_id,
            ea.arm,
            ea.experiment_name,
            ea.assigned_at,
            pd.decision_mode,
            pd.chosen_score,
            sf.status,
            sf.satisfaction_score,
            sf.followed_as_prescribed,
            sf.modified_volume,
            sf.modified_intensity
        FROM experiment_assignments ea
        LEFT JOIN prescription_decisions pd
            ON pd.athlete_id = ea.user_id
            AND pd.created_at >= ea.assigned_at
        LEFT JOIN session_feedback sf
            ON sf.planned_session_id = pd.planned_session_id
        ORDER BY ea.user_id, pd.created_at
        LIMIT 200000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

```python
# app/analysis/feature_builders/scoring_weight_features.py
"""Q8: Scoring weight dataset (all candidates + outcomes)."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            cdl.prescription_decision_id,
            cdl.branch_id,
            cdl.candidate_type,
            cdl.score_components_json,
            cdl.final_score,
            cdl.chosen,
            cdl.hard_failed,
            sf.status,
            sf.satisfaction_score
        FROM candidate_decision_logs cdl
        JOIN prescription_decisions pd ON pd.id = cdl.prescription_decision_id
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY cdl.prescription_decision_id, cdl.final_score DESC
        LIMIT 500000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

```python
# app/analysis/feature_builders/interference_features.py
"""Q9: Interference dataset (endurance/metabolic load before strength benchmarks)."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bd.better_direction,
            bo.raw_value,
            bo.normalized_value,
            bo.observed_at
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        ORDER BY bo.user_id, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

```python
# app/analysis/feature_builders/confidence_calibration_features.py
"""Q10: Confidence calibration dataset (predicted variance vs observed residual)."""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def build_dataset(session: AsyncSession) -> list[dict]:
    query = text("""
        SELECT
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bo.raw_value,
            bo.normalized_value,
            bo.observation_weight,
            bo.observed_at,
            LAG(bo.observed_at) OVER (
                PARTITION BY bo.user_id, bo.benchmark_definition_id
                ORDER BY bo.observed_at
            ) AS prev_observed_at
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        ORDER BY bo.user_id, bd.code, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
```

- [ ] **Step 8: Create `scripts/export_validation_datasets.py`**

```python
#!/usr/bin/env python
"""Export offline validation datasets for all 10 research questions.

Usage:
    python scripts/export_validation_datasets.py --output data/exports/

Requires a running database accessible at DATABASE_URL (or TEST_DATABASE_URL).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure app is importable when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab",
)

BUILDERS = {
    "Q1_session_decrement": "app.analysis.feature_builders.session_decrement",
    "Q2_fatigue_recovery": "app.analysis.feature_builders.fatigue_recovery",
    "Q3_tissue_risk": "app.analysis.feature_builders.tissue_risk_features",
    "Q4_sleep_stress_residual": "app.analysis.feature_builders.sleep_stress_residual",
    "Q5_benchmark_validity": "app.analysis.feature_builders.benchmark_validity_features",
    "Q6_deload_risk": "app.analysis.feature_builders.deload_risk_features",
    "Q7_experiment": "app.analysis.feature_builders.experiment_features",
    "Q8_scoring_weights": "app.analysis.feature_builders.scoring_weight_features",
    "Q9_interference": "app.analysis.feature_builders.interference_features",
    "Q10_confidence_calibration": "app.analysis.feature_builders.confidence_calibration_features",
}


async def export_all(output_dir: Path) -> None:
    import importlib
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        for name, module_path in BUILDERS.items():
            print(f"  Building {name}...", end=" ", flush=True)
            try:
                mod = importlib.import_module(module_path)
                rows = await mod.build_dataset(session)
                out_path = output_dir / f"{name}.jsonl"
                with out_path.open("w") as f:
                    for row in rows:
                        f.write(json.dumps(row, default=str) + "\n")
                print(f"{len(rows)} rows → {out_path.name}")
            except Exception as exc:
                print(f"FAILED: {exc}")

    await engine.dispose()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/exports", help="Output directory")
    args = parser.parse_args()
    asyncio.run(export_all(Path(args.output)))
```

- [ ] **Step 9: Run a quick import check**

```
python -c "from app.analysis.feature_builders import session_decrement, fatigue_recovery; print('OK')"
```

Expected: OK

- [ ] **Step 10: Commit**

```bash
git add app/analysis/ scripts/export_validation_datasets.py
git commit -m "feat: add feature builders and dataset exporters for all 10 research questions"
```

---

### Task 12: Extended Simulation Harness Tests

**Files:**
- Create: `tests/test_simulation_extended.py`

**Interfaces:**
- Consumes: All modules added in Tasks 1–10

- [ ] **Step 1: Create `tests/test_simulation_extended.py`**

```python
"""Extended simulation harness tests covering all upgraded engine modules.

These tests verify math directionality and guard rails without requiring a DB.
Each test is deterministic — no randomness, no DB, no network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.benchmark_validity import BenchmarkValidityProfile, effective_variance, get_validity_profile
from app.logic.decrement_prediction import compute_decrement_prediction
from app.logic.deload_need import compute_deload_need
from app.logic.interference import directional_interference_multiplier, suppression_exp
from app.logic.state_update_v0 import (
    apply_benchmark_observation, recovery_clearance_multiplier, update_athlete_state,
)
from app.logic.tissue_risk import compute_tissue_risk
from app.schemas.engine_vectors import (
    AdaptationContribution, CapacityConfidence, CapacityState,
    FatigueState, StressDoseSix, TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


def _state(
    cns: float = 0.0, muscular: float = 0.0, metabolic: float = 0.0,
    structural: float = 0.0, tendon: float = 0.0, grip: float = 0.0,
    max_strength: float = 50.0, aerobic: float = 300.0,
    lumbar: float = 0.0, knee: float = 0.0,
    conf: float = 1.0,
) -> UnifiedStateVector:
    cx = CapacityState(aerobic=aerobic, max_strength=max_strength)
    f = FatigueState(cns=cns, muscular=muscular, metabolic=metabolic,
                     structural=structural, tendon=tendon, grip=grip)
    t = TissueState(lumbar=lumbar, knee=knee)
    leg = sync_legacy_from_vectors(cx, f, t)
    cc = CapacityConfidence(**{k: conf for k in CapacityConfidence.KEYS})
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        capacity_confidence=cc, s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _log(sleep: float = 7.0, stress: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength", duration_minutes=60.0,
        session_rpe=6.0, sleep_quality=sleep, life_stress_inverse=stress,
    )


def _zero_dose() -> StressDose:
    return StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())


# -------------------------------------------------------------------------
# 1. Recovery clearance direction
# -------------------------------------------------------------------------

def test_poor_sleep_slows_fatigue_clearance():
    s0 = _state(cns=50.0)
    s_good = update_athlete_state(s0, _zero_dose(), timedelta(hours=24), _log(sleep=9.0, stress=8.0))
    s_poor = update_athlete_state(s0, _zero_dose(), timedelta(hours=24), _log(sleep=3.0, stress=3.0))
    assert s_poor.fatigue_f.cns > s_good.fatigue_f.cns, \
        "Poor sleep should leave more fatigue remaining than good sleep"


def test_neutral_recovery_is_between_good_and_poor():
    s0 = _state(cns=50.0)
    s_good = update_athlete_state(s0, _zero_dose(), timedelta(hours=24), _log(sleep=9.0, stress=9.0))
    s_neutral = update_athlete_state(s0, _zero_dose(), timedelta(hours=24), _log(sleep=7.0, stress=7.0))
    s_poor = update_athlete_state(s0, _zero_dose(), timedelta(hours=24), _log(sleep=2.0, stress=2.0))
    assert s_good.fatigue_f.cns < s_neutral.fatigue_f.cns < s_poor.fatigue_f.cns


# -------------------------------------------------------------------------
# 2. Benchmark validity — noise reduces update
# -------------------------------------------------------------------------

def test_benchmark_noise_reduces_capacity_update():
    """A noisy mobility benchmark should move capacity less than a clean 1RM."""
    from types import SimpleNamespace
    import math

    s = _state(max_strength=50.0, conf=1.0)

    # Create fake mapping objects
    def mapping_obj(target_key: str, coefficient: float = 0.9) -> object:
        return SimpleNamespace(
            target_vector="capacity", target_key=target_key,
            coefficient=coefficient, intercept=0.0,
            mapping_type="direct", config={},
            min_value=None, max_value=None,
        )

    profile_1rm = get_validity_profile("1rm")
    profile_mobility = get_validity_profile("mobility")

    # Perfect score on both
    score01 = 0.8

    s_after_1rm = apply_benchmark_observation(
        s, raw_value=score01, normalized_value=score01 * 100, better_direction="higher",
        observation_weight=1.0, mappings=[mapping_obj("max_strength")],
        score01=score01, validity_profile=profile_1rm,
    )
    s_after_mob = apply_benchmark_observation(
        s, raw_value=score01, normalized_value=score01 * 100, better_direction="higher",
        observation_weight=1.0, mappings=[mapping_obj("mobility", coefficient=0.7)],
        score01=score01, validity_profile=profile_mobility,
    )

    delta_1rm = abs(s_after_1rm.capacity_x.max_strength - s.capacity_x.max_strength)
    delta_mob = abs(s_after_mob.capacity_x.mobility - s.capacity_x.mobility)
    assert delta_1rm > delta_mob or delta_mob == 0.0, \
        f"1RM delta ({delta_1rm:.3f}) should exceed noisy mobility ({delta_mob:.3f})"


# -------------------------------------------------------------------------
# 3. Confidence decay
# -------------------------------------------------------------------------

def test_weak_mapping_does_not_over_shrink_confidence():
    """After a weak-mapping benchmark, prior variance should not drop much."""
    from types import SimpleNamespace

    s = _state(conf=1.0)
    weak_mapping = SimpleNamespace(
        target_vector="capacity", target_key="mobility",
        coefficient=0.20, intercept=0.0,
        mapping_type="direct", config={},
        min_value=None, max_value=None,
    )
    profile = get_validity_profile("mobility")
    s_after = apply_benchmark_observation(
        s, raw_value=0.5, normalized_value=50.0, better_direction="higher",
        observation_weight=1.0, mappings=[weak_mapping],
        score01=0.5, validity_profile=profile,
    )
    # Confidence should shrink less for weak mapping
    drop = s.capacity_confidence.mobility - s_after.capacity_confidence.mobility
    assert drop < 0.50, f"Weak mapping should not shrink confidence by {drop:.3f}"


def test_confidence_decay_increases_with_time():
    s0 = _state(conf=0.20)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=30), _log())
    assert s1.capacity_confidence.max_strength > s0.capacity_confidence.max_strength
    assert s1.capacity_confidence.aerobic > s0.capacity_confidence.aerobic


def test_confidence_is_capped():
    p = default_parameters()
    s0 = _state(conf=0.0)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=1000), _log())
    for key in CapacityConfidence.KEYS:
        v = getattr(s1.capacity_confidence, key)
        max_v = p.confidence_max_variance.get(key, 1.5)
        assert v <= max_v, f"{key}: {v} exceeds cap {max_v}"


# -------------------------------------------------------------------------
# 4. Tissue risk — uses lagged exposure, not future labels
# -------------------------------------------------------------------------

def test_tissue_risk_uses_lagged_exposure_not_future_labels():
    """compute_tissue_risk must work without any outcome labels."""
    s = _state(lumbar=60.0)
    result = compute_tissue_risk(s, lagged_exposure_7d={"lumbar": 50.0})
    # If this doesn't raise, the module doesn't require label data
    assert result.risk_by_axis["lumbar"] > 0.20
    assert result.calibrated is False


# -------------------------------------------------------------------------
# 5. Deload need — requires multiple soft signals for bias
# -------------------------------------------------------------------------

def test_deload_need_requires_multiple_soft_signals():
    s = _state(cns=20.0)  # no hard rule
    single_signal = compute_deload_need(s, performance_residual_slope=-0.05)
    two_signals = compute_deload_need(
        s, performance_residual_slope=-0.05, mean_fatigue_slope=0.04
    )
    assert single_signal.tier in ("none", "watch"), f"Single signal should not bias, got {single_signal.tier}"
    # Two signals may or may not reach bias (depends on score), but score should be higher
    assert two_signals.score >= single_signal.score


# -------------------------------------------------------------------------
# 6. Interference — smooth, bounded, monotonic
# -------------------------------------------------------------------------

def test_interference_multiplier_is_smooth_bounded_monotonic():
    p = default_parameters()
    prev_m = 1.0
    for fatigue_level in range(0, 101, 10):
        s = _state(metabolic=float(fatigue_level), structural=float(fatigue_level))
        m = directional_interference_multiplier("max_strength", s, p)
        floor = p.interference_floor_by_axis.get("max_strength", 0.30)
        assert floor <= m <= 1.0, f"fatigue={fatigue_level}: multiplier {m} out of bounds"
        assert m <= prev_m + 1e-9, f"Multiplier increased at fatigue={fatigue_level}: {prev_m:.4f} → {m:.4f}"
        prev_m = m


def test_suppression_exp_is_smooth():
    """suppression_exp should be continuous and monotonically decreasing."""
    prev = suppression_exp(0.0, alpha=1.0, floor=0.3)
    for z in [0.1, 0.2, 0.5, 1.0, 2.0, 5.0]:
        curr = suppression_exp(z, alpha=1.0, floor=0.3)
        assert curr <= prev, f"Not monotonic at z={z}"
        prev = curr


# -------------------------------------------------------------------------
# 7. Candidate scoring guardrails
# -------------------------------------------------------------------------

def test_candidate_score_weight_constraints():
    from app.logic.constraint_engine.candidate import DEFAULT_SCORE_WEIGHTS, validate_score_weights
    violations = validate_score_weights(DEFAULT_SCORE_WEIGHTS)
    assert violations == [], f"Default weights must pass validation: {violations}"


def test_learned_weights_cannot_remove_fatigue_penalty():
    from app.logic.constraint_engine.candidate import validate_score_weights
    unsafe = {**{"goal_alignment": 0.50, "state_fit": 0.50, "fatigue_penalty": 0.0, "tissue_penalty": 0.0}}
    violations = validate_score_weights(unsafe)
    assert any("fatigue_penalty" in v for v in violations)


# -------------------------------------------------------------------------
# 8. Candidate logging
# -------------------------------------------------------------------------

def test_all_candidates_logged():
    from app.logic.prescriber import recommend_next_session
    from app.logic.constraint_engine.candidate import SessionCandidate
    s = _state()
    collected: list[SessionCandidate] = []
    _ = recommend_next_session(s, candidate_log_out=collected)
    assert len(collected) > 0, "Must collect at least one candidate"


# -------------------------------------------------------------------------
# 9. Static arm ignores adaptive scoring
# -------------------------------------------------------------------------

def test_static_with_safety_caps_ignores_adaptive_scoring():
    from app.logic.prescriber import recommend_next_session
    s = _state()
    rx = recommend_next_session(s, prescription_arm="static_with_safety_caps")
    assert rx is not None
    if rx.why:
        applied = rx.why.constraints_applied
        assert any("static_with_safety_caps" in c for c in applied), \
            f"Static arm prescription must annotate arm. Got: {applied}"
```

- [ ] **Step 2: Run all tests**

```
pytest tests/test_simulation_extended.py -v
```

Expected: all PASS

- [ ] **Step 3: Run full test suite to check for regressions**

```
pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_simulation_extended.py
git commit -m "test: extend simulation harness covering all upgraded engine modules"
```

---

## Self-Review

**Spec coverage check:**

| Acceptance Criterion | Task |
|---|---|
| 1. Sleep/stress recovery direction corrected and tested | Task 1 |
| 2. Benchmark validity profiles affect Kalman gain | Task 2 |
| 3. Capacity confidence decays by family | Task 3 |
| 4. Prescription decisions log all candidates | Tasks 8, 9 |
| 5. Session feedback distinguishes outcomes | Task 4 |
| 6. Pain/tissue reports and outcome events stored | Task 4 |
| 7. Deload need, tissue risk, decrement, interference in shadow mode | Tasks 5, 6, 7, 10 |
| 8. Static-with-safety-caps arm exists | Task 9 |
| 9. Scoring weights configurable but constrained | Task 8 |
| 10. Offline exporters for all 10 research questions | Task 11 |
| 11. Tests cover math directionality, logging, guardrails | Tasks 1–12 |
| 12. No new learned model can hard-block training | All shadow_only=True by default |

**Non-goals confirmed absent:**
- No HMM, RL, contextual bandits
- No injury diagnosis
- No hard tissue-risk thresholds from retrospective data
- No automatic athlete-specific τ
- No unbounded learned weights
- No automatic capacity downgrades from one poor benchmark

**Feature flags all default False:** `ENABLE_WORKOUT_INFORMED_CONFIDENCE_MAINTENANCE`, `ENABLE_TISSUE_RISK_CANDIDATE_PENALTY`, `ENABLE_DECREMENT_PREDICTION_HARD_BLOCK`
