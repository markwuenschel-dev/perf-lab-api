/** Unit conversion and formatting utilities. All internal state is metric (kg, km). */

export const isImperial = (units: string) => units === "Imperial (mi)";

export const KM_PER_MI = 1.60934;

export const kmToMi = (km: number) => km / KM_PER_MI;
export const miToKm = (mi: number) => mi * KM_PER_MI;
export const lbsToKg = (lbs: number) => lbs * 0.453592;
export const kgToLbs = (kg: number) => kg * 2.20462;

export const distLabel = (units: string) => (isImperial(units) ? "mi" : "km");
export const weightLabel = (units: string) => (isImperial(units) ? "lbs" : "kg");
export const paceLabel = (units: string) => (isImperial(units) ? "min/mi" : "min/km");

/** Format a backend pace value (seconds per mile) into a min:ss string in the user's unit. */
export const fmtPace = (secPerMile: number, units: string): string => {
  const spu = isImperial(units) ? secPerMile : Math.round(secPerMile / KM_PER_MI);
  return `${Math.floor(spu / 60)}:${String(spu % 60).padStart(2, "0")}`;
};

/** Format a km value as a localised distance string. */
export const fmtDist = (km: number, units: string, decimals = 1): string =>
  `${(isImperial(units) ? kmToMi(km) : km).toFixed(decimals)} ${distLabel(units)}`;

/** Parse "MM:SS" or "M:SS" → total seconds, or null if unparseable. */
export const parseMMSS = (t: string): number | null => {
  const parts = t.split(":").map(Number);
  if (parts.length === 2 && parts.every((n) => !isNaN(n))) return parts[0] * 60 + parts[1];
  return null;
};
