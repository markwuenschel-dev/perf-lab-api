# Goal-Anchored Program & Prescription — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan phase-by-phase. Steps use checkbox (`- [ ]`) syntax for tracking. This is a **multi-subsystem program**: each Phase is independently shippable and testable. Phases 0–2 are execution-ready; Phases 3–7 each begin with a short design task because their detail depends on earlier phases landing.

**Goal:** Make the athlete's *goal* a real, persisted, first-class thing so every surface (prescription, planning, identity, program timeline) reflects it instead of falling back to hard-coded running mock-ups.

**Architecture:** The backend is already a general multi-domain engine (`api → service → logic → models`, Alembic Postgres, append-only `AthleteState`). The failures the user sees are (a) one real prescriber bug where the structured exercise list is generated goal-blind from an equipment map, and (b) a missing "spine": the goal/objective/program is not persisted, so the web UI renders literals ("A. Rivera", "Valencia Marathon", "Tempo intervals"). This plan fixes the bug first, then builds the spine bottom-up: identity → persisted goal → per-block session prefs → Objectives → Program container → rewire the mock UI → goal-aware Simulator.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic 2.x, Postgres; web is React + TypeScript (Vite), types generated from OpenAPI (`web/src/types.gen.ts`), client in `web/src/api/perfLabClient.ts`, local store in `web/src/perflab/store.tsx`.

## Global Constraints

- **Migrations:** current head is `a005_telemetry`/`a006_experiment` — run `alembic heads` and chain new migrations off the true head; do not renumber existing ones. Startup does a head-check (fails fast in production on mismatch).
- **Type contract:** any backend schema change requires regenerating web types — `python -m app.scripts.export_openapi` (or `--check`), then in `web/`: `npm run gen:types` → `tsc -b`. The `openapi.json` is LF-pinned via `.gitattributes`; do not let CRLF drift in.
- **CI gate is blocking:** `ruff check .` + `mypy app` must be green **repo-wide** (not just changed files) and the OpenAPI contract job must pass before merge. Watch `gh pr checks --watch`. `ruff`↔`mypy` can conflict (C416 vs Row-not-tuple) — prefer `dict(res.tuples().all())`.
- **Append-only state:** never mutate an `AthleteState` row; new state = new row.
- **Vector-model duplication landmine:** do not reintroduce `app/schemas/engine_vectors.py` redefinitions of vectors that already live in `app/domain/vectors.py` (pydantic 2.x rejects cross-class instances).
- **Local DB caveat:** async_db integration tests SKIP on this box (conftest event-loop bug). "Local pytest green" ≠ DB code verified — verify DB-touching work with a standalone asyncio harness or in CI.
- **Decision records:** the product thesis is already recorded (PDR-0001…0006). Objectives = PDR-0004 (accepted direction). A new **Program/macrocycle container** is a new architectural decision → write an ADR/PDR for it in Phase 5 before building.

---

## Phase map & dependencies

| Phase | Deliverable | Depends on | Ships alone? |
|---|---|---|---|
| **0** | Prescriber returns goal-correct exercises (the "SBD → tempo squat" bug) + `/planning/today` periodization fix | — | ✅ |
| **1** | Editable athlete name (kills "A. Rivera / Athlete · 28") | — | ✅ |
| **2** | Persisted training goal (drives `/next-session` without a query param) | — | ✅ |
| **3** | Per-block session prefs: duration + accessory emphasis; block-creation UI | 0, 2 | ✅ |
| **4** | Objectives model (generalizes running-only Goal Race) | 2 | ✅ |
| **5** | Program / macrocycle container above blocks ("week X of Y", built back from an Objective) | 4 | ✅ |
| **6** | Rewire mock UI to real data: Overview "recommended today" + daily check-in prompt; Planning detail | 0, 2, 5 | ✅ |
| **7** | Goal-aware Simulator (not running-only) | 2, 4 | ✅ |

Recommended order: 0 and 1 in parallel (independent quick wins), then 2 → 3, 4 → 5 → 6, with 7 last.

---

