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
- **Construction:** services build `AthleteContextRepository(session)` from the `AsyncSession` they already hold. No route constructs it directly today — every real construction site lives inside `state_service.py`; routes go through its loader functions instead. There is no `get_athlete_repo` FastAPI dependency — add one only if a route needs to construct the repository itself, not as a documented affordance nothing yet uses.
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
The status of one daily-wellness signal (HRV, sleep, RHR, soreness, mood, **stress**): **untracked** (not expected — hidden, no penalty; *implicit* until first provided, or an *explicit* opt-out on `AthleteProfile.untracked_wellness_signals`), **unknown today** (tracked but absent — a visible gap that lowers confidence), or **provided** (measured). A signal becomes *expected* once provided ≥1 time or explicitly opted in. Only *provided* values enter readiness; missing is never silently imputed, and a stale sample is never reused as if fresh. The 28-day personal baseline is display-only interpretation, not an input.
_Avoid_: default value, filled, imputed reading.

## Logical signal vs metric
A **logical signal** is what an athlete recognizes (e.g. `sleep`); a **metric** is a stored column (`sleep_hours`, `sleep_quality`). The canonical `WELLNESS_SIGNAL_REGISTRY` maps one logical signal to ≥1 metric. Coverage, the `signal_summary`, and UI copy count *logical* signals (so sleep counts once); the readiness modifier's `components` stay *metric*-grained. Signals carry a category — `wellness_readiness` / `biometric_recovery` / `safety_symptom` — and only `coverage=true` ones enter the coverage denominator (pain is `safety_symptom`, excluded).
_Avoid_: field, column (when you mean the user-facing signal).

