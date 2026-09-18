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

/** Equipment tags the catalog actually requires (app/data/exercise_bulk.py, seed_exercises.py). */
export const EQUIPMENT_TAGS: { tag: string; label: string }[] = [
  { tag: "barbell", label: "Barbell" },
  { tag: "dumbbells", label: "Dumbbells" },
  { tag: "kettlebell", label: "Kettlebells" },
  { tag: "machine", label: "Machines" },
  { tag: "cable", label: "Cable machine" },
  { tag: "pullup_bar", label: "Pull-up bar" },
  { tag: "box", label: "Plyo box" },
  { tag: "rings", label: "Rings" },
  { tag: "parallettes", label: "Parallettes" },
  { tag: "jump_rope", label: "Jump rope" },
  { tag: "rower", label: "Rower" },
  { tag: "bike", label: "Bike" },
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
