What the Prescriber Is

The prescriber is a decision engine.

It is not:

a workout logger
a state updater
a static program lookup
a random exercise picker

It takes the athlete model as input and produces the next recommended session.

That makes it the part of the system that turns:

latent state
into
actionable training
Inputs to the Prescriber
1. Current athlete state S(t)

The prescriber should always start with the latest UnifiedStateVector.

Relevant state fields include:

Capacities
c_met_aerobic
c_nm_force
c_struct

These describe what the athlete is broadly capable of.

Battery
b_met_anaerobic

This affects tolerance for high-intensity work.

Fatigue channels
f_met_systemic
f_nm_peripheral
f_nm_central
f_struct_damage

These constrain what is appropriate today.

Signals
s_struct_signal

This helps distinguish pure cost from productive stimulus.

Human factors
habit_strength
skill_state

These should influence session complexity, friction, and technical demands.

2. Goal

The current API exposes goal as a query parameter to GET /v1/next-session.

Visible values across the repo/model surface include:

Strength
Hypertrophy
Power
General
Running
Hyrox
CrossFit
Calisthenics
Recomp

Goal determines the broad direction of the recommendation, but should not
override readiness constraints.

The goal tells the prescriber what to aim for.
The state tells it what is tolerable today.

3. Block context

When a mesocycle/planning layer is active, the prescriber should use:

active block goal
weekly template
planned session category
deload flags
current week within the block

This is already reflected in the schema comments:

MesocycleBlock defines weekly structure and emphasis mix
PlannedSession is the daily slot
prescribed_content is filled lazily when today’s session is opened

That means the prescriber should not invent each day from scratch if a block
exists. It should respect the block and adapt within it.

4. Weak points

Weak points are first-class bias signals.

Each weak point includes:

canonical tag
source
confidence
note
resolution state

This is a strong design.

A prescriber should not treat “weakness” as one binary flag.
It should aggregate evidence from:

self report
benchmark
inference
performance data

Active unresolved weak points should bias selection, not blindly dominate it.

Example:

if posterior_chain and grip are active, the session may include hinge or
carry emphasis
but not if current structural or peripheral fatigue makes that choice reckless
5. Equipment and profile constraints

The athlete profile provides durable constraints such as:

experience level
available days per week
session duration
equipment access
baseline benchmarks

The exercise library provides the matching surface:

modality
movement pattern
equipment requirements
load type
skill demand
impact level
weak-point tags

This means the prescriber should not choose exercises in a vacuum.
It should choose implementations the athlete can actually do.

6. Most recent training context

Even without a full explicit “training history summary” layer, the prescriber
should still care about recent context such as:

what was prescribed recently
what fatigue remains high
whether the athlete is in a deload slot
whether a benchmark was recently completed

This prevents repeated exposure to the same stress pattern when the state says
recovery is incomplete.

Output of the Prescriber

The API currently exposes a WorkoutPrescription via GET /v1/next-session.

From the current UI and PlannedSession.prescribed_content example, a useful
prescription shape includes:

type
focus
duration_min
rationale
optional exercises
optional progression / intensity detail

Example conceptual shape:

{
  "type": "Max Strength",
  "focus": "Back Squat 5x3 @ RPE 8",
  "duration_min": 60,
  "rationale": "Lower-body strength emphasis while central fatigue is acceptable.",
  "exercises": [
    {
      "name": "Back Squat",
      "sets": 5,
      "reps": 3,
      "target_rpe": 8
    }
  ]
}

The exact schema can evolve, but the output should always answer four questions:

What kind of session is this?
What is the main emphasis?
How long should it take?
Why was it chosen?

If it fails to answer the fourth question, it becomes a black box.

Decision Priorities

The prescriber should make decisions in this order.

1. Safety / recoverability

First ask:

What should the athlete not do today?

If structural damage is high, do not prescribe high-impact or heavily loaded
structural work just because it matches the long-term goal.

If central or peripheral fatigue is high, do not prescribe the most neurally
expensive option available.

This is the first gate, not a later tweak.

2. Preserve goal direction

Once unsafe or poorly timed options are removed, preserve the athlete’s
training direction.

Example:

a strength block should still mostly feel like strength training
a hypertrophy block should not drift into random conditioning
a running goal should still preserve aerobic and economy development

The prescriber should adapt within the goal, not abandon it at the first
constraint.

3. Respect block structure

If a block and planned session exist, the prescriber should stay inside that slot.

Examples:

“Heavy Lower” remains lower-body focused
“Conditioning” remains conditioning
deload weeks reduce volume and/or intensity rather than pretending recovery
does not exist

The block gives direction. The current state tunes dosage.

4. Bias toward active weak points

Only after safety, goal, and slot alignment should weak points bias the session.

This is where a lot of systems go wrong: they let a detected weakness hijack the
whole day.

A weak point should influence exercise choice, assistance work, or emphasis.
It should not always redefine the primary objective of the session.

5. Choose concrete exercises the athlete can actually perform