## Readiness score vs confidence
`readiness.score` = how ready the athlete appears today; `readiness.confidence` = how well-supported that estimate is (an evidence-coverage reliability object, not a second score). The **score** may transparently nudge the plan (bounded ±0.15); **confidence** is **report-only** in P8 — its `recommendation_gate` carries `enforced=false` and never gates the prescriber (that is the P13 [confidence gate](#confidence-gate)). Shorthand: *score can nudge, confidence cannot gate*.
_Avoid_: treating confidence as low readiness, or as an enforced cap (pre-P13).

## Training emphasis
The `modality_mix` (a `domain → weight` blend) the prescriber actually pursues, **computed from the athlete's active objectives** (weighted by priority × proximity × gap × status, smoothed), then floored and override-adjusted. Replaces the single-goal bottleneck. `primary_goal`/`block_goal` survive only as a fallback when no objectives exist.
_Avoid_: primary goal, the goal.

## Planning override
User-declared structural intent the prescriber honors — pin/exclude a modality, phase, block goal, or frequency. Carries an **authority**: a **hard override** is honored unless a safety gate forbids it; a **soft preference** the optimizer may trade off. A hard override that deviates from the efficient path is honored *and* its objective cost is surfaced — never silently corrected.
_Avoid_: setting, tweak.

## Authority stack
The fixed precedence the planner resolves intent through: **safety** (absolute) → **user hard override** → **objectives / floors** → **optimizer** → **tradeoff explanation**. Shorthand: the user owns intent and structure; the engine owns safety, feasibility, and execution quality.

## External intensity (dose `I`)
The dose law's external-load term — load relative to capacity (`load / e1RM_pre` for lifts), **independent** of internal effort `F` (RPE/RIR). Per [ADR-0039](docs/adr/0039-dose-law-external-load-vs-effort.md) it was accepted but **never delivered**: the engine hardcoded `I = 1.0`, so equal-volume sessions at different relative loads produced the same dose. Closes on **Model A** (session-scalar `I` enters the session base) with per-exercise routing deferred to [ADR-0054](docs/adr/0054-per-exercise-dose-routing.md).
_Avoid_: conflating with effort `F` or with volume.

## Strength evidence ledger
The single canonical store of strength observations (today `benchmark_observations`), where **validity ≠ authority** ([ADR-0055](docs/adr/0055-strength-evidence-ledger.md)). A row's `evidence_type` / `observation_model` sets what it may do: a **benchmark test** (`direct_capacity_measurement`) may move capacity bidirectionally; a **training-derived e1RM** (`training_estimate` / `censored_lower_bound`) feeds prescription, tracking, PR detection, and the dose intensity denominator but **never regresses capacity**. Coverage is **wide** (any e1RM-eligible movement, keyed by `exercise_id`); capacity **authority is narrow** (benchmarked/protocol-grade only).
_Avoid_: "e1RM table", treating a working set as a measurement.

## Lower-bound evidence
A logged hard set proves the athlete is **at least** this strong (`X ≥ L`), not that their max *equals* L. So training-derived e1RM ratchets the estimate **upward only** (past a small deadband) and never pulls it down. The durable rule: *training can demonstrate you are stronger; it cannot prove you are weaker.*
_Avoid_: modeling a working set as a noisy direct observation `y = X + η` (that regresses capacity on easy days).

## Effort fidelity
The provenance of a set's effort value ([ADR-0045](docs/adr/0045-per-set-catalog-bound-workout-logging.md) amendment): `set_level` (logged per set) > `group_level` (cloned from a `sets=N` quick-entry) > `session_level` > `missing`. It is an **evidence-authority multiplier** — `group_level` RPE may inform dose and conservative lower-bound evidence but never carries `set_level` authority. *The set is atomic; the group is UX; effort fidelity is evidence authority.*
_Avoid_: treating cloned quick-entry RPE as true per-set effort.

## %1RM calibration
The one versioned service ([ADR-0056](docs/adr/0056-canonical-percent-1rm-calibration.md)) that maps between load, reps, effort, and %1RM for **every** consumer (prescription, e1RM extraction, dose intensity fallback), always emitting `source` + `confidence` + `model_version`. Ladder: actual `load/e1RM_pre` → RPE/RIR chart → reps-beyond-first Epley (to-failure only) → default → neutral. Replaces the three divergent Epley forms P9 left behind. *The same set must resolve to the same base `I_set` everywhere; downstream may transform it, never recompute it.*
_Avoid_: inline Epley formulas, an unversioned chart.

## Dose control-space compatibility scale
The versioned scalar that maps a raw per-exercise-routed dose contribution (`raw_fatigue_dose = Σ_i φ_i·D_i`, model-native, unbounded) into the existing **0–100 fatigue/tissue control space** the engine's deload/interference/safety thresholds already speak ([ADR-0054](docs/adr/0054-per-exercise-dose-routing.md)): `fatigue_delta_compat_0_100 = k_fatigue_v1 · raw_fatigue_dose`. `k` is chosen by **distribution-matching** old-vs-new dose over a representative session corpus (median of `old_delta / raw_dose` on eligible sessions; P50/P75/P90/P95 checked), **not** one toy session. Two spaces are kept distinct: **raw model space** (observability, the future tuning harness) and **control space** (what safety rules consume); raw φ·D is **never** fed directly to a threshold. Every value carries `raw_*`, the unclipped compat value, `*_scale_k`, and `fatigue_scale_model_version` (`fatigue_compat_v1`). *It is a compatibility bridge, not a validated physiology unit — its job is to stop a model-scale migration from silently invalidating live safety thresholds.* Threshold re-tuning is a separate later project gated on a real tuning/evaluation harness.
_Avoid_: calling the scaled value a physiology unit; letting raw φ·D touch deload/safety rules; re-tuning thresholds in the same PR.

## Dose routing basis
The provenance of *where* a session's dose stress was routed, as a coverage/missingness ladder — never a tunable blend ([ADR-0054](docs/adr/0054-per-exercise-dose-routing.md)). Per dose-bearing exercise: **`exercise_phi`** (resolved φ — catalog-seeded or computed from the exercise's own modality/movement/skill/impact — routes through its own φ vectors, the authoritative case) → **`unresolved_exercise_fallback`** (no φ resolved for this movement; routed through a conservative substitute at low confidence with `fallback_reason="missing_exercise_phi"`, so its dose is **never erased**) . Per session: if ≥1 dose-bearing exercise resolves φ the session `routing_basis = exercise_phi`; if none do, the whole session uses **`session_modality_fallback`** (conservative session-modality φ). *Exercise-level φ is authoritative when available; session modality is a fallback only, never blended with resolved φ through a λ parameter — session labels are lossy summaries, φ is the true router.* Total routed dose ≈ session dose regardless of φ coverage (so the compat-scale calibration stays valid).
_Avoid_: a continuous λ blend of φ and modality; dropping the dose of unresolved exercises; treating "partly resolved" as a calibrated continuum instead of a coverage gap.

## DomainCode
The canonical domain vocabulary (`domain_vocab.DOMAINS`) filling **three non-interchangeable roles** ([ADR-0057](docs/adr/0057-domaincode-three-roles-one-vocabulary.md), extending [ADR-0038](docs/adr/0038-canonical-domain-taxonomy.md)): **home domain** (a benchmark's/template's/objective's specialist domain), **surfacing lens** (`domain_lenses` — discoverability metadata only), and **prescription capability** (`PRESCRIPTION_SUPPORTED_DOMAINS`, an explicit reviewed set). One vocabulary, three fields; a benchmark surfacing under `strength` does not make its home domain `strength`, and a domain being canonical does not make it prescriber-supported. Canonical values are the only values persisted/serialized; `DOMAIN_ALIASES` normalize inbound external/legacy input at a boundary and are never persisted (owned seed rows fail CI if they carry an alias). `weightlifting` = Olympic weightlifting + derivatives; `running` = the broad running domain including sprinting (sprint/endurance lives in `category`/tags).
_Avoid_: treating `domain`, `domain_lenses`, and prescription-support as one field; persisting an alias (`mixed_modal`/`olympic_lifting`/`sprinting`); confusing the movement/exercise/tag vocabulary (a separate axis) with `DomainCode`.

## Benchmark surfacing lens
`benchmark_definition.domain_lenses` — the canonical athlete-domain lenses under which a benchmark is *eligible to surface* in the assessment onramp ([ADR-0057](docs/adr/0057-domaincode-three-roles-one-vocabulary.md)). **Discoverability metadata only** — never physiological routing, prescription capability, or benchmark authority. A benchmark's home domain is always included in its resolved lens set; the set is curated wider so a coarse athlete lens finds specialist benchmarks (e.g. `squat_1rm`: home `powerlifting`, lenses `[powerlifting, strength]`). Null resolves to `[domain]` as a **compatibility fallback**, distinguished from deliberate narrowness by `domain_lenses_source ∈ {explicit_curated, home_domain_default}`.
_Avoid_: reading `domain_lenses` as routing/capability; letting a null default masquerade as completed curation; a supported athlete lens with zero eligible onramp benchmarks.

## Seed evidence tier
The quality class of the evidence that seeded a capacity axis at onboarding ([ADR-0059](docs/adr/0059-seed-uncertainty-and-provisionality-views.md), extending [ADR-0036](docs/adr/0036-per-axis-confidence-scalar.md)): `direct_measured_onramp` › `direct_estimated_onramp` › `cross_axis_inference` › `experience_prior` › `unseeded`, strictly ordered so seed variance `= axis_base_variance × tier_multiplier` is always **higher** (less confident) than any validated benchmark for that axis. `source_type`, `evidence_tier`, and `variance` are three separate fields — never collapsed 1:1. Cross-axis inference (power←squat) is not an experience prior: it keeps `source_observation_id` + `seed_group_id` + `inference_model_version`, and no service may count same-`seed_group_id` axes as independent evidence. The seed snapshot is immutable per-axis provenance; live `CapacityConfidence` is the only runtime authority.
_Avoid_: uniform seed variance; calling a cross-axis inference an experience prior; treating unseeded `50` as a known value; counting one squat as two independent measurements.

## Evidence status vs confidence status
Two orthogonal facts about a capacity axis ([ADR-0059](docs/adr/0059-seed-uncertainty-and-provisionality-views.md)) that the honesty ladder keeps separate. **Evidence status** = provenance (`measured | estimated | inferred | experience_prior | unobserved`). **Confidence status** = current uncertainty, derived from **live `CapacityConfidence` only** (`established | provisional | insufficient`). A value can be "measured but provisional" (noisy/old/weak); a historic `measured` stamp never overrides high live variance. Likewise **measurement debt** (evidence insufficient for a decision) is separate from **actionable debt** (a safe, feasible assessment can reduce it) — uncertainty is not hidden by benchmark unavailability, and only actionable debt becomes a ranked, cooldown-gated prompt. There is no global `twin_is_provisional`.
_Avoid_: one enum mixing provenance and uncertainty; a measured stamp masking high live variance; hiding an axis's uncertainty because no benchmark is available; a global provisional flag.
