// src/perflab/domains.ts
//
// The single frontend source for canonical training domains — mirrors the backend
// `DOMAINS` set (app/logic/domain_vocab.py). Objectives are created with these values;
// `objective_service.normalize_domain_at_boundary` rejects anything non-canonical with a
// 400, so any domain picker MUST source from here (the old ad-hoc lists included
// non-canonical `hyrox`/`other` that would fail).

export interface DomainOption {
  value: string; // canonical DomainCode
  label: string;
}

export const DOMAIN_OPTIONS: DomainOption[] = [
  { value: "strength", label: "General Strength" },
  { value: "powerlifting", label: "Powerlifting" },
  { value: "weightlifting", label: "Olympic Weightlifting" },
  { value: "hypertrophy", label: "Hypertrophy / Muscle" },
  { value: "power", label: "Power / Explosiveness" },
  { value: "running", label: "Running / Endurance" },
  { value: "gymnastics", label: "Gymnastics" },
  { value: "calisthenics", label: "Calisthenics" },
  { value: "grip", label: "Grip Strength" },
  { value: "mixed", label: "Mixed / CrossFit" },
  { value: "general", label: "General Fitness" },
];

const _LABEL = new Map(DOMAIN_OPTIONS.map((d) => [d.value, d.label]));

/** Friendly label for a canonical domain, falling back to a title-cased value. */
export function domainLabel(value: string | null | undefined): string {
  if (!value) return "General";
  return _LABEL.get(value) ?? value.charAt(0).toUpperCase() + value.slice(1);
}
