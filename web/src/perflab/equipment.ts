// src/perflab/equipment.ts
//
// What the athlete HAS. One list, shared by the two screens that ask for it (onboarding and
// Settings) so they can never drift into offering different equipment.
//
// `equipment` filters exercise selection (a hard filter, app/services/prescription_service.py),
// and it is one of the four basics the prescribe gate requires
// (app/logic/onboarding_state.py:72). It has three stored states, and the difference between
// the first two matters: NOT SET ([] — nothing is filtered) is not the same answer as
// BODYWEIGHT ONLY (["bodyweight"] — only exercises needing no equipment).
//
// `equipment_preference` is a separate thing kept deliberately apart (docs/PRESCRIBER_LOGIC.md):
// a tie-break among exercises the athlete can already do, which can neither add nor remove one.

/**
 * Equipment tags the catalog actually requires (app/data/exercise_bulk.py, seed_exercises.py).
 *
 * EVERY tag the catalog requires must be offered here: an exercise is kept only when all of its
 * required tags are selected, so a tag missing from this list makes those exercises unreachable
 * for anyone who configures equipment (tests/test_equipment_picker_matches_catalog.py fails on
 * drift in either direction). Grouped: free weights, loaded implements, machines, bodyweight
 * apparatus, conditioning.
 */
export const EQUIPMENT_TAGS: { tag: string; label: string }[] = [
  { tag: "barbell", label: "Barbell" },
  { tag: "plates", label: "Weight plates" },
  { tag: "trap_bar", label: "Trap bar" },
  { tag: "dumbbells", label: "Dumbbells" },
  { tag: "kettlebell", label: "Kettlebells" },
  { tag: "sandbag", label: "Sandbag" },
  { tag: "wall_ball", label: "Wall ball" },
  { tag: "vest", label: "Weight vest" },
  { tag: "gripper", label: "Hand grippers" },
  { tag: "atlas_stone", label: "Atlas stone" },
  { tag: "log_bar", label: "Log bar" },
  { tag: "yoke", label: "Yoke" },
  { tag: "keg", label: "Keg" },
  { tag: "tire", label: "Tire" },
  { tag: "machine", label: "Machines" },
  { tag: "cable", label: "Cable machine" },
  { tag: "band", label: "Resistance band" },
  { tag: "pullup_bar", label: "Pull-up bar" },
  { tag: "box", label: "Plyo box" },
  { tag: "rings", label: "Rings" },
  { tag: "parallettes", label: "Parallettes" },
  { tag: "rope", label: "Climbing rope" },
  { tag: "ab_wheel", label: "Ab wheel" },
  { tag: "jump_rope", label: "Jump rope" },
  { tag: "battle_ropes", label: "Battle ropes" },
  { tag: "rower", label: "Rower" },
  { tag: "skierg", label: "SkiErg" },
  { tag: "bike", label: "Bike" },
  { tag: "assault_bike", label: "Air bike" },
  { tag: "sled", label: "Sled" },
];

export const PREFERENCE_OPTIONS: { value: string; label: string }[] = [
  { value: "barbell", label: "Barbells" },
  { value: "dumbbell", label: "Dumbbells" },
  { value: "machine", label: "Machines" },
];

export type EquipmentMode = "unset" | "bodyweight" | "equipment";

export const BODYWEIGHT_ONLY_TAGS = new Set(["bodyweight", "none"]);

/** The stored list read back as the answer it represents. */
export function equipmentModeOf(list: string[]): EquipmentMode {
  const tags = list.map((t) => t.trim().toLowerCase()).filter(Boolean);
  if (tags.length === 0) return "unset";
  if (tags.every((t) => BODYWEIGHT_ONLY_TAGS.has(t))) return "bodyweight";
  return "equipment";
}

export const MODE_OPTIONS: { mode: EquipmentMode; label: string; help: string }[] = [
  { mode: "unset", label: "Not set", help: "Not set: any exercise may appear in your sessions." },
  { mode: "bodyweight", label: "Bodyweight only", help: "Only exercises that need no equipment." },
  { mode: "equipment", label: "I have equipment", help: "Only exercises you can do with what you select." },
];
