# Contextual terminology for the athlete‑training domain

## AthleteContextRepository
A **repository** abstraction that provides the *data‑access boundary* for loading and persisting the athlete‑context records needed by services such as workout, prescription, planning, benchmark, and dashboard. It owns only persistence mechanics (fetching, inserting, linking, updating) and never contains domain logic like dose calculation, state updates, or benchmark mapping.

## Athlete context
The set of database‑backed records that together allow the system to make or update an athlete‑specific recommendation: profile, current state, recent workouts, active weak points, active block/session context, equipment, KPI snapshots, and benchmark observations. The repository is responsible for reading and writing these records.

## Repository (general definition)
A **repository** is a persistence boundary used by services to read/write ORM records. Repositories must not contain training‑engine decisions, dose calculations, or readiness logic; those belong in the service layer.

## Prescribe-and-persist
The single service pathway that turns an athlete's current context into a next-session recommendation **and** writes it back into a `PlannedSession.prescribed_content` slot. `prescription_service.prescribe_for_athlete` owns it end to end: state load/init, ADR-0030 goal resolution, signal gathering (recent sessions, KPIs, active weak points), block-context assembly, scoring, and persistence. HTTP routes (`/next-session`, `/planning/today`) delegate rather than reimplement — `/planning/today` passes the `PlannedSession` it will display as the persist target, so the displayed session is always the one written to. The serialized shape lives in one place, `WorkoutPrescription.to_prescribed_content()`, which `state_service` reads back by string key (ADR-0031). Any route that instead reassembles this pipeline inline is a divergence risk, not a variant.

## AthleteContextRepository — interface contract
The seam is being built incrementally, one migrated query at a time; every method has real callers (no aspirational stubs).

- **Returns ORM rows**, never domain vectors. Callers convert rows to domain objects via `app.engine.state_bridge` (`unified_from_athlete_row`) in the service/engine layer. Returning domain vectors would pull that mapping into the repository — the domain‑logic leak this boundary exists to prevent.
- **Loading current state:** services and routes call `state_service.load_current_state` (read‑or‑`None`) or `state_service.load_or_init_current_state` (read‑or‑init) rather than re‑pairing `get_latest_state` with `unified_from_athlete_row` by hand. These loaders own that fetch→convert(→auto‑init) pairing in one place, above the repository seam — don't re‑scatter it back into callers. (The atomic staged‑baseline path inside `process_new_workout` stays bespoke: it stages the baseline for an atomic commit with the workout, so it can't use the committing loader.)
- **Transaction ownership stays with services** for now: services own `commit`; the repository owns query/read/write mechanics. A later slice may consolidate the commit boundary here.
- **Construction:** services build `AthleteContextRepository(session)` from the `AsyncSession` they already hold; routes may use a `get_athlete_repo` FastAPI dependency.
- **Tested** through the real interface against the `async_db` Postgres fixture — no in‑memory fake (a second adapter would be a hypothetical seam).

## Session (logged)
One training occasion an athlete records. It is a **heterogeneous bag of sets** that may span modalities (a run *and* squats *and* wall-balls in one sitting). Its `modality` is not chosen — it is **derived** from the sets it contains (uniform → that modality, otherwise `Mixed`). Distinct from a `PlannedSession` (what was prescribed) and from a `MesocycleBlock` (the strategic container).
_Avoid_: workout (ambiguous — can mean the plan or the record), running log.

## Set
The **atomic logged unit** — one working effort of a single exercise. It references a movement-library `Exercise`, and that exercise's `load_type` decides which metrics the set carries (load+reps, reps+assist, duration, or distance). Straight sets are entered once and expanded into individual editable sets so the top set and per-set effort survive.
_Avoid_: rep, entry.

## Movement-library Exercise
A seed-data catalog entry (`exercises`) describing a movement — its `load_type`, `movement_pattern`, phi vectors, `sport_domains`, and benchmark flag. It is the identity a logged `Set` binds to, and the switch that types a set's fields. A movement absent from the catalog is logged as **free-text** (estimated phi, no benchmark linkage) until promoted.
_Avoid_: lift, activity.

