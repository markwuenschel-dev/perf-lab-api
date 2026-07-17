import { describe, expect, it } from "vitest";

import {
  KM_PER_MI,
  distLabel,
  fmtDist,
  fmtPace,
  isImperial,
  kgToLbs,
  kmToMi,
  lbsToKg,
  miToKm,
  paceLabel,
  parseMMSS,
  weightLabel,
} from "./units";

describe("units conversions", () => {
  it("round-trips km <-> mi", () => {
    expect(kmToMi(KM_PER_MI)).toBeCloseTo(1);
    expect(miToKm(1)).toBeCloseTo(KM_PER_MI);
  });

  it("round-trips kg <-> lbs", () => {
    expect(kgToLbs(1)).toBeCloseTo(2.20462);
    expect(lbsToKg(2.20462)).toBeCloseTo(1, 4);
  });
});

describe("unit labels", () => {
  it("switch on the imperial flag", () => {
    expect(isImperial("Imperial (mi)")).toBe(true);
    expect(isImperial("Metric")).toBe(false);
    expect(distLabel("Imperial (mi)")).toBe("mi");
    expect(distLabel("Metric")).toBe("km");
    expect(weightLabel("Imperial (mi)")).toBe("lbs");
    expect(paceLabel("Metric")).toBe("min/km");
  });
});

describe("formatting", () => {
  it("fmtPace formats seconds/mile as min:ss, converting to /km for metric", () => {
    expect(fmtPace(360, "Imperial (mi)")).toBe("6:00");
    expect(fmtPace(65, "Imperial (mi)")).toBe("1:05"); // pads the seconds
    expect(fmtPace(360, "Metric")).toBe("3:44"); // 360 / 1.60934 ≈ 224s
  });

  it("fmtDist converts and labels", () => {
    expect(fmtDist(KM_PER_MI, "Imperial (mi)")).toBe("1.0 mi");
    expect(fmtDist(5, "Metric")).toBe("5.0 km");
  });
});

describe("parseMMSS", () => {
  it("parses MM:SS to seconds", () => {
    expect(parseMMSS("6:00")).toBe(360);
    expect(parseMMSS("1:05")).toBe(65);
  });

  it("returns null on unparseable input", () => {
    expect(parseMMSS("abc")).toBeNull();
    expect(parseMMSS("6")).toBeNull();
    expect(parseMMSS("6:0:0")).toBeNull();
  });
});
