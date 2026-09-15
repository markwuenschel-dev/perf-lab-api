// @vitest-environment jsdom
//
// src/perflab/InfoTip.test.tsx
//
// Help beside a field must reach every reader: pointer, tap, keyboard and assistive technology —
// and closing it must never move the reader's focus.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { InfoTip } from "./InfoTip";

beforeAll(() => {
  // Radix positions the popover with ResizeObserver, which jsdom does not provide.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  // Pointer type decides hover behavior; give jsdom a PointerEvent that carries it.
  if (!("PointerEvent" in window)) {
    class PointerEventWithType extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? "mouse";
      }
    }
    (window as unknown as { PointerEvent: unknown }).PointerEvent = PointerEventWithType;
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const SECTIONS = [
  { heading: "What it measures", text: "Your time for 5 km." },
  { heading: "How to measure", text: "Run 5 km as fast as you can." },
];

function renderTip() {
  render(
    <>
      <button type="button">Elsewhere</button>
      <InfoTip label="About 5 km time" sections={SECTIONS} />
    </>,
  );
  return screen.getByRole("button", { name: "About 5 km time" });
}

const help = () => screen.queryByRole("dialog");

describe("InfoTip", () => {
  it("is a native button with a name, described by the same help text", () => {
    const trigger = renderTip();
    expect(trigger.tagName).toBe("BUTTON");
    expect(trigger.getAttribute("type")).toBe("button"); // Enter and Space activate it natively
    const described = document.getElementById(trigger.getAttribute("aria-describedby") ?? "");
    expect(described?.textContent).toBe(
      "What it measures: Your time for 5 km. How to measure: Run 5 km as fast as you can.",
    );
  });

  it("shows the help when focused by keyboard, without moving focus, and hides it on blur", () => {
    const trigger = renderTip();
    act(() => trigger.focus());
    expect(help()).not.toBeNull();
    expect(help()?.textContent).toContain("Your time for 5 km.");
    expect(document.activeElement).toBe(trigger);
    act(() => trigger.blur());
    expect(help()).toBeNull();
  });

  it("stays open after a click or tap until clicked again", () => {
    const trigger = renderTip();
    fireEvent.click(trigger);
    expect(help()).not.toBeNull();
    fireEvent.click(trigger);
    expect(help()).toBeNull();
  });

  it("closes on Escape without moving focus", () => {
    const trigger = renderTip();
    fireEvent.click(trigger);
    expect(help()).not.toBeNull();
    const focused = document.activeElement;
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(help()).toBeNull();
    expect(document.activeElement).toBe(focused);
  });

  it("closes on an outside click without moving focus", async () => {
    const trigger = renderTip();
    fireEvent.click(trigger);
    // Radix starts listening for outside pointers on the next tick.
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    const focused = document.activeElement;
    fireEvent.pointerDown(screen.getByRole("button", { name: "Elsewhere" }));
    expect(help()).toBeNull();
    expect(document.activeElement).toBe(focused);
  });

  it("opens on hover, stays open while the pointer moves onto it, and closes after leaving both", () => {
    vi.useFakeTimers();
    const trigger = renderTip();
    fireEvent.pointerEnter(trigger, { pointerType: "mouse" });
    expect(help()).toBeNull();
    act(() => vi.advanceTimersByTime(200));
    const content = help();
    expect(content).not.toBeNull();

    fireEvent.pointerLeave(trigger, { pointerType: "mouse" });
    fireEvent.pointerEnter(content as HTMLElement, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(1000));
    expect(help()).not.toBeNull();

    fireEvent.pointerLeave(content as HTMLElement, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(1000));
    expect(help()).toBeNull();
  });

  it("does not open from a touch pointer passing over it", () => {
    vi.useFakeTimers();
    const trigger = renderTip();
    fireEvent.pointerEnter(trigger, { pointerType: "touch" });
    act(() => vi.advanceTimersByTime(1000));
    expect(help()).toBeNull();
  });
});
