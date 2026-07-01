# Task 7: Interference Module — Implementation Report

## TDD Evidence

**RED phase:** Created `tests/test_interference.py` with 9 tests. Running before any implementation
produced `ModuleNotFoundError: No module named 'app.logic.interference'` — confirmed RED.

**GREEN phase (tracer):** Created `app/logic/interference.py` with both public functions and added
interference parameters to `app/engine/parameters.py`. All 9 interference tests passed immediately.

**Regression check (GREEN):** All 12 tests in `test_state_update_v2.py` remained green before and
after the `state_update_v0.py` rewire.

---

## Real `_apply_adaptation_gains` structure

The function at lines 377–434 of `app/logic/state_update_v0.py` applied inline interference
via `_interference_factor` to **two axes only** inside the `for key in ac.KEYS` loop:

```python
if key == "max_strength":
    gain *= _interference_factor(cross_talk.INTERFERENCE_MET_ON_FORCE, _endurance_load(s.fatigue_f))
elif key == "power":
    gain *= _interference_factor(cross_talk.INTERFERENCE_MET_ON_FORCE, _endurance_load(s.fatigue_f))
    gain *= _interference_factor(cross_talk.INTERFERENCE_DAM_ON_POWER, s.fatigue_f.structural)
```

`_endurance_load(f)` = `0.4 * f.metabolic + 0.6 * f.structural` (raw, not fraction).
`INTERFERENCE_MET_ON_FORCE = 1.3`, `INTERFERENCE_DAM_ON_POWER = 0.6`.

**Note:** There is a separate `_interference_factor` call in the `d_struct_signal` bump block in
`update_athlete_state` (step 4, line 486). The brief was explicit about only rewiring
`_apply_adaptation_gains`; the step-4 call was left unchanged and continues to use
`_interference_factor`.

---

## How the Rewire Was Done

Replaced the two inline `if key == "max_strength" / elif key == "power"` blocks with:

```python
if key in ("max_strength", "power", "hypertrophy", "skill", "aerobic"):
    gain *= directional_interference_multiplier(key, s, p)
```

This **adds** interference suppression to `hypertrophy`, `skill`, and `aerobic` axes that had no
interference in the prior code. This is the brief's stated design intent.

The existing hardcoded CNS skill suppression block (lines 406–409) was **left in place**; the new
`directional_interference_multiplier("skill", ...)` is multiplicative on top of it.

`_interference_factor` was marked `"""Legacy linear interference. Superseded by directional_interference_multiplier."""`

---

## Alpha Calibration Concern (and Resolution)

The brief specified `interference_e_on_strength_alpha = 1.3`, but at that value
`test_concurrent_endurance_blunts_strength_gain` in `test_simulation_scenarios.py` **failed**:

- `gain_concurrent = 2.345 > gain_strength_only * 0.8 = 2.143`

Root cause: the prior linear model `max(0.2, 1 - 1.3*z)` at median concurrent load (z ≈ 0.394)
yields suppression = 0.488, while `suppression_exp(0.394, 1.3, 0.30)` yields 0.719 — 47% more
permissive. Per the brief: "prefer matching the old behavior at the median load by tuning alpha."

**Resolution:** Solved for alpha where `0.30 + 0.70 * exp(-alpha * 0.394) = 0.488`:
`alpha = 3.34`. Updated both `interference_e_on_strength_alpha` and
`interference_e_on_power_alpha` to `3.34` in `parameters.py`. This also exactly matches the
brief's stated calibration target ("match current linear floor at the median load level").

With alpha=3.34, `gain_concurrent = 1.846` vs threshold `1.876` (ratio 0.787 < 0.8). ✓

---

## Existing Test Behavior Shift

`test_state_update_v2.py`: All 12 tests pass with zero behavior shift — they all assert
directional inequalities, not exact values.

`test_simulation_scenarios.py::test_concurrent_endurance_blunts_strength_gain`: Would have
**failed** with the brief's suggested alpha=1.3 but **passes** with the calibrated alpha=3.34.
No test was silently edited; the alpha was tuned instead.

No test that was passing before Task 7 now fails.

---

## Ruff Result

