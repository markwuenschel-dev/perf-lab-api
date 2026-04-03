# Performance Lab API — Full-Spectrum Fitness Engine Upgrade

**Document type:** project / agent brief — target architecture and delivery checklist for evolving this codebase.

**Phase 1 status (implemented in repo):** Pydantic vectors `CapacityState`, `FatigueState`, `TissueState`, `StressDoseSix`; JSONB `engine_state` on `athlete_states`; exercise ontology columns (`phi_*`, `energy_mix`, mechanical metadata); parameterized decays in `app/engine/parameters.py`; modality φ defaults in `app/engine/phi_table.py`; dose law + multi-F/T updates in `dose_engine` / `state_update`; ~251 exercise rows via `seed_exercises` + `exercise_bulk`. Still to do: Alembic revisions, program templates solver, EKF layer, per-exercise dose aggregation from logged sets.

---

## Objective

Transform the Performance Lab API into a fully parameterized, literature-informed, multi-domain training intelligence system.

The system must:

- **Model the athlete, not the workout**
- Support all major modalities:
  - Strength (powerlifting, Olympic lifting)
  - Hypertrophy
  - Endurance (running, conditioning)
  - CrossFit / Hyrox
  - Gymnastics / calisthenics
  - Grip / structural resilience
- Use a latent dynamical system with explicit:
  - State vector **X<sub>t</sub>**
  - Fatigue vector **F<sub>t</sub>**
  - Tissue tolerance vector **T<sub>t</sub>**
  - Stress dose **D<sub>t</sub>**
- Produce adaptive, explainable prescriptions
- Be **parameterized**, not hardcoded
- Be designed for future **EKF / Bayesian personalization**

---

## Core system model (mandatory)

### 1. State decomposition

Separate **capacity**, **fatigue**, and **tissue readiness**.

**Capacity state X<sub>t</sub>**

Components (example ordering — finalize in schema):

`[aerobic, glycolytic, max_strength, hypertrophy, power, skill, mobility, work_capacity]`

**Fatigue state F<sub>t</sub>**

`[cns, muscular, metabolic, structural, tendon, grip]`

**Tissue / structural readiness T<sub>t</sub>**

`[shoulder, elbow, wrist, lumbar, hip, knee, ankle, finger]`

### 2. Stress dose vector

Each workout maps to **D<sub>t</sub>**:

`[volume, intensity, density, impact, skill, metabolic]`

Derived from exercise features + execution (sets, reps, load, RPE/RIR, tempo, rest, novelty, proximity to failure, etc.).

### 3. Exercise mapping

Each exercise defines:

| Output | Vector (conceptual) |
|--------|---------------------|
| Adaptation | φ<sub>adapt</sub> = `[strength, hypertrophy, power, aerobic, anaerobic, skill, mobility]` |
| Fatigue | φ<sub>fatigue</sub> = `[cns, muscular, metabolic, structural, tendon, grip]` |
| Tissue load | φ<sub>tissue</sub> = `[shoulder, elbow, wrist, lumbar, hip, knee, ankle, finger]` |

### 4. State update equations

**Capacity update** (Banister-style + nonlinear extensions; parameters in config/DB):

`X_{t+1} = A·X_t + B·φ_adapt(D_t) + G·X_t + ε_t`

- **A** — decay / recovery
- **B** — dose–response
- **G** — cross-talk matrix
- **ε<sub>t</sub>** — process noise (placeholder for future filtering)

**Fatigue update** (multi-timescale):

`F_{t+1} = Λ·F_t + Ψ(D_t) − Ω(recovery inputs)`

Decay rates (parameterized):

- CNS → slow  
- Muscular → medium  
- Metabolic → fast  
- Structural / tendon → very slow  

**Tissue load update:**

`T_{t+1} = Γ·T_t + Υ(D_t)`

Tracks injury risk and structural tolerance.

### 5. Performance model

**Performance ∝ X<sub>t</sub> − F<sub>t</sub>** (conceptually — exact functional form to be chosen)

Subject to:

- Tissue constraints **T<sub>t</sub>**
- Skill ceilings
- Movement requirements

### 6. Prescription objective

`D*(t) = argmax_D J(X_t, F_t, T_t, goal, constraints)`

Constraints:

- Fatigue ceilings  
- Tissue limits  
- Movement balance  
- Recovery windows  

---

## Exercise ontology (major expansion)

Replace the current seed with a **fully structured ontology**.

### Required fields

**A. Mechanical**

- `movement_pattern`, `pattern_family`
- unilateral / bilateral
- ROM demand
- contraction bias

**B. Metadata**

- `modality`, `sport_domains`
- `equipment`, `load_type`, `scalable_by`

**C. Difficulty**

- `skill_demand`, `technical_ceiling`
- `recovery_cost`, `novelty_penalty`

**D. Vectors (critical)**

- adaptation vector (φ<sub>adapt</sub>)
- fatigue vector (φ<sub>fatigue</sub>)
- tissue vector (φ<sub>tissue</sub>)

**E. Energy system**

- aerobic / glycolytic / alactic mix

**F. Tags**

- `weak_point_tags` — expanded controlled vocabulary (see below)

---

## Weak point taxonomy (expand)

Structured tags (examples — extend in DB/config):

| Domain | Example tags |
|--------|----------------|
| strength | `lockout_strength`, `start_strength`, `bracing` |
| gymnastics | `false_grip`, `ring_support`, `handstand_line` |
| grip | `crush`, `pinch`, `support`, `finger` |
| endurance | `aerobic_base`, `lactate_threshold` |
| structural | `tendon_tolerance`, `joint_stability` |
| skill | `transition_skill`, `kip_efficiency` |

