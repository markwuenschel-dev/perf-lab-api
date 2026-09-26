---
status: accepted
date: 2026-09-26
---
# The periodization envelope resolves a block's own template, inside the one entry point

Amends [ADR-0029](0029-periodization-intent-envelope.md).

Before phase 7, every block got one envelope. [`periodization_envelope`](../../app/logic/planning.py)
took no goal, so a runner got accumulation / intensification / **peak at RPE 8.5–9.5**. Meanwhile
goal-specific `PlanTemplate`s with their own bands sat unused beside it: running base 4.0–6.0,
threshold 6.5–8.0, race-specific 7.0–8.5, taper. ADR-0029 fixed the rule that exactly one
periodization source of truth lives on the block.

**Decision.**

1. **One entry point.** `periodization_envelope(..., goal=)` resolves the template internally. No
   caller chooses a template beside it. Every caller (the prescriber, load sizing and the
   projection) derives `goal` through one helper, `periodization_goal(block_goal, modality_mix)`,
   so they cannot disagree upstream.
2. **Block-owned data only.** The template is chosen from the block's goal and modality mix, and
   from nothing else. The athlete's profile goal and the planned day's domain are not consulted:
   a standing profile preference can drift from the block, which is the drift ADR-0029 exists to
   prevent.
3. **Exact, not inherited.** Only block goals with an authored template reach one: `Running` →
   running, and `Calisthenics` → the gymnastics skill template.
   - A Running block whose modality mix is sprint-primary (sprinting strictly the largest share)
     periodizes for `Sprinting`. Sprinting has no template, so it gets the generic envelope. A
     distance runner's shape is not inherited because sprinting sits in the running domain.
   - Templates keyed only by an athlete goal (Powerlifting, OlympicLifts, Grip, HalfMarathon,
     FullMarathon) stay reference data until a block can express that goal.
4. **Generic when nothing is authored.** Strength, hypertrophy, power, HYROX, CrossFit, general and
   recomp keep the generic envelope, unchanged. The explanation says so:
   `block:periodization=generic`. New bands are not invented for them.
5. **The block owns recovery; the template fills working weeks.** When a week is a deload (the
   block's cadence) or the taper (its final week) is decided by the block's calendar, exactly as
   before.
   - Recovery weeks take the template's DELOAD / TAPER band when it has one, else the generic
     band.
   - The remaining working weeks are numbered 1..N, and the n-th samples the middle of its share
     of the template's T non-recovery weeks: `ceil((n − ½)·T/N)`. So a deload never advances the
     athlete through the template, each phase keeps its share, and a block matching the template
     is the template.
   - This is an engineering interpretation of authored templates onto blocks of any length, not a
     physiological claim.
6. **The workload preference** still shifts working-week bands only, never recovery (unchanged).

**Consequences.**

- What changes today: Running and Calisthenics blocks change session length (`volume_modifier`
  scales `duration_min`) and the stated RPE band. No prescribed load moves: the envelope sizes
  load only for lifts with an e1RM code, and neither template's blocks prescribe them.
- The phase-1 characterization (`docs/simulations/phase-1.md`) is not rewritten. Its identity test
  is scoped to generically periodized cells; matrix #4 (`docs/simulations/phase-7.md`) is the
  evidence for the running change.
- Making another template reachable is a product change: a block model that can declare that
  goal. It is not an engine lookup change.