```
uv run ruff check app/logic/interference.py app/engine/parameters.py \
    app/logic/state_update_v0.py tests/test_interference.py
All checks passed!
```

4 fixable issues were auto-corrected (import ordering in 2 files, `dict.fromkeys` in parameters.py).

---

## Files Changed

| File | Action |
|------|--------|
| `app/logic/interference.py` | Created — `suppression_exp`, `_endurance_load_fraction`, `directional_interference_multiplier` |
| `app/engine/parameters.py` | Modified — added 6 interference parameter fields |
| `app/logic/state_update_v0.py` | Modified — import + rewire of `_apply_adaptation_gains` |
| `tests/test_interference.py` | Created — 9 behavioral TDD tests |

---

## Final Test Summary

- `tests/test_interference.py`: 9/9 PASS
- `tests/test_state_update_v2.py`: 12/12 PASS (unchanged from baseline)
- `tests/test_simulation_scenarios.py`: 8/8 PASS (including previously-passing interference scenario)
- Full suite: **290 passed, 59 skipped, 0 failures**

---

## Concerns

1. **Alpha deviation from brief:** Brief suggested 1.3; calibrated value is 3.34. The higher alpha
   is required to preserve the existing `test_concurrent_endurance_blunts_strength_gain` behavior.
   The brief's comment "calibrated to match current linear floor at the median load level" supports
   3.34 mathematically.

2. **skill double-suppression:** With `directional_interference_multiplier("skill", ...)` now in
   `_apply_adaptation_gains`, skill gains face TWO CNS suppression paths: the existing
   `crosstalk_skill_suppressed_above_cns` block AND the new interference multiplier. The
   `test_cns_fatigue_suppresses_skill_gains` regression still passes, but the combined effect is
   stronger than either alone. This is the design as written in the brief; flagged for awareness.

3. **Power axis structural→CNS change:** Old code: `_interference_factor(DAM_ON_POWER, f.structural)`.
   New code: `suppression_exp(f.cns/100, interference_cns_on_power_alpha, floor)`. This changes the
   suppressing variable from structural damage to CNS fatigue. No test asserted the old behavior
   directly, so the change is invisible to the test suite but is a semantic redesign.

---

## Fix report (Task 7 review findings)

### Approach: PRIMARY — removed legacy `crosstalk_skill_suppressed_above_cns` block

The legacy threshold block (cns > 55 → gain *= max(0.5, 1-excess*0.5)) was removed from
`_apply_adaptation_gains`. `directional_interference_multiplier("skill", ...)` is now the sole
CNS-on-skill suppressor. "skill" remains in the new-multiplier axis list.

**Why primary over fallback:** The new exponential (alpha=0.6, floor=0.5) is a smooth,
calibrated replacement that produces the correct directional inequality at all CNS levels.
The existing test `test_cns_fatigue_suppresses_skill_gains` (cns=10 vs cns=85) passes cleanly
because gain_low / gain_high ≈ 0.971 / 0.800 — still directionally correct. No test required
editing.

### New integration regression test

`tests/test_state_update_v2.py::test_skill_cns_suppression_is_single_factor_not_squared`

Constructs `_state(cns=0)` and `_state(cns=80)`, runs `update_athlete_state` with a skill
dose on each, measures the gain ratio, and asserts:
- It is within 5% of the single-factor value (`suppression_exp(0.8, alpha=0.6, floor=0.5) ≈ 0.809`)
- It is strictly above the squared value (≈ 0.655) by a margin of 0.01

This makes re-introduction of a second CNS-on-skill path a hard test failure.

### Minor: power suppressor comment

Added a code comment in `app/logic/interference.py` at the `power` branch explaining the two
channels (endurance-load proxy via min(); CNS via min()) and noting this is a deliberate
modeling change from the legacy `INTERFERENCE_DAM_ON_POWER` structural product.

### Ruff result

```
uv run ruff check app/logic/state_update_v0.py app/logic/interference.py \
    tests/test_interference.py tests/test_state_update_v2.py
All checks passed!
```

### Full-suite result

```
291 passed, 59 skipped, 0 failures  (3.07s)
```

(+1 test vs Task 7 baseline of 290; the new regression test accounts for the difference.)
