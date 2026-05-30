# Performance Lab Prescriber Logic

## What the Prescriber Is

The prescriber is the decision engine that turns the athlete model into the next recommended session.

It is not:

- a workout logger
- a state updater
- a static program lookup
- a random exercise picker

It reads state and context, builds candidate sessions, scores them, validates the result, then returns a structured `WorkoutPrescription`.

```text
S(t) + goal + recent history + KPIs + weak points + equipment + block context
    -> candidate pool
    -> safety/readiness redirects
    -> scoring
    -> validation / template enrichment
    -> WorkoutPrescription u(t)
```

## Inputs to the Prescriber

### 1. Current athlete state `S(t)`

The prescriber starts with the latest `UnifiedStateVector`.

Important state components:

`capacity_x`:

- aerobic
- glycolytic
- max_strength
- hypertrophy
- power
- skill
- mobility
- work_capacity

`fatigue_f`:

- cns
- muscular
- metabolic
- structural
- tendon
- grip

`tissue_t`:

- shoulder
- elbow
- wrist
- lumbar
- hip
- knee
- ankle
- finger

Legacy mirrors also remain available for compatibility:

- `c_met_aerobic`
- `c_nm_force`
- `c_struct`
- `b_met_anaerobic`
- `f_met_systemic`
- `f_nm_peripheral`
- `f_nm_central`
- `f_struct_damage`

### 2. Goal

The current API accepts these `TrainingGoal` values:

```text
Strength
Hypertrophy
Power
General
OlympicLifts
Powerlifting
MetCon
Calisthenics
Gymnastics
Grip
Running
Sprinting
HalfMarathon
FullMarathon
```

The goal determines the broad target. State determines what is tolerable now.

### 3. Recent workout context

The route gathers recent workout summaries from the last 14 days, capped at 40 rows.

Summaries include:

- modality
- duration
- RPE
- timestamp
- derived tags
- intensity bucket

This helps avoid repeated stress patterns and supports template validation.

### 4. KPI summary

The route passes latest derived KPI values to the prescriber.

Examples used in the current candidate logic include:

- `run_fatigue_factor`
- `wl_snatch_cj_ratio`
- `pl_relative_total`
- `pl_projected_total`
- `gym_pull_support_balance`

KPI signals are soft context. State vectors remain the primary controller.

### 5. Active weak points

Active unresolved weak-point tags are fetched from the database and passed to the prescriber.

Current behavior:

- tags are appended to `why.constraints_applied` as `weak_point:{tag}`
- candidate scoring has a weak-point coverage axis
- weak points bias the recommendation but do not override hard safety rules

### 6. Equipment/profile constraints

The athlete profile provides available equipment. The current prescriber uses a simple equipment-to-exercise mapping and falls back to bodyweight exercises when no matching equipment exists.

Current equipment keys in the prescriber map include:

- barbell
- dumbbells
- pullup_bar
- bodyweight fallback

The exercise library model supports deeper future selection using equipment requirements, phi vectors, skill demand, impact level, and weak-point tags.

### 7. Block and planned-session context

When an active block and today's pending planned session exist, the route passes block context:

- block goal
- session category
- deload flag
- benchmark flag
- week number

Current prescriber behavior:

- adds a +0.15 score boost when candidate type matches `session_category`
- appends `block:deload` to constraints when the planned session is a deload
- appends `block:benchmark` when the planned session is a benchmark
- route writes generated prescription content back to `PlannedSession.prescribed_content`

## Output

The API returns `WorkoutPrescription`.

Fields:

- `type`
- `focus`
- `rationale`
- `duration_min`
- `model_version`
- `exercises`
- `why`

`ExercisePrescription` includes:

- `name`
- `sets`
- `reps`
- `load_note`
- `weak_point_tags`

`PrescriptionExplanation` includes:

- `state_drivers`
- `goal_alignment`
- `constraints_applied`
- `source_alignment`
- `template_id`
- `prescription_branch`
- `validation`
- `warnings`
- `score`
- `structured_template_name`

Every recommendation should answer:

1. What kind of session is this?
2. What is the main emphasis?
3. How long should it take?
4. Why was it chosen now?
5. What constraints shaped it?

## Decision Priorities

### 1. Safety / recoverability

Hard safety overrides happen before normal scoring.

Examples from current logic:

- high lumbar or knee tissue stress -> low-impact recovery
- high tendon or structural fatigue -> tissue deload
- high structural damage scalar -> recovery
- very high systemic metabolic fatigue -> passive recovery / sleep / nutrition

Safety overrides bypass normal candidate scoring.

### 2. Readiness redirects

Soft fatigue shifts are added to the candidate pool and scored alongside goal candidates.

Examples:

- high CNS fatigue can redirect toward Zone 2 cardio or technique/flow work
- high peripheral fatigue can redirect toward active recovery or neural priming depending on goal

### 3. Goal-specific candidate generation

Each goal has candidate generators.

Current goal families:

