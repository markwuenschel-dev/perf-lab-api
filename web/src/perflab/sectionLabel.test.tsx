// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { cn } from "@/lib/utils";

import { SectionLabel } from "./ui";

// The class strings the screens built before the compact variant existed. Written out here,
// not imported, so the test compares against the old appearance rather than restating the new code.
const DEFAULT = "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]";
const OLD_OVERRIDE = "text-[10px] text-faint";

afterEach(cleanup);

function classOf(el: React.ReactElement): string {
  const { container } = render(el);
  return (container.firstElementChild as HTMLElement).className;
}

describe("SectionLabel compact keeps the console screens' labels exactly as they were", () => {
  it("renders the default label unchanged", () => {
    expect(classOf(<SectionLabel>Default</SectionLabel>)).toBe(cn(DEFAULT));
    expect(classOf(<SectionLabel>Default</SectionLabel>)).toContain("text-[11px]");
  });

  // Every form the 45 overrides used: the bare constant, and the constant plus a margin.
  it.each(["", "mb-2", "mb-[11px]", "mb-[14px]"])("compact with className %j matches the old override", (extra) => {
    const before = cn(DEFAULT, extra ? `${OLD_OVERRIDE} ${extra}` : OLD_OVERRIDE);
    const after = classOf(
      <SectionLabel compact className={extra || undefined}>
        Label
      </SectionLabel>,
    );
    expect(after).toBe(before);
    expect(after).toContain("text-[10px]");
    expect(after).toContain("text-faint");
    expect(after).not.toContain("text-[11px]");
  });
});
