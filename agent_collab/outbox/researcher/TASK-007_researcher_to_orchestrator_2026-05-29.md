---
task_id: TASK-007
from: researcher
to: orchestrator
timestamp: 2026-05-29
turn: 2
cycle: 1
status: complete
---

## Question Asked

Trace the complete exercise selection path in the prescriber and produce a findings file that tells the Coder exactly what to change to swap from hardcoded equipment-mapped lists to DB-driven selection from the `Exercise` ORM table.

---

## Files Examined

- `app/logic/prescriber.py` (full file, 836 lines)
- `app/models/exercise.py` (full file, 151 lines)
- `app/models/user.py` (full file, 79 lines)
- `app/schemas/prescription.py` (full file, 65 lines)
- `app/logic/prescription_finalize.py` (full file, 145 lines)
- `app/engine/config.py` (full file, 46 lines)
- `app/schemas/engine_vectors.py` (full file, 234 lines)
- `app/api/v1/prescribe.py` (full file, 130 lines)
- `app/api/v1/planning.py` (full file, 172 lines)
- `tests/test_prescriber_candidates.py` (lines 1–60 examined)
- `tests/test_prescriber_finalize.py` (grep-confirmed call sites)
- `tests/test_scenarios.py` (grep-confirmed call sites)

---

## Findings

### Q1 — Entry Point

`recommend_next_session` is the top-level public function.

- **File:** `app/logic/prescriber.py:761`
- **Full signature:**
  ```python
  def recommend_next_session(
      state: UnifiedStateVector,
      goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
      recent_sessions: list[dict] | None = None,
      kpi_summary: dict[str, float] | None = None,
      active_weak_points: list[str] | None = None,
      available_equipment: list[str] | None = None,
      block_context: dict | None = None,
  ) -> WorkoutPrescription:
  ```
- It is a **synchronous** function (no `async` keyword). `app/logic/prescriber.py:761`

The call sequence from entry point to exercise list attachment:

1. `recommend_next_session` (`prescriber.py:761`) → calls `_finalize` (`prescriber.py:706`) → calls `finalize_prescription` (`prescription_finalize.py:27`) — this builds `rx.why`, validates hard constraints, and returns the prescription.
2. Back in `recommend_next_session`, after `_finalize` returns: `rx.exercises = _exercise_list_for_equipment(available_equipment)` (`prescriber.py:827`).

---

### Q2 — Current Selection Mechanism: All Hardcoded Call Sites

#### 2a — `_EQUIPMENT_EXERCISE_MAP` dict

Defined at `app/logic/prescriber.py:721–741`. It is a module-level dict:

```python
_EQUIPMENT_EXERCISE_MAP: dict[str, list[tuple[str, str, str]]] = {
    "barbell": [("Back Squat", "4", "4-6"), ("Romanian Deadlift", "3", "5-8"), ("Bench Press", "4", "4-6")],
    "dumbbells": [("DB Goblet Squat", "4", "8-10"), ("DB RDL", "3", "8-10"), ("DB Floor Press", "3", "8-12")],
    "pullup_bar": [("Pull-Up", "4", "4-8"), ("Hanging Knee Raise", "3", "10-15")],
    "bodyweight": [("Tempo Squat", "4", "8-12"), ("Push-Up", "4", "8-15"), ("Split Squat", "3", "8-12/side")],
}
```

There is **no entry** for `"kettlebell"`, `"machine"`, or `"cable"` in the dict, even though `_exercise_list_for_equipment` iterates over them (`prescriber.py:748`).

#### 2b — `_exercise_list_for_equipment` function

Defined at `app/logic/prescriber.py:744–758`. It:
1. Lowercases the `available_equipment` list (`prescriber.py:745`).
2. Iterates over a fixed key priority order: `("barbell", "dumbbells", "kettlebell", "machine", "cable", "pullup_bar")` (`prescriber.py:748`).
3. Appends all matching tuples from `_EQUIPMENT_EXERCISE_MAP` into `picks`.
4. Falls back to `"bodyweight"` if nothing matched (`prescriber.py:752–753`).
5. Returns up to 4 exercises as `ExercisePrescription` objects, all with `load_note="Autoregulate by RPE"` (`prescriber.py:755–758`).

The returned list is always truncated to **at most 4 exercises** (`prescriber.py:756`, `picks[:4]`).

#### 2c — Hardcoded exercise names inside `SessionCandidate.focus` strings

These are in the `_gen_*` candidate generator functions. They appear in the `.focus` field (a plain string) and are **not** `ExercisePrescription` objects — they are free-text descriptions used in the session type/rationale, not in `rx.exercises`. Cited locations:

