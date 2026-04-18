Performance Lab Data Model
Purpose

This document explains how Performance Lab stores athlete identity, baseline information, workout history, internal state, planning data, weak-point signals, and exercise metadata.

The core rule is simple:

Do not confuse what happened, what the system believes, and what the system plans next.

That rule drives the entire schema.

Data Model Principles
1. Event data and state data are different

A workout log is a record of an event.
An athlete state row is the model’s interpretation after processing events.

Those should stay separate.

2. State is historical, not mutable-in-place

The system stores a time series of athlete states rather than one current-state row.

3. Plans are not logs

A planned session is an intended future slot.
A workout log is what actually occurred.

4. Weak points are probabilistic signals

Weakness is not stored as a single permanent trait.
It is stored as a tagged signal with source and confidence.

5. Exercise selection is metadata-driven

Concrete exercise prescription should come from structured tags rather than hard-coded branching.

Entity Overview
User
 └─ AthleteProfile (1:1)

User
 ├─ AthleteState (1:N)
 ├─ WorkoutLog (1:N)
 ├─ MesocycleBlock (1:N)
 └─ WeakPoint (1:N)

MesocycleBlock
 └─ PlannedSession (1:N)

PlannedSession
 └─ WorkoutLog (0:1 fulfillment link)

Exercise
 └─ referenced by prescriber logic, not owned by user
Entity Categories
Identity and baseline
User
AthleteProfile
Event history
WorkoutLog
Modeled internal history
AthleteState
Planning layer
MesocycleBlock
PlannedSession
Bias / inference layer
WeakPoint
Prescription library
Exercise
User

Represents the account-level identity.

Key fields
id
email
hashed_password
is_active
created_at
Relationships
one AthleteProfile
many AthleteState
many MesocycleBlock
many WeakPoint
Role in the system

User is the root owner for almost every athlete-specific object.

Notes

This is intentionally thin. Identity should stay separate from training logic.

AthleteProfile

Represents onboarding and relatively stable baseline information.

Key fields
experience years / level
available days per week
session duration
equipment access
baseline lifts / benchmarks
bodyweight / height
Relationship
one-to-one with User
Role in the system

This is the athlete’s baseline configuration layer. It seeds initial assumptions and constrains prescription.

Why it matters

Without a profile layer, the prescriber has no durable source for:

equipment constraints
schedule reality
baseline benchmark context
Source of truth

This is the source of truth for relatively stable setup information, not the evolving athlete state.

AthleteState

Represents the persisted unified athlete state S(t).

Key fields
Capacities
c_met_aerobic
c_nm_force
c_struct
Battery
b_met_anaerobic
Fatigues
f_met_systemic
f_nm_peripheral
f_nm_central
f_struct_damage
Signals
s_struct_signal
Human factors
habit_strength
skill_state
Relationship
many states per user over time
Role in the system

This is the engine’s internal belief state after processing the athlete’s training history.

Source of truth

AthleteState is the source of truth for current modeled readiness and capacity.

Important design choice

Each update creates a new row.
That means this table is a state history, not a mutable profile snapshot.

Why not store just one row?

Because you lose:

replayability
auditability
trend analysis
model-version migration options
WorkoutLog

Represents a persisted workout event.

Key fields
user_id
optional planned_session_id
logged_at
session_timestamp
modality
duration
session RPE
optional RIR
optional distance
optional volume
sleep quality
inverse life stress
dose_snapshot
benchmark flags / benchmark results
Relationship
belongs to User
may fulfill one PlannedSession
Role in the system

This is the event-history layer for what actually happened.

Important design choice

The table stores dose_snapshot.

That is useful because the dose engine can evolve over time. Storing the calculated dose at log time gives you an audit trail and supports later comparisons or replay strategies.

Distinction from DTO

There is both:

a Pydantic WorkoutLog schema for API input
a SQLAlchemy WorkoutLog model for persistence

That distinction should stay explicit in docs and naming, because the name overlap can confuse new contributors.

MesocycleBlock

Represents a macro training block.

Key fields
goal
status
duration weeks
sessions per week
start / end date
modality_mix
weekly_template
rationale
deload settings
Relationship
belongs to User
has many PlannedSession
Role in the system

This is the long-range planning container. It stores training intent over a multi-week period.

Important fields

weekly_template is especially important because it defines the recurring structure of the block.
modality_mix defines the intended emphasis split.

Why it exists

Without blocks, the engine can only react locally.
With blocks, it can combine strategic direction with daily adaptation.

PlannedSession

