# Simulation matrix #5: phase 9 exit (equipment kits)

**Scope: a fresh-athlete equipment matrix.** Each of the 36 planned-day bindings runs under
4 equipment kits, 144 cells in all, through the real prescriber against the seeder catalog.
The athlete is intermediate and fresh, in week 2 of an 8-week block. Only the equipment
varies.

This is **not** a claim about every athlete state. A fatigued athlete can still hit the
slot-less readiness redirect "Active Recovery". With a barbell listed, that redirect draws on
the generic equipment map and prescribes Back Squat / RDL / Bench. That behaviour predates
phase 9 and has its own open item. Until it is fixed, phase 9 has eliminated filler on
planned days for a fresh athlete, not inappropriate fallback in every state.

Regenerate the matrix below with:

    uv run python -m app.scripts.kit_matrix

## The invariant (ADR-0072)

Equipment adaptation may change the exercise implementation. It must not silently change
the session's training intent or sport identity. Each cell is classified from first
principles (`app/scripts/kit_matrix.py`): the winning template's slots are re-resolved
against the same kit, because explanation codes cannot tell the equipment map from a catalog
selection. The cell types:

| mark | outcome | meaning |
|---|---|---|
| ✓ | followed | the planned template, every authored slot realized |
| ↻ | replaced | a different session of the SAME domain, stated: `plan:session_replaced=<slug>(<reason>)` |
| ∅ | unavailable | explicit zero-work Equipment Unavailable: `plan:session_unavailable=<slug>` + `equipment:unavailable=<tags>` |
| ✗ filler | (never) | a slot-less template, the generic equipment map, or another domain's template |
| ✗ partial | (never) | only some of an authored session's slots |

The kits:
- **unconfigured:** equipment never set, nothing filtered.
- **full_gym:** every catalog tag.
- **home:** dumbbells, kettlebell and pull-up bar.
- **bodyweight:** `["bodyweight"]`.

What each kit must support is authored per day in `app/logic/kit_support.py`.

## Before and after

| | before phase 9 (main `56f2e53`) | after (9.4c) |
|---|---|---|
| filler cells | 39 | **0** |
| partial cells | 19 | **0** |
| dishonest cells in total | 58 | **0** |
| templates with no exercise slots | 7 of 45 | 0 of 50 |

Before, three planned days (Strength — Volume, Accessory / Isolation, Neural Priming)
prescribed "Air Squat, Push-up, Lunges" even to a fully equipped athlete. A HYROX day at home
became full-body GPP through a silent general-pool fallback.

## The matrix