| Generator function | Line | Hardcoded exercises in `.focus` |
|--------------------|------|---------------------------------|
| `_gen_strength_candidates` | `prescriber.py:91` | "Back Squat 5×3 @ RPE 8 + Romanian Deadlift 3×5" |
| `_gen_strength_candidates` | `prescriber.py:106` | "Goblet Squats 3×8 (Tempo 3-1-1) + Box Squat Technique" |
| `_gen_strength_candidates` | `prescriber.py:122` | "Box Squats + Trap Bar Deadlift + Medicine Ball Slams" |
| `_gen_strength_candidates` | `prescriber.py:136` | "Front Squat 4×6 @ RPE 6–7 + Accessory Pull" |
| `_gen_hypertrophy_candidates` | `prescriber.py:163` | "Leg Press 4×12 + Hack Squat 3×15 + Leg Curl 3×12 near failure" |
| `_gen_hypertrophy_candidates` | `prescriber.py:174` | "Machine Isolation 3×10 @ RPE 7 — upper / lower split" |
| `_gen_power_candidates` | `prescriber.py:200` | "Hang Power Clean 5×3 @ RPE 6–7 + Box Jumps 4×4 (full recovery)" |
| `_gen_olympic_candidates` | `prescriber.py:239–244` | "Snatch Complex + Power Snatch 5×2 @ RPE 6–7" / "Clean & Jerk Drills + Hang Variations @ RPE 6–7" |
| `_gen_olympic_candidates` | `prescriber.py:260` | "Snatch Pull + Deadlift from Deficit 4×4 @ RPE 7" |
| `_gen_powerlifting_candidates` | `prescriber.py:288` | "Squat / Bench / Deadlift — top sets + 3–4 back-off sets" |
| `_gen_powerlifting_candidates` | `prescriber.py:302` | "Paused Squat 3×4 + Close-Grip Bench + Romanian Deadlift 3×6" |
| `_gen_metcon_candidates` | `prescriber.py:329` | "Row / Bike / KB Swings — AMRAP intervals @ sustainable pace" |
| `_gen_gymnastics_candidates` | `prescriber.py:437` | "Handstand Progressions + Ring Support Hold + Shaping Drills" |
| `_gen_gymnastics_candidates` | `prescriber.py:452` | "Pull-ups / Dips / Push-up Variations + Skill Progressions" |
| `_gen_grip_candidates` | `prescriber.py:479` | "Farmer Carries + Dead Hangs + Pinch Block Hold + Crush Work @ RPE 7–8" |
| `_safety_candidates` | `prescriber.py:542` | "Low-Impact Mobility + Swim / Bike Easy" |
| `_safety_candidates` | `prescriber.py:553` | "Isometrics + Blood-Flow Circuits" |
| `_readiness_redirect` | `prescriber.py:609` | "Zone 2 Cardio (Bike / Row) @ RPE 4–5" |

Note: these `.focus` strings flow into `WorkoutPrescription.focus` (`prescription.py:59`) which is a plain `str` field, not a structured exercise list. The DB migration for Q2c would require either replacing `.focus` generation or adding a separate exercise list generated from DB results.

---

### Q3 — Output Schema

**`ExercisePrescription`** — defined at `app/schemas/prescription.py:44–50`:

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | Required |
| `sets` | `int \| None` | Optional |
| `reps` | `str \| None` | Optional (string to allow "8-12/side") |
| `load_note` | `str \| None` | Optional; currently always "Autoregulate by RPE" |
| `weak_point_tags` | `list[str]` | Default empty list |

**`WorkoutPrescription`** — defined at `app/schemas/prescription.py:53–64`:

| Field | Type | Notes |
|-------|------|-------|
| `type` | `str` | Session type name |
| `focus` | `str` | Free-text session description |
| `rationale` | `str` | Prescriber reasoning |
| `duration_min` | `int` | Session duration |
| `model_version` | `str` | Default `"v0.3"` |
| `exercises` | `list[ExercisePrescription]` | Default empty list; populated by `_exercise_list_for_equipment` |
| `why` | `PrescriptionExplanation \| None` | Explainability block |

`ExercisePrescription.weak_point_tags` is already defined in the schema (`prescription.py:50`) but is **never populated** by the current hardcoded path — `_exercise_list_for_equipment` always constructs `ExercisePrescription(name=name, sets=int(sets), reps=reps, load_note="Autoregulate by RPE")` with no `weak_point_tags` (`prescriber.py:756–758`).

---

### Q4 — Exercise ORM Table Schema

Table name: **`exercises`** (`app/models/exercise.py:27`)