## Derived modality
A session's modality **label computed from its sets**, never an input. Contrast the running-era assumption that a session *is* a single modality chosen up front.

## Skill state
A **domain-filtered projection** — a *view*, not a state store — over the athlete's unified state: the whole-body `capacity.skill` anchor, the per-movement `skill_state` map, skill/technique benchmark observations, and weak-point tags, filtered to the athlete's active domains. Running economy shows only under a running lens; technical grade only under lifting. There is no `skill.running.cadence` axis — domain is a lens, not a taxonomy.
_Avoid_: skill axis, skill taxonomy, per-domain skill.

## SkillEvidence
One item in a skill view: a labelled piece of evidence with a `source` (capacity / movement / benchmark / weak-point / rating), a `value` that is **`null` when unknown** (never `0`), a `status` (`measured | estimated | not_measured`), and a confidence. The unit that makes "not yet measured" honest.

## Assessable skill tag
A skill/technique label attached to a benchmark or weak-point definition **that carries a real `measurement_protocol`** (`assessable_skill_tags`). It is the anchor for a "not yet measured" item — a skill may be shown as unmeasured only if such a tag exists for it. A label without a protocol is a backlog idea, not an assessable skill.
_Avoid_: skill def, technique axis.

## Weak-point tag
A **hypothesis / routing label** ("this athlete may have a running-economy limitation"), not a state dimension. It biases candidate scoring, benchmark suggestion, and display — but never creates a canonical state axis on its own.
_Avoid_: weakness axis, deficit state.

## Assessment surface
The single, domain-filtered surface for entering or performing a benchmark. It runs in **onramp** mode (during onboarding, seeding the twin) or **retest** mode (ongoing) — the difference is framing, not data. Every submission writes a `benchmark_observation`; there are no domain-specific seeders and no privileged running Field Test.
_Avoid_: field test, compute-metrics screen.

## Measurement debt
The set of an athlete's state axes / assessable skills that are **estimated or not-yet-measured** rather than benchmark-backed. Surfaced in-app as honest, in-context prompts to sharpen the twin — never as an onboarding failure or a block.

## Confidence gate
The rule that **confidence acts, not just displays**: per-axis confidence continuously tightens the engine's aggressiveness ceiling, and thresholds suppress strong discrete claims (race prediction, high-confidence tissue-risk). Distinct from a **safety override** — a confidence gate means "we don't know you well enough to push," a safety override means "this would hurt you."

## Wellness signal state
The status of one daily-wellness signal (HRV, sleep, RHR, soreness, mood): **untracked** (a persistent user preference — hidden, never expected), **unknown today** (tracked but absent — a visible gap that lowers confidence), or **provided** (measured). Only *provided* values enter readiness; missing is never silently imputed. The 28-day personal baseline is display-only interpretation, not an input.
_Avoid_: default value, filled, imputed reading.

## Training emphasis
The `modality_mix` (a `domain → weight` blend) the prescriber actually pursues, **computed from the athlete's active objectives** (weighted by priority × proximity × gap × status, smoothed), then floored and override-adjusted. Replaces the single-goal bottleneck. `primary_goal`/`block_goal` survive only as a fallback when no objectives exist.
_Avoid_: primary goal, the goal.

## Planning override
User-declared structural intent the prescriber honors — pin/exclude a modality, phase, block goal, or frequency. Carries an **authority**: a **hard override** is honored unless a safety gate forbids it; a **soft preference** the optimizer may trade off. A hard override that deviates from the efficient path is honored *and* its objective cost is surfaced — never silently corrected.
_Avoid_: setting, tweak.

## Authority stack
The fixed precedence the planner resolves intent through: **safety** (absolute) → **user hard override** → **objectives / floors** → **optimizer** → **tradeoff explanation**. Shorthand: the user owns intent and structure; the engine owns safety, feasibility, and execution quality.