| domain | planned day | unconfigured | full_gym | home | bodyweight |
|---|---|---|---|---|---|
| strength | Max Strength | ✓ `strength_max` | ✓ `strength_max` | ↻ `strength_volume_home` | ∅ |
| strength | Strength — Volume | ✓ `strength_volume` | ✓ `strength_volume` | ↻ `strength_volume_home` | ∅ |
| strength | Accessory Focus | ↻ `strength_max` | ↻ `strength_max` | ↻ `strength_volume_home` | ∅ |
| hypertrophy | High Volume Upper | ✓ `hyp_upper_split` | ✓ `hyp_upper_split` | ↻ `hyp_upper_split_home` | ∅ |
| hypertrophy | High Volume Lower | ✓ `hyp_high_vol` | ✓ `hyp_high_vol` | ↻ `hyp_high_vol_home` | ∅ |
| hypertrophy | Accessory / Isolation | ✓ `hyp_maintenance` | ✓ `hyp_maintenance` | ↻ `hyp_maintenance_home` | ∅ |
| hypertrophy | High Volume | ✓ `hyp_high_vol` | ✓ `hyp_high_vol` | ↻ `hyp_high_vol_home` | ∅ |
| running | Aerobic Base | ✓ `run_z2_base` | ✓ `run_z2_base` | ✓ `run_z2_base` | ✓ `run_z2_base` |
| running | Threshold Work | ✓ `run_threshold` | ✓ `run_threshold` | ✓ `run_threshold` | ✓ `run_threshold` |
| running | Speed | ✓ `run_sprint` | ✓ `run_sprint` | ✓ `run_sprint` | ✓ `run_sprint` |
| running | Active Recovery | ✓ `run_recovery` | ✓ `run_recovery` | ✓ `run_recovery` | ✓ `run_recovery` |
| power | Power Development | ✓ `power_main` | ✓ `power_main` | ↻ `power_reactive` | ↻ `power_reactive` |
| power | Neural Priming | ✓ `power_neural_prime` | ✓ `power_neural_prime` | ↻ `power_neural_prime_jumps` | ↻ `power_neural_prime_jumps` |
| power | Strength Potentiation | ✓ `power_potentiation` | ✓ `power_potentiation` | ∅ | ∅ |
| powerlifting | SBD Strength | ✓ `pl_sbd_main` | ✓ `pl_sbd_main` | ∅ | ∅ |
| powerlifting | Accessory Focus | ✓ `pl_accessory` | ✓ `pl_accessory` | ∅ | ∅ |
| weightlifting | Weightlifting Technique | ✓ `wl_technique_cj` | ✓ `wl_technique_cj` | ∅ | ∅ |
| mixed | MetCon | ✓ `metcon_mixed_modal` | ✓ `metcon_mixed_modal` | ∅ | ∅ |
| mixed | Metabolic Conditioning | ✓ `metcon_mixed_modal` | ✓ `metcon_mixed_modal` | ∅ | ∅ |
| mixed | Mixed Modal | ✓ `metcon_mixed_modal` | ✓ `metcon_mixed_modal` | ∅ | ∅ |
| mixed | Engine Work | ✓ `metcon_engine` | ✓ `metcon_engine` | ∅ | ∅ |
| mixed | Strength Endurance | ✓ `mixed_strength_endurance` | ✓ `mixed_strength_endurance` | ∅ | ∅ |
| mixed | Hyrox Simulation | ✓ `hyrox_half_sim_b` | ✓ `hyrox_half_sim_b` | ∅ | ∅ |
| mixed | Running + Functional | ✓ `run_functional_lunges` | ✓ `run_functional_lunges` | ∅ | ∅ |
| mixed | Strength + Skill | ✓ `cf_strength_skill_deadlift` | ✓ `cf_strength_skill_deadlift` | ∅ | ∅ |
| calisthenics | Skill & Straight-Arm Strength | ✓ `cal_skill` | ✓ `cal_skill` | ✓ `cal_skill` | ✓ `cal_skill` |
| calisthenics | Bodyweight Strength | ✓ `cal_strength` | ✓ `cal_strength` | ✓ `cal_strength` | ✓ `cal_strength` |
| calisthenics | Gymnastics Conditioning | ✓ `cal_conditioning` | ✓ `cal_conditioning` | ✓ `cal_conditioning` | ↻ `cal_strength` |
| gymnastics | Gymnastics Skill | ✓ `gym_skill` | ✓ `gym_skill` | ✓ `gym_skill` | ✓ `gym_skill` |
| grip | Grip & Support | ✓ `grip_main` | ✓ `grip_main` | ✓ `grip_main` | ↻ `grip_recovery` |
| general | Full-Body GPP | ✓ `gpp_balanced` | ✓ `gpp_balanced` | ✓ `gpp_balanced` | ✓ `gpp_balanced` |
| general | Active Recovery | ✓ `gpp_mobility` | ✓ `gpp_mobility` | ✓ `gpp_mobility` | ✓ `gpp_mobility` |
| general | Aerobic + Strength | ✓ `gpp_strength_foundation` | ✓ `gpp_strength_foundation` | ↻ `gpp_balanced` | ↻ `gpp_balanced` |
| general | Strength Preservation | ✓ `gpp_strength_foundation` | ✓ `gpp_strength_foundation` | ↻ `gpp_balanced` | ↻ `gpp_balanced` |
| general | Metabolic Conditioning | ✓ `gpp_conditioning` | ✓ `gpp_conditioning` | ↻ `gpp_balanced` | ↻ `gpp_balanced` |
| conditioning | Metabolic Conditioning | ✓ `gpp_conditioning` | ✓ `gpp_conditioning` | ↻ `gpp_balanced` | ↻ `gpp_balanced` |

144 cells, 0 not honest.

## What the unsupported cells do

The sport-specific days (powerlifting, weightlifting, HYROX, CrossFit strength and skill,
engine, strength endurance, metcon) and Strength Potentiation are authored as gym-only. At
home or bodyweight they are:
- replaced in-domain, where a whole same-domain session exists, and stated; or
- explicitly Equipment Unavailable, with zero work.

The unavailable prescription carries a structured `session_unavailable` record: planned
domain, category, slug, branch ids and the missing equipment. So "which planned days are most
often impossible?" is a query.

## Known gaps, recorded rather than assumed away

- **Web-configured full gym.** The web picker cannot express `skierg`, `sandbag`, `wall_ball`
  and similar tags. An athlete who configured equipment in the web app therefore gets Hyrox
  Simulation and Running + Functional as unavailable, even with a full gym. The separate web
  equipment-tags PR empties that list (`UI_UNSUPPORTED` in `tests/test_kit_eligibility.py`).
- **Readiness redirects are slot-less**, as described above.
- **Home strength has a ceiling.** `strength_volume_home` uses a Goblet Squat. For a strong
  athlete that may be light: an equipment limit and a progression question, not a reason to
  fake a barbell pattern.
- **The catalog does not model benches.** Home variants use DB Floor Press and flat Split
  Squat, and a test forbids a kit variant from pinning a bench movement.
- **Replacements that shift emphasis.** Some in-domain replacements are stated but change
  emphasis: GPP conditioning or strength-preservation days at home become Full-Body GPP, and
  Gymnastics Conditioning at bodyweight becomes Bodyweight Strength. They are honest by the
  matrix and candidates for variety work in phase 9b.

## Live check

Not deployed yet. After deploy, append a read-only live check: one SELECT, then in-memory
prescribing against the production catalog.