Columns relevant to exercise selection filtering:

| Column | Type | Nullable | Relevance |
|--------|------|----------|-----------|
| `name` | `String` | NOT NULL, unique, indexed | Display name for `ExercisePrescription.name` |
| `modality` | `String` | NOT NULL, indexed | Maps to TrainingGoal: "Strength", "Hypertrophy", "Power", "Running", "Conditioning", "Calisthenics", "Mixed" (`exercise.py:33–37`) |
| `movement_pattern` | `String` | NOT NULL, indexed | Fine-grained pattern: "squat", "hinge", "push_horizontal", "push_vertical", "pull_horizontal", "pull_vertical", "carry", "run", "row", "bike", "jump", "rotation", "core", "mixed" (`exercise.py:39–45`) |
| `pattern_family` | `String` | nullable, indexed | Broader family grouping: "squat_family", "hinge_family", "press_family", "pull_family", "locomotion" (`exercise.py:46–51`) |
| `equipment_required` | `ARRAY(String)` | default=list | Tags must match `AthleteProfile.equipment`; empty = bodyweight (`exercise.py:65–69`) |
| `load_type` | `String` | NOT NULL | "barbell", "dumbbell", "bodyweight", "machine", "cable", "kettlebell", "distance", "time", "reps" (`exercise.py:72–76`) |
| `weak_point_tags` | `ARRAY(String)` | default=list | e.g. ["grip", "posterior_chain", "aerobic_base", "hip_hinge"] — used to bias selection toward flagged weak points (`exercise.py:128–132`) |
| `is_benchmark` | `Boolean` | default=False | Marks exercises for periodic re-test / assessment protocols (`exercise.py:135–138`) |
| `sport_domains` | `ARRAY(String)` | default=list | e.g. "powerlifting", "weightlifting", "crossfit", "gymnastics" (`exercise.py:79–82`) |
| `skill_demand` | `Float` | default=0.5 | 0–1; can filter against athlete skill level from `state.skill_state` (`exercise.py:91–93`) |
| `recovery_cost` | `Float` | default=0.5 | 0–1; can be used alongside fatigue state to filter high-cost exercises (`exercise.py:105–108`) |

Additional columns present but less critical for initial selection filtering: `unilateral`, `rom_demand`, `contraction_bias`, `primary_muscles`, `secondary_muscles`, `scalable_by`, `technical_ceiling`, `impact_level`, `novelty_penalty`, `phi_adapt`, `phi_fatigue`, `phi_tissue`, `energy_mix`, `coaching_notes`, `meta`.

**AthleteProfile equipment column** — the user's equipment is stored at `app/models/user.py:68`:
```python
equipment = Column(ARRAY(String), default=list)
```
This is already passed into `recommend_next_session` as `available_equipment` from both call sites (`prescribe.py:112`, `planning.py:155`).

---

### Q5 — Gap Analysis: Call Sites vs ORM Columns

#### Primary gap: `_exercise_list_for_equipment` + `_EQUIPMENT_EXERCISE_MAP`

| Current mechanism | File:line | Data available at call site | ORM columns to replace it |
|-------------------|-----------|-----------------------------|-----------------------------|
| Dict key lookup `_EQUIPMENT_EXERCISE_MAP[key]` for each equipment tag | `prescriber.py:748–753` | `available_equipment: list[str]` | `Exercise.equipment_required` (ARRAY overlap) or `Exercise.load_type` |
| Bodyweight fallback when no match | `prescriber.py:752–753` | `available_equipment` is empty / None | `Exercise.equipment_required == []` (empty array) |
| Hardcoded sets/reps tuples `("Back Squat", "4", "4-6")` | `prescriber.py:723–741` | none — fully static | No ORM column for sets/reps; these are prescription parameters, not exercise attributes. The DB stores structural info (load_type, skill_demand, etc.), not session-specific volume. |
| `load_note="Autoregulate by RPE"` always | `prescriber.py:757` | none — fully static | N/A — this is a prescription convention, not stored in the DB |
| `weak_point_tags` always empty | `prescriber.py:756–758` | `active_weak_points: list[str]` passed to `recommend_next_session` (`prescriber.py:785`) | `Exercise.weak_point_tags` (ARRAY overlap with `active_weak_points`) |
| Goal/modality not used in exercise selection at all | `prescriber.py:744–758` | `goal: TrainingGoal` available in `recommend_next_session` scope | `Exercise.modality`, `Exercise.sport_domains` |