## Phase 0 — Prescriber returns goal-correct exercises

**Why:** The user's headline complaint. `recommend_next_session` picks a goal-correct template whose `focus` is literally `"Squat / Bench / Deadlift — top sets + 3–4 back-off sets"` (`candidate_library.py:308`), but finalization then **overwrites the structured exercise list** from an equipment map, and with no equipment configured it emits the bodyweight default `Tempo Squat / Push-Up / Split Squat` (`prescriber.py:305-309, :478`, `_exercise_list_for_equipment` `:313-327`). `CandidateTemplate` (`candidate_library.py:65-99`) carries only a `focus` string — **no structured movements** — so there is nothing goal-specific for finalization to use. Fix: give templates real exercise slots and finalize from them, keeping the equipment map only as a fallback.

**Files:**
- Modify: `app/logic/candidate_library.py` — add `exercise_slots` to `CandidateTemplate` (`:65-99`); populate SBD + accessory + the other goal templates.
- Modify: `app/logic/prescriber.py` — finalization (`~:270-287`, `:427`) and the overwrite line (`:478`) to prefer template slots over the equipment map.
- Modify: `app/api/v1/planning.py:168-174` — add `duration_weeks` + `deload_every_n_weeks` to the inline `block_context` so the periodization envelope stops silently no-op-ing on the `/today` path (envelope guard is `prescriber.py:443-444`, `weeks_total = int(block.get("duration_weeks") or 0)`).
- Test: `tests/logic/test_prescriber_exercise_selection.py` (new), `tests/api/test_planning_today_periodization.py` (new).

**Interfaces:**
- Produces: `CandidateTemplate.exercise_slots: list[ExerciseSlot]` where `ExerciseSlot = tuple[str, str, str]` (name, sets, reps) — same shape the equipment map already uses, so `ExercisePrescription` construction (`prescriber.py:324-326`) is reused unchanged. Empty `exercise_slots` → fall back to `_exercise_list_for_equipment` (preserves current behavior for templates not yet populated).

**Tasks:**

- [ ] **0.1** Write a failing test: a powerlifting prescription returns `exercises` whose names include Squat/Bench/Deadlift (not "Push-Up"). Given a `UnifiedStateVector` (neutral readiness) + `goal="Powerlifting"` + no equipment, assert `{"Squat","Bench","Deadlift"} ⊆ {e.name-stem for e in rx.exercises}` and `"Push-Up" not in names`. Run it; watch it fail on the bodyweight fallback.
- [ ] **0.2** Add `exercise_slots: list[tuple[str,str,str]] = field(default_factory=list)` to `CandidateTemplate`. Populate the two `POWERLIFTING_TEMPLATES` SBD entries with real slots (Back Squat / Bench Press / Deadlift + back-off), the `pl_accessory` template with its accessories, and each other domain's primary template (strength, running, weightlifting, metcon, calisthenics) with representative slots. Keep slots equipment-tolerant (barbell primary, note bodyweight scaling in `load_note`).
- [ ] **0.3** In finalization, thread the winning `CandidateTemplate` (or its `exercise_slots`) through `finalize_prescription`/`prescriber.py:478` so `rx.exercises` come from `exercise_slots` when present, else `_exercise_list_for_equipment(available_equipment)`. Run 0.1 → PASS. Confirm the equipment-map path still works via an existing/added test where slots are empty.
- [ ] **0.4** Write a failing test for the `/planning/today` periodization gap: build a block with `duration_weeks=8`, a planned session at `week_number=7` (near taper), call the today path, assert the prescription's `why`/annotations include a periodization phase/RPE (envelope fired). It currently fails because `duration_weeks` is omitted from `block_context`.
- [ ] **0.5** Add `"duration_weeks"` and `"deload_every_n_weeks"` to the `block_context` dict in `planning.py:168-174` (fetch from the parent `MesocycleBlock`, alongside the existing `deload_volume_factor` query). Run 0.4 → PASS.
- [ ] **0.6** Repo-wide `ruff check .` + `mypy app`; run the two new test files. Commit: `fix(prescriber): structured exercises follow the goal template, not the equipment fallback`.