- Strength
- Hypertrophy
- Power
- Olympic lifting
- Powerlifting
- MetCon
- Running / Sprinting / HalfMarathon / FullMarathon
- Gymnastics / Calisthenics
- Grip
- General

### 4. Block alignment

If a planned session exists, candidate scoring gets a block-context boost when the candidate type matches the planned session category.

### 5. Weak-point bias

Weak-point coverage contributes to candidate score and is included in the explanation.

### 6. Equipment-aware exercise payload

After the session is chosen, the prescriber fills `rx.exercises` with equipment-compatible options or a bodyweight fallback.

### 7. Validation and explainability

`finalize_prescription()` enriches the output with template provenance, state drivers, validation results, warnings, and optional structured-template scoring.

If hard constraints fail during finalization, the prescription is replaced with a safer recovery session.

## Candidate Model

`SessionCandidate` is the core scoring object.

Fields include:

- type
- focus
- rationale
- duration
- branch ID
- goal alignment
- state fit
- fatigue penalty
- tissue penalty
- novelty bonus
- habit bonus
- template bias
- weak-point coverage
- safety override flag
- source

Default scoring weights:

```text
goal_alignment       +0.30
state_fit            +0.25
weak_point_coverage  +0.15
fatigue_penalty      -0.15
tissue_penalty       -0.08
novelty_bonus        +0.04
habit_bonus          +0.03
template_bias        +0.05
```

The score is clamped to `[0, 1]`.

## Safety Overrides

Current hard-stop triggers include:

- `tissue_t.lumbar > 65` or `tissue_t.knee > 70`
- `fatigue_f.tendon > 55` or `fatigue_f.structural > 65`
- `f_struct_damage > 70`
- `f_met_systemic > 80`

These return recovery/tissue-deload options before goal candidates matter.

## Readiness Redirects

Current soft redirects include:

- high central fatigue -> aerobic shift or technique flow
- high peripheral fatigue -> neural priming for power/sprint/Olympic goals or active recovery otherwise

These do not automatically override the program. They compete through scoring.

## Goal-Specific Current Behavior

### Strength

Candidates include:

- Max Strength
- Skill Acquisition for low squat skill
- Strength Variety when habit is low
- Strength Volume

### Hypertrophy

Candidates include:

- High Volume Hypertrophy
- Maintenance Volume

### Power

Candidates include:

- Power Development
- Neural Priming

### OlympicLifts

Candidates include:

- Weightlifting Technique
- Strength Pulls

KPI context can bias snatch work when snatch/C&J ratio is low.

### Powerlifting

Candidates include:

- SBD Strength
- Accessory Focus

KPI context can bias volume when relative total is low.

### MetCon

Candidates include:

- Metabolic Conditioning
- Engine Work

### Running

Candidates include:

- Aerobic Base
- Threshold Work for half/full marathon or elevated fatigue factor
- Speed for Sprinting goal

### Gymnastics / Calisthenics

Candidates include:

- Gymnastics Skill
- Bodyweight Strength for Calisthenics

### Grip

Candidates include:

- Grip & Support
- Grip Recovery

### General

Candidate includes balanced GPP.

## Finalization Layer

`finalize_prescription()` adds:

- state drivers
- goal alignment
- constraints applied
- source/provenance alignment
- template ID
- branch ID
- validation summary
- warnings
- score
- structured-template name when enabled

It uses:

- program templates
- optional structured coaching templates
- constraint context built from state and recent sessions
- session draft encoding
- legacy and structured validation

If hard domain constraints are triggered, finalization replaces the recommendation with:

```text
Recovery — Easy movement + mobility (constraint override)
```

## Explanation Quality Standard

Good rationale names the selection pressure.

Good:

```text
Central fatigue is elevated, so high-neural work is deprioritized and stress is shifted toward low-neural aerobic work.
```

Bad:

```text
This workout will help you improve.
```

The first explains the model's decision. The second does not.

## What the Prescriber Should Avoid

1. Chasing only the last workout.
2. Flattening all fatigue into one number before decision-making.
3. Letting weak points override safety or block identity.
4. Picking exercises before deciding session intent.
5. Giving opaque rationales.
6. Treating equipment as a preference rather than a hard implementation constraint.
7. Ignoring benchmark/KPI context when it is available.

## Current Limitations

Implemented:

- candidate-based controller
- hard safety overrides
- readiness redirects
- goal-specific candidate pools
- block-context score boost
- weak-point explanation tags
- KPI summary input
- recent workout context input
- equipment-based exercise payload with fallback
- finalization with validation and provenance fields
- deload/benchmark explanation annotations

Still incomplete / likely next:

- deeper exercise selection directly from the `Exercise` DB table
- richer use of block templates beyond a simple category boost
- stronger deload dosage tuning from `deload_volume_factor`
- fuller weak-point aggregation by source/confidence
- more complete benchmark-specific session generation
- deterministic conflict rules when many fatigue channels are high