Only after the abstract session is determined should the prescriber choose the
exact exercise implementation.

The exercise library supports this by storing:

movement pattern
required equipment
skill demand
impact level
weak-point tags
coaching notes

This is the bridge from “what kind of stress do we want?” to “what movement
should appear on the page?”

6. Explain the choice

Every recommendation should include rationale.

Not a generic motivational sentence.
A real explanation.

Good rationale example:

“Central fatigue is moderate but structural fatigue is low, so lower-body
loading remains viable while sprint work is deprioritized.”

Bad rationale example:

“This workout will help you improve.”

One explains selection pressure. The other says nothing.

Core Heuristics

These are the main heuristics the prescriber should follow.

Heuristic 1: Fatigue channels constrain modality and intensity

Different fatigue channels should block different choices.

Examples:

high f_struct_damage → reduce plyometric/high-impact work
high f_nm_central → reduce max-force / highly technical neural work
high f_nm_peripheral → reduce local muscular stress in recently taxed areas
high f_met_systemic → reduce long or metabolically dense sessions

The exact thresholds can evolve, but the directional logic should remain.

Heuristic 2: Capacities shape ceiling, not just today’s readiness

Capacities should influence:

expected session difficulty ceiling
progression range
benchmark suitability
session density the athlete can tolerate

Capacities are not the same as freshness.
A strong but tired athlete and a weaker but fresh athlete should not get the
same prescription.

Heuristic 3: Skill demand must match skill state

The system already tracks skill_state and the exercise library tracks
skill_demand.

That implies an obvious rule:
high-skill movements should not be prescribed aggressively when the athlete’s
skill state or readiness is poor.

Otherwise the system will choose theoretically correct but practically bad sessions.

Heuristic 4: Equipment is a hard constraint, not a preference

If the athlete does not have the required equipment, the exercise should not be chosen.

This sounds trivial, but many recommendation systems fail here by treating
constraints like soft suggestions.

Heuristic 5: Deloads should change dosage, not theme

A deload week should usually preserve:

movement pattern
block intent
session slot identity

What changes is:

volume
intensity
density
exercise complexity if needed

A deload should still feel like the same program, just with lower cost.

Heuristic 6: Weak points should bias assistance more than main lift selection

This is not a hard law, but it is usually the right default.

Why:
if a weak point always replaces the primary work, the block loses coherence.

Better pattern:

main slot preserves goal
accessories, variants, or secondary emphasis address weak points
Recommended Decision Pipeline

The cleanest prescriber pipeline is:

Step 1: Determine context

Gather:

latest state
goal
active block
current planned session
active weak points
athlete profile
exercise availability
Step 2: Select abstract session type

Examples:

max strength
upper hypertrophy
threshold run
aerobic base
mixed conditioning
recovery / low-cost technical work
Step 3: Set dosage

Decide:

duration
volume target
intensity target
density / rest profile
Step 4: Choose exercise implementations

Map the abstract session onto concrete movements based on:

equipment
skill demand
weak-point tags
impact profile
Step 5: Generate rationale

Summarize why this session was selected now.

Step 6: Return structured prescription

Return the session in a format the UI and future planning layer can consume.

What the Prescriber Should Avoid
1. Chasing the last workout only

The prescriber should use modeled state, not just “what happened yesterday.”

Otherwise it collapses back into reactive workout shuffling.

2. Treating all fatigue as one number

The project explicitly separates fatigue channels.
Flattening them into one readiness score too early destroys useful information.

3. Letting weak points hijack programming

Weak-point biasing is useful.
Weak-point obsession is destructive.

4. Picking exercises before deciding session intent

Exercise selection should come after session type and dosage, not before.

5. Giving opaque rationales

If the user cannot tell why the recommendation changed, trust drops fast.

Current Visible Behavior vs Intended Behavior
Clearly visible today
there is a next-session endpoint
the frontend treats it as the source of the next recommendation
the response includes type, focus, duration_min, and rationale
the broader model clearly expects block context, weak-point biasing, and
exercise selection to feed prescription
Clearly intended by schema/comments
planned sessions are lazily filled by the prescriber
weak points are aggregated as bias signals
exercise library metadata is used for concrete movement selection
deload flags should alter prescription intensity targets
block templates should inform the day’s session category
Not confirmed from uploaded implementation
exact threshold values
exact scoring / ranking algorithm
whether generation is deterministic, LLM-based, or hybrid
exact conflict resolution when multiple fatigue channels are high

That uncertainty should stay explicit until the prescriber code is documented directly.

Suggested Architecture for the Prescriber

A strong implementation shape would be:

State + Context
    ↓
Constraint Filter
    ↓
Session Type Selector
    ↓
Dose / Intensity Tuner
    ↓
Exercise Selector
    ↓
Rationale Generator
    ↓
WorkoutPrescription

This is better than a single giant decision function because it separates:

what is forbidden
what is desired
how much stress is appropriate
how the session is concretely expressed