**Acceptance:** A Powerlifting athlete with no equipment configured gets Squat/Bench/Deadlift work, not "Tempo Squat / Push-Up / Split Squat". The `/next-session` and `/planning/today` paths agree on periodization.

**Later target (not this phase):** DB-driven selection from the `Exercise` table (`app/models/exercise.py`) filtered by domain/tags/equipment/weak-points (ROADMAP §5.4). `exercise_slots` is the pragmatic bridge; note it in the docstring.

---

## Phase 1 — Editable athlete name

**Why:** "A. Rivera / Athlete · 28" is hard-coded JSX (`web/src/perflab/Sidebar.tsx:152-157`). The backend has **no name field** (`User` = email only, `user.py:27-61`; `AthleteProfile` has none, `:64-93`). Onboarding's "First name / Last name / DOB" inputs are wired to nothing (`OnboardingScreen.tsx:212-214`) and dropped by `finish()` (`:135-166`).

**Files:**
- Migration: `alembic/versions/a00N_athlete_name.py` (chain off current head) — add `display_name TEXT NULL` to `athlete_profiles` (and optional `date_of_birth DATE NULL` if you want the age to be real rather than "28").
- Modify: `app/models/user.py` — add `display_name` (+ optional `date_of_birth`) to `AthleteProfile`.
- Modify: `app/schemas/profile.py` — add to `ProfileRead` and `ProfileUpdate` (`:29-49`).
- Modify: `app/schemas/onboarding.py` + `app/api/v1/onboard.py:32-42` — accept and persist `display_name`.
- Modify web: `web/src/perflab/screens/OnboardingScreen.tsx:212-214` (wire the name inputs into `finish()`), `web/src/perflab/screens/SettingsScreen.tsx` (add a name field to the profile card, `:133-267`), `web/src/perflab/Sidebar.tsx:152-157` (read the name + initials from profile instead of the literal), `web/src/api/perfLabClient.ts` (name already flows through `getProfile`/`updateProfile`/`onboard` once the schema carries it).
- Test: `tests/api/test_profile_name.py` (new).

**Tasks:**

- [ ] **1.1** Write failing test: `PATCH /v1/profile {"display_name":"Mark"}` then `GET /v1/profile` returns `display_name == "Mark"`.
- [ ] **1.2** Alembic migration adding the column; `alembic upgrade head` locally / in CI.
- [ ] **1.3** Add the field to the ORM + `ProfileRead`/`ProfileUpdate` + onboarding schema; persist it in `onboard.py`. Run 1.1 → PASS.
- [ ] **1.4** `export_openapi` → regen web types → wire the three web surfaces (onboarding, settings, sidebar). Sidebar initials derive from `display_name` (fallback to email local-part; never the literal "A. Rivera").
- [ ] **1.5** `ruff`/`mypy`/`tsc -b`; commit `feat(profile): editable athlete display name end-to-end`.

**Acceptance:** Set your name in onboarding or Settings; the sidebar shows it. No hard-coded "Rivera" anywhere.

---

## Phase 2 — Persisted training goal

**Why:** The goal is not a stored athlete attribute. `OnboardRequest.goal` is accepted but **silently discarded** (`onboard.py` never reads `request.goal`; `onboarding.py:12`). `/next-session` takes `goal` as a query param defaulting to `"Strength"` (`prescribe.py:16-22`, `training_goals.py:22`); the web only keeps it in localStorage (`store.tsx`, `SettingsScreen.tsx:302-315`). So "your goal" can't drive anything durably.

**Files:**
- Migration: `alembic/versions/a00N_profile_goal.py` — add `primary_goal TEXT NULL` to `athlete_profiles` (values from the `TrainingGoal` literal, `training_goals.py:5-20`).
- Modify: `app/models/user.py` (`AthleteProfile.primary_goal`), `app/schemas/profile.py`, `app/schemas/onboarding.py` (already has `goal`), `app/api/v1/onboard.py:32-42` (persist `request.goal`).
- Modify: `app/api/v1/prescribe.py:14-24` + `app/services/prescription_service.py:103` — when no explicit query goal and no active block, fall back to `profile.primary_goal` (order: active block goal > explicit query > stored `primary_goal` > `"Strength"`).
- Modify web: `SettingsScreen.tsx` goal dropdown → `updateProfile({primary_goal})` (currently local-only); `OnboardingScreen.tsx finish()` already sends `goal`.
- Test: `tests/api/test_persisted_goal.py` (new).