---

## Exercise library expansion

Target **250–400+** exercises minimum.

Must include coverage for:

- **Powerlifting** — competition lifts + variations (paused, tempo, pin, deficit)
- **Olympic lifting** — snatch, clean, jerk + derivatives
- **Gymnastics / calisthenics** — muscle-ups, levers, handstands, progressions
- **CrossFit / conditioning** — thrusters, wall balls, metcons, EMOM-style work
- **Grip** — grippers, pinches, hangs, thick bar
- **Endurance** — zone 2, tempo, intervals

---

## Fatigue model (critical upgrade)

Replace scalar / coarse fatigue with the full **F<sub>t</sub>** decomposition:

`[cns, muscular, metabolic, structural, tendon, grip]`

Each exercise contributes via **φ<sub>fatigue</sub>** and session aggregation rules (sum, caps, nonlinear saturation — parameterized).

---

## Dose computation

Per exercise *k*, conceptual form:

`D_exercise,k = w_k · log(1 + V) · I^α · Δ^β · N^γ · F^ρ`

Where (names are illustrative — bind to schema):

- **V** — volume  
- **I** — intensity  
- **Δ** — density  
- **N** — novelty  
- **F** — proximity to failure  
- **w<sub>k</sub>, α, β, γ, ρ** — parameters (from tables / literature ranges)

Session-level **D<sub>t</sub>** aggregates exercise-level contributions into the six-dimensional dose vector (volume, intensity, density, impact, skill, metabolic) per product design.

---

## Program and prescription engine

**Phase 1 (required)**

- Heuristic scoring  
- Constraint filtering  
- Best-session selection  

**Phase 2 (future)**

- Optimization-based solver  

---

## Program templates

Reusable templates (to be implemented as data + engine hooks):

| Modality | Templates |
|----------|-----------|
| Strength | linear progression, DUP, conjugate, peaking blocks |
| Hypertrophy | volume accumulation, intensity blocks |
| Endurance | zone 2 + threshold + VO₂ |
| CrossFit | engine, mixed modal, strength-bias |
| Gymnastics | skill ladders, progression chains |
| Grip | crush / pinch / support specialization |

---

## Progression logic

Modality-specific (parameterized rules):

- Strength → load progression  
- Hypertrophy → reps → load  
- Skill → regressions → progressions  
- Endurance → pace / density  
- Grip → time / load / diameter  

---

## Skill progression system

Each advanced movement should declare:

- prerequisites  
- regressions  
- progressions  

Example chain: **ring muscle-up** → false grip → transitions → assisted → strict.

---

## Benchmark system

Measurable anchors per domain:

- **Strength** — 1RM / 3RM / velocity  
- **Endurance** — VO₂ tests, time trials  
- **Gymnastics** — hold times, rep maxes  
- **CrossFit** — benchmark WODs  
- **Grip** — hangs, pinch, thick bar  

---

## Data assimilation (future)

Prepare for **EKF / Bayesian** updates:

`S_{t|t} = S_{t|t−1} + K · (y_t − h(S_t))`

Inputs (extensible):

- Performance tests  
- HRV  
- Subjective fatigue  
- Wearable data  

---

## Critical gaps vs current codebase

The current system lacks (non-exhaustive):

- Explicit fatigue decomposition **F<sub>t</sub>** as first-class state  
- Consistent exercise → vector mapping (φ<sub>adapt</sub>, φ<sub>fatigue</sub>, φ<sub>tissue</sub>)  
- Central **parameter tables** (literature-informed, DB or config)  
- Progression systems and skill graphs  
- Tissue tracking **T<sub>t</sub>**  
- Program templates wired to prescription  

---

## Parameter strategy

- Initialize from **literature ranges**  
- Store in **DB / config**  
- Allow **tuning** per deployment or per athlete (future)  
- Enable **future personalization** (EKF / Bayesian)  

---

## Implementation phases

| Phase | Scope |
|-------|--------|
| **1** | Schema expansion, exercise DB expansion, fatigue model, dose computation |
| **2** | State update engine, prescription logic, templates |
| **3** | Personalization (EKF), adaptive learning |

---

## Design principles

1. Model the athlete, not the workout  
2. Everything maps to **state change**  
3. Separate **capacity**, **fatigue**, and **structure**  
4. Keep parameters **configurable**  
5. Prioritize **explainability** over unnecessary complexity  
6. Start **heuristic** → evolve to **optimization**  

---

## Final deliverables (checklist)

- [ ] Expanded exercise ontology (300+ exercises)  
- [ ] Vectorized dose engine  
- [ ] Multi-component fatigue system  
- [ ] State update engine (X, F, T)  
- [ ] Program templates  
- [ ] Skill progression graphs  
- [ ] Benchmark system  
- [ ] Configurable parameter tables  

---

## Literature backbone (orientation)

Use as priors and citations for parameter ranges and model structure:

- Banister — fitness–fatigue model  
- Busso — nonlinear adaptation  
- Foster — training load  
- Impellizzeri — internal vs external load  
- Zourdos / Helms — RPE / strength  
- González-Badillo — velocity training  
- Morin / Samozino — power profiling  
- Daniels / Cooper — endurance modeling  
- Issurin — block periodization  

---

*This brief is the north star for incremental refactors; align PRs and schemas with these phases rather than implementing everything in one change.*