**Summary of what a DB query for `_exercise_list_for_equipment` would filter on:**
1. `Exercise.equipment_required` must be a subset of `available_equipment` (or empty for bodyweight).
2. `Exercise.modality` should match the session's `goal` (e.g., goal="Strength" → modality="Strength").
3. `Exercise.weak_point_tags` overlap with `active_weak_points` for bias/ordering.
4. `Exercise.is_benchmark` can be used to include/exclude benchmark exercises based on `block_context["is_benchmark"]`.
5. `Exercise.skill_demand` could be filtered against `state.capacity_x.skill` to avoid exercises too technically demanding for the athlete's current level.

#### Secondary gap: `SessionCandidate.focus` strings

These strings (`prescriber.py:91`, `prescriber.py:106`, etc.) are used as `WorkoutPrescription.focus` which is a plain `str`. They are not structured exercise lists. Replacing them with DB data would require either:
- Continuing to use them as free-text session labels (no ORM column maps directly), or
- Deriving them from `Exercise.name` values returned by the DB query and concatenating.

No single ORM column maps to `.focus`. This is a separate concern from `rx.exercises`.

---

### Q6 — Risk: Non-Async Call Sites

`recommend_next_session` is currently a **synchronous** function (`prescriber.py:761`, no `async` keyword). Every call site listed below calls it without a db session:

| Call site | File:line | Context | db available? |
|-----------|-----------|---------|---------------|
| `prescribe.py` route `get_next_session` | `prescribe.py:106` | `async def get_next_session(... db: AsyncSession ...)` | Yes — `db` is in scope |
| `planning.py` route `get_today` | `planning.py:146` | `async def get_today(... db: AsyncSession ...)` | Yes — `db` is in scope |
| `tests/test_prescriber_candidates.py` | `:103, :109, :189, :198, :214, :226–227, :236, :243` | Synchronous pytest functions, no db | No — all tests call without db |
| `tests/test_prescriber_finalize.py` | `:29, :55` | Synchronous pytest functions, no db | No |
| `tests/test_scenarios.py` | `:158, :187, :245` | Synchronous pytest functions, no db | No |

**Risk summary:**

1. If `recommend_next_session` is changed to `async` and given a required `db: AsyncSession` parameter, **all 11+ test call sites break** because they are synchronous and have no db session.
2. The two API call sites already have an `AsyncSession` in scope and could pass it through without structural change.
3. The safest migration pattern would be to keep `recommend_next_session` synchronous and extract a new async helper (e.g., `fetch_exercises_for_equipment`) that is called by the API routes **before** calling `recommend_next_session`, then passes the result in via the existing `available_equipment` parameter (or a new `exercises: list[ExercisePrescription]` override parameter). This avoids touching the synchronous prescriber core and all existing tests.
4. Alternatively, `_exercise_list_for_equipment` could be split into a sync stub (for tests) and an async DB version called only from async contexts, with `recommend_next_session` accepting a pre-fetched `exercises` override.

---

## Ambiguities / Open Questions

1. **Sets/reps from DB:** The `Exercise` ORM has no sets/reps columns. The hardcoded tuples in `_EQUIPMENT_EXERCISE_MAP` include sets and reps. The Coder will need to decide a default prescription scheme (e.g., derive from `load_type` + goal, or add a `default_sets`/`default_reps_range` column to the ORM).

2. **Ordering/ranking:** The ORM has `skill_demand`, `recovery_cost`, and `weak_point_tags` that could be used to rank exercises, but there is no existing scoring function for exercise-level selection — only session-level candidate scoring (`prescriber.py:800–807`). A new ranking function would be required.

3. **Empty `exercises` table:** The `exercises` table is schema-defined but there is no evidence of seed data or migrations populating it in the files examined. If the table is empty at runtime the fallback behavior must be defined.

4. **`goal` is not passed to `_exercise_list_for_equipment`:** At `prescriber.py:827`, only `available_equipment` is passed. The `goal` and `active_weak_points` variables are in scope at `prescriber.py:784–785` but not forwarded. A DB version would need these passed explicitly.

---

## Recommendation for Coder

The minimum-friction migration path is: create an async function `fetch_exercises_from_db(db, available_equipment, goal, active_weak_points, is_benchmark)` in a new module (e.g., `app/logic/exercise_selector.py`) that queries `exercises` filtering on `equipment_required`, `modality`, and optionally `weak_point_tags`, then call it from the two API route handlers (`prescribe.py:106`, `planning.py:146`) and pass the result as a new optional `exercises` override to `recommend_next_session`, replacing the `_exercise_list_for_equipment` call at `prescriber.py:827` only when the override is provided. This keeps `recommend_next_session` synchronous, leaves all existing tests unmodified, and enables DB-driven selection at both API call sites.