**Tasks:**

- [ ] **2.1** Failing test: onboard with `goal="Powerlifting"`, then `GET /v1/profile` shows `primary_goal=="Powerlifting"`, and `GET /next-session` (no `goal` query, no active block) prescribes powerlifting content.
- [ ] **2.2** Migration + ORM + schemas; persist in `onboard.py`. 
- [ ] **2.3** Prescriber fallback chain in `prescription_service.py:103` (`effective_goal = block goal or query goal or profile.primary_goal or default`). Run 2.1 → PASS.
- [ ] **2.4** Wire the Settings goal dropdown to PATCH `primary_goal`; regen types; `tsc -b`.
- [ ] **2.5** `ruff`/`mypy`; commit `feat(goal): persist primary training goal and use it in prescription`.

**Acceptance:** Pick "Powerlifting" once; it survives reload and drives `/next-session` with no query param.

---

## Phase 3 — Per-block session preferences (duration + accessory emphasis)

**Design decision (recorded via your answer):** duration + accessory emphasis are **per-block settings** set at block creation, not standing profile prefs. Note: `AthleteProfile.session_duration_minutes` exists (`user.py:80`) and is currently ignored by the prescriber — we honor the **block** value first, then fall back to the profile value.

**Design task first:**
- [ ] **3.0** Decide the accessory-emphasis representation on the block. Recommended: `accessory_emphasis: str` (`"minimal" | "balanced" | "high"`) + optional `accessory_focus: list[str]` (tag names like `"posterior_chain"`, `"push"`, `"core"`). Decide how emphasis maps to prescription (e.g. number/volume of accessory slots appended after the primary lifts) and how the block's `target_session_minutes` trims/extends slot count. Write this as a 10-line note at the top of the phase's first test file.

**Files:**
- Migration: `alembic/versions/a00N_block_session_prefs.py` — add `target_session_minutes INT NULL`, `accessory_emphasis TEXT NULL`, `accessory_focus JSONB NULL` to `mesocycle_blocks`.
- Modify: `app/models/mesocycle.py:50-123`, `app/schemas/planning.py:17-27` (`BlockCreateRequest`), `app/services/planning_service.py:142-195` (persist), `app/services/prescription_service.py:82-93` (add to `block_context`), `app/logic/prescriber.py` (consume: trim/extend `exercise_slots` toward `target_session_minutes`; append accessory slots per `accessory_emphasis`/`accessory_focus`, reusing the `pl_accessory`-style content and weak-point tags).
- New web: a **block-creation screen/overlay** — the client fns already exist and are unwired: `createPlanningBlock`/`listPlanningBlocks`/`updatePlanningBlock` (`perfLabClient.ts:295-306`). Add duration + accessory controls here.
- Test: `tests/logic/test_prescriber_session_prefs.py`, `tests/api/test_block_prefs.py`.

**Tasks:**