Represents a scheduled training slot inside a block.

Key fields
block_id
user_id
scheduled_date
week_number
day_of_week
category
modality
status
prescribed_content
optional workout_log_id
is_deload
Relationship
belongs to MesocycleBlock
may be linked to a WorkoutLog
Role in the system

This is the bridge between macro planning and daily prescription.

Important design choice

prescribed_content is populated lazily.
That means the block can define session slots first, and the exact content can be generated later using fresh state data.

That is the correct pattern for an adaptive training system.

WeakPoint

Represents a flagged limitation for a user.

Key fields
tag
source
confidence
note
detected_at
resolved_at
optional source_session_id
Source types
self report
benchmark
inference
performance data
Relationship
belongs to User
Role in the system

This is the targeted-bias layer for prescription.

Why confidence matters

A self-reported weak point and a benchmark-derived weak point should not carry identical weight.
Storing source and confidence lets the prescriber aggregate evidence instead of treating all weakness labels as equal.

Why separate rows instead of one merged row per tag?

Because multiple independent signals can point to the same weak point over time.

Exercise

Represents a library entry for movement selection.

Key fields
name
modality
movement pattern
muscles
equipment requirements
load type
skill demand
impact level
weak-point tags
benchmark flag
coaching notes
metadata
Relationship

This is seed / library data, not user-owned data.

Role in the system

This table lets the prescriber turn abstract training goals into concrete exercise selections.

Why it matters

The prescriber should not hard-code every exercise decision.
This table makes exercise selection data-driven and easier to expand across modalities.

Source of Truth by Layer
User / AthleteProfile   = identity + stable setup
WorkoutLog              = observed event history
AthleteState            = modeled internal history
MesocycleBlock          = strategic plan
PlannedSession          = scheduled tactical slot
WeakPoint               = targeted bias signals
Exercise                = movement library for implementation

This separation should remain visible in both code and docs.

Typical Data Lifecycle
1. Athlete onboarding

Create:

User
AthleteProfile

Optionally seed:

initial WeakPoint rows
first MesocycleBlock
2. State initialization

Create baseline AthleteState if no history exists.

3. Training day

User opens today’s planned session or requests a next session.

The prescriber reads:

latest AthleteState
active block / planned session
active weak points
exercise library
athlete profile constraints
4. Workout completion

Create a WorkoutLog.

Then:

compute StressDose
update state
persist a new AthleteState
optionally connect the log to the planned session
5. Ongoing adaptation

Weak points can be:

added
reinforced from new evidence
resolved when corrected

Blocks and planned sessions advance independently of raw event history.

Why the Split Between WorkoutLog and AthleteState Is Correct

This is the most important data-model decision in the repo.

A single table cannot cleanly represent both:

what the athlete did
what the model believes that did to the athlete

Those are different things.

Concrete failure case if they are merged:

You change the dose formula later
now old rows are ambiguous
you no longer know whether stored values are raw inputs or model outputs
replay becomes painful or impossible

Keeping logs and state separate avoids that trap.

Recommended Constraints and Conventions
Naming

Be explicit when names overlap between DTOs and ORM models.

Example:

API schema: WorkoutLogIn
DB model: WorkoutLog

That is not required, but it would reduce confusion.

Time

Use consistent semantics for:

event timestamp
row creation timestamp
modeled state timestamp

Right now the schema already distinguishes these reasonably well.

Append-only state

Treat AthleteState rows as immutable historical snapshots.

Weak point resolution

Do not delete resolved weak points; use resolved_at.

Planned vs completed

Do not overwrite PlannedSession with raw observed session data.
Link it to WorkoutLog instead.

Open Model Questions

These are worth documenting explicitly as the project grows:

1. How should model versioning be tracked?

As of v0.3, `model_version = "v0.3"` is stored as a field on both:

- `UnifiedStateVector` (every persisted AthleteState row carries the engine version)
- `WorkoutPrescription` (every prescription response carries the engine version)

This gives a lightweight audit trail. If dose logic changes in a future version,
consumers can detect which formula set produced each row by inspecting
model_version. Full replay-from-logs and per-dose-snapshot versioning remain
open questions as the project scales.
2. How should active state be queried?

Current pattern is “latest row by timestamp.”
That is fine now, but eventually you may want:

cached latest-state view
materialized summary
state-version tagging
3. How should weak-point aggregation work?

Multiple rows may point to one tag.
The aggregation rule belongs in documented prescriber logic.

4. How should planned sessions handle rescheduling?

The current model supports status changes, but the behavioral rules should be documented.