- [ ] **3.1** Failing test: a block with `target_session_minutes=45` yields a prescription with fewer/shorter slots than the same block at `90`; `accessory_emphasis="high"` appends accessory slots, `"minimal"` appends none.
- [ ] **3.2** Migration + ORM + `BlockCreateRequest` + persist in `planning_service`.
- [ ] **3.3** Thread the three fields into `block_context` and consume them in `prescriber.py` (duration trims/pads slots around the template's `duration_min`; accessory emphasis appends tagged accessory slots, biased by active weak-points). Run 3.1 → PASS.
- [ ] **3.4** Build the block-creation UI (wire `createPlanningBlock`); regen types; `tsc -b`. This also fixes "a fresh user always hits the Planning empty state" (no create UI existed).
- [ ] **3.5** `ruff`/`mypy`; commit `feat(planning): per-block session length + accessory emphasis drive the prescription`.

**Acceptance:** Create a block choosing "~45 min, high accessory, posterior-chain focus"; prescriptions shorten and gain posterior-chain accessories. Session length is no longer a stored-but-ignored field.

---

## Phase 4 — Objectives (generalize the running-only Goal Race)

**Why:** No Objective model exists (`models/__init__.py` has none; grep for `Objective`/`target_date`/`goal_race` = nothing). "Goal Race" is a frontend-only, running-only hard-coded object ("Valencia Marathon", `store.tsx:180-188`) with its own sidebar entry and its own `GoalRaceScreen.tsx` (VO₂/pace/splits). PDR-0004 already decided Objectives are first-class and should replace it. Objectives also become the **anchor** the Program (Phase 5) is built backward from.

**Design task first:**
- [ ] **4.0** Confirm Objective shape against `BenchmarkDefinition`/`BenchmarkObservation` (they carry `better_direction` for progress math). Recommended fields (matches ROADMAP §P4): `user_id`, `benchmark_code` (FK → `benchmark_definitions.code`, nullable for free-text targets), `label`, `domain`, `target_value FLOAT`, `target_unit`, `target_date DATE`, `priority INT`, `status` (`active|achieved|abandoned`), `created_at`. Decide how priority feeds the prescriber (recommended for now: taper window near `target_date` + a small stress-allocation nudge toward the objective's domain — keep it modest, ADR-worthy later).

**Files:**
- Create: `app/models/objective.py`, `app/schemas/objective.py`, `app/services/objective_service.py` (CRUD + `progress = f(latest observation vs target, direction-aware)`), `app/api/v1/objectives.py` (`GET/POST/PATCH/DELETE /v1/objectives`), migration `a00N_objectives.py`.
- Modify: `app/models/__init__.py` (register), `app/services/prescription_service.py` (surface active objectives into `block_context`; apply taper near `target_date`), `main.py`/router registration.
- New web: replace `GoalRaceScreen.tsx` with an **Objectives** surface (multi-objective list, per-objective countdown + progress %, domain-aware — a strength meet, a Hyrox, a race, a benchmark PR — not just marathons). Add `objectives` client fns to `perfLabClient.ts`. Retire the `state.race` mock (`store.tsx:180-188`) and the Valencia references on Overview (`OverviewScreen.tsx:85-92`).
- Test: `tests/api/test_objectives.py`, `tests/services/test_objective_progress.py`.

**Tasks:**

- [ ] **4.1** Failing test: create an objective (deadlift 220kg by a date), post an observation, `GET /v1/objectives` returns direction-aware `progress` and a `days_to_go`.
- [ ] **4.2** Model + migration + schema + service (progress vs latest `BenchmarkObservation`, using `better_direction`).
- [ ] **4.3** Router `app/api/v1/objectives.py`; register; `export_openapi`.
- [ ] **4.4** Prescriber: taper window near the highest-priority objective's `target_date`; annotate in `why`. Test that a session inside the taper window reduces volume.
- [ ] **4.5** Web: Objectives screen replaces Goal Race (keep a running-race objective as one *kind*, not the only kind); regen types; `tsc -b`; delete the `state.race` mock and Valencia literals.
- [ ] **4.6** `ruff`/`mypy`; commit `feat(objectives): first-class multi-domain objectives replace the running-only goal race`.

**Acceptance:** Add "Deadlift 220 kg by <date>" and "Half-marathon sub-1:45 by <date>" as concurrent objectives; each shows progress + countdown; the prescriber tapers near the nearest one.

---

## Phase 5 — Program / macrocycle container

**Why (recorded via your answer, "1+2"):** you want **both** an Objective anchor *and* a Program container above blocks. Today `MesocycleBlock` is the top of the hierarchy, capped at 24 weeks (`schemas/planning.py:20`, `le=24`), single-block; "week 3/7" on the Planning screen is a hard-coded literal (`PlanningScreen.tsx:110`). There is a **ready blueprint**: the inert `PlanTemplate`/`TrainingBlock` library with `total_weeks()`/`block_at_week()` (`app/logic/planning.py:85-382`) — never persisted or invoked. A `Program` makes "how long — a week / 3–6 months / a year / until my meet" real and gives a true "week X of Y" across the whole thing.

**Design task first:**
- [ ] **5.0** Write **ADR/PDR: Program (macrocycle) as the container above blocks.** Decide: `Program` fields (`user_id`, `objective_id` FK nullable, `horizon_kind` = `weeks|until_objective`, `total_weeks` nullable, `start_date`, `status`); relationship `Program 1—* MesocycleBlock` (add `program_id` FK + `sequence_index` to `MesocycleBlock`); how "week X of Y" is computed across sequential blocks; and the generator that lays down blocks backward from the objective date (reuse the inert `PlanTemplate.block_at_week` logic). Lift the 24-week block cap where a program owns the block.

**Files:**
- Create: `app/models/program.py`, `app/schemas/program.py`, `app/services/program_service.py` (create program → generate sequential blocks toward objective/horizon; compute program-position), `app/api/v1/programs.py` (`GET/POST /v1/programs`, `GET /v1/programs/current`), migration `a00N_program.py` (+ `program_id`/`sequence_index` on `mesocycle_blocks`).
- Modify: `app/models/mesocycle.py` (FK + sequence), `app/logic/planning.py` (promote `PlanTemplate`/`block_at_week` from inert reference into the generator), `app/services/prescription_service.py` (program-position into `block_context`).
- Modify web: `PlanningScreen.tsx:110` header → real "phase · wk X/Y" from `GET /v1/programs/current`; add a program overview (blocks timeline, "you are here"); wire the previously-dead `listPlanningBlocks`/program client fns.
- Test: `tests/api/test_programs.py`, `tests/services/test_program_position.py`.

**Tasks:**

- [ ] **5.1** Failing test: create a program `horizon_kind="until_objective"` toward an objective 16 weeks out → generates N sequential blocks summing to ~16 weeks; `GET /v1/programs/current` returns `week_in_program`, `total_weeks`, `current_block`, `phase`.
- [ ] **5.2** Models + migration (+ block FK/sequence) + schema.
- [ ] **5.3** `program_service` generator (backward from objective date, reusing `block_at_week`) + position computation. Run 5.1 → PASS.
- [ ] **5.4** Router + register + `export_openapi`.
- [ ] **5.5** Web: real program header + timeline; `tsc -b`.
- [ ] **5.6** `ruff`/`mypy`; commit `feat(program): macrocycle container with real program position and horizon`.

**Acceptance:** Choose "design my program until my meet on <date>"; the app lays out sequential blocks to that date and shows a truthful "Week 5 of 16 · Intensification".

---

## Phase 6 — Rewire the mock UI surfaces to real data

**Why:** Overview's "Recommended today — Tempo intervals" is literal JSX (`OverviewScreen.tsx:149-163`) even though `getTodayPlannedSession(goal)` already exists (`perfLabClient.ts:362-370`) and is never called. The daily check-in is wired (`CheckinModal.tsx` → `ingestWellness`/`getReadiness`) but nothing prompts it. Planning's detail card, load/readiness chart, and impact rows are literals (`PlanningScreen.tsx:58-65,149-192`).

**Files (web-only unless a gap surfaces):**
- Modify: `OverviewScreen.tsx` — "Recommended today" → `getTodayPlannedSession`; greeting uses the real name (Phase 1); add a **daily check-in prompt** (nudge card when today has no `WellnessSample`, opening `CheckinModal`); remove the Valencia goal-race card (replaced by an Objectives summary from Phase 4).
- Modify: `PlanningScreen.tsx` — session-detail card from the real planned session's `prescribed_content`; keep the load/readiness chart tagged "simulated" only if no backend series exists yet (Twin/Overview readiness already come from `getReadiness`).
- Modify: `SessionPlayer.tsx`/`FeedbackModal.tsx` — drop the hard-coded "Tempo intervals" copy; render the passed session.
- Test: web `tsc -b` + a light render test if the harness supports it; otherwise manual `/run`.

**Tasks:**

- [ ] **6.1** Wire Overview "Recommended today" to `getTodayPlannedSession`; empty/loading/error states like `PlanningScreen`'s week strip.
- [ ] **6.2** Add the daily check-in prompt (show when no wellness sample for today; CTA opens `CheckinModal`). This is the "prompt you to check in every day" the user asked for.
- [ ] **6.3** Replace Planning's hard-coded detail with `prescribed_content`; remove the `state.race`/Valencia literals; Session Player/Feedback render real content.
- [ ] **6.4** `tsc -b`; commit `feat(web): overview + planning read real prescriptions and prompt the daily check-in`.

**Acceptance:** Overview shows today's *actual* prescribed session and nudges the check-in; no "Valencia"/"Tempo intervals" literals remain outside `sim.ts`.

---

## Phase 7 — Goal-aware Simulator

**Why:** `SimulatorScreen.tsx` is 100% `buildProjection()` from `sim.ts:104-129` — running-only (VO₂max + 10K), no goal-awareness, no backend. For a strength/hybrid athlete it's irrelevant.

**Design task first:**
- [ ] **7.0** Decide backend vs client projection. Recommended: a small backend `GET /v1/projection?horizon_weeks=N` that projects the athlete's **capacity axes** (all 8, per ADR-0023) forward under the current program/objective, returning per-axis trajectories; the Simulator renders whichever axes matter for the athlete's domain(s) (strength → force/max-strength trajectory + projected e1RM; running → aerobic/VO₂; hybrid → both). This reuses the engine's build/detrain dynamics rather than the client VO₂ formula.

**Files:**
- Create: `app/services/projection_service.py`, `app/api/v1/projection.py`, schema `app/schemas/projection.py`.
- Modify web: `SimulatorScreen.tsx` — call the projection endpoint; render domain-appropriate axes; retire the running-only `buildProjection`.
- Test: `tests/services/test_projection.py`.

**Tasks:**

- [ ] **7.1** Failing test: projection for a powerlifting athlete returns a max-strength/force trajectory and a projected e1RM, not just VO₂.
- [ ] **7.2** `projection_service` reusing engine build/detrain; router; `export_openapi`.
- [ ] **7.3** Web: goal-aware Simulator; regen types; `tsc -b`.
- [ ] **7.4** `ruff`/`mypy`; commit `feat(simulator): multi-domain forward projection driven by goal and program`.

**Acceptance:** The Simulator shows strength trajectories for a lifter, aerobic for a runner, both for a hybrid athlete.

---

## Self-review notes

- **Coverage vs the user's critique:** name (P1), Valencia staleness + daily check-in prompt (P4 removes mock, P6 adds prompt), stale "tempo intervals recommended today" (P0 fixes the content, P6 wires it live), Simulator running-only (P7), prescriber SBD→wrong-exercises mismatch + session length + accessory prefs (P0 + P3), Goal Race running-only / should depend on goal (P4), Planning tab hard-coded (P5 header + P6 detail), program horizon + "where am I" (P5). All eight complaint clusters map to a phase.
- **Type consistency:** `exercise_slots: list[tuple[str,str,str]]` reuses the exact shape `_exercise_list_for_equipment` already consumes; `effective_goal` fallback chain is defined once in `prescription_service.py:103`; `block_context` keys (`duration_weeks`, `deload_every_n_weeks`, `target_session_minutes`, `accessory_emphasis`, `accessory_focus`, program-position) are added additively.
- **Open design tasks flagged inline:** 3.0 (accessory representation), 4.0 (objective↔benchmark shape + priority→prescriber), 5.0 (Program ADR), 7.0 (projection backend-vs-client). These are the genuine forks left; each is a one-note decision at the top of its phase.
- **Migration chain:** every phase that adds columns/tables needs a fresh Alembic revision off the true head — verify with `alembic heads` at execution, do not assume `a007`.
