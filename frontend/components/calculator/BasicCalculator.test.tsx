import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

// The news feed has its own keyboard tests (components/news/NewsKeyboard.test.tsx).
vi.mock("@/components/news/GlobalNewsFeed", () => ({ GlobalNewsFeed: () => null }));

import { BasicCalculator } from "./BasicCalculator";

describe("BasicCalculator keys", () => {
  let container: HTMLDivElement;
  let root: Root;
  // Stands in for a control elsewhere on the page, such as the EMI tab pill.
  let outside: HTMLButtonElement;

  beforeEach(async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    outside = document.createElement("button");
    outside.textContent = "EMI";
    document.body.appendChild(outside);
    root = createRoot(container);
    await act(async () => root.render(<BasicCalculator />));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    outside.remove();
  });

  const display = () => container.querySelector('input[aria-label="Calculator expression"]') as HTMLInputElement;
  const expression = () => display().value;
  const keypad = (label: string) => container.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement;
  const historyIsEmpty = () => (container.textContent ?? "").includes("Your calculations appear here");
  /** Dispatches a keydown; true when nothing called preventDefault (the browser's own action runs). */
  const key = (target: EventTarget, k: string) => {
    let notPrevented = true;
    act(() => {
      notPrevented = target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
    });
    return notPrevented;
  };

  it("leaves Enter, Escape and Delete on a focused button elsewhere on the page to that button", () => {
    for (const k of ["2", "+", "2"]) key(document.body, k);
    outside.focus();
    // jsdom never turns Enter into a click, so "not prevented" is what is checked.
    expect(key(outside, "Enter")).toBe(true);
    expect(key(outside, "Escape")).toBe(true);
    expect(key(outside, "Delete")).toBe(true);
    expect(expression()).toBe("2+2");
    expect(historyIsEmpty()).toBe(true);
    // Digits typed while it has focus still reach the calculator.
    expect(key(outside, "5")).toBe(false);
    expect(expression()).toBe("2+25");
  });

  it("Enter on a keypad key reached by Tab presses that key, not =", () => {
    key(document.body, "7");
    const seven = keypad("7");
    seven.focus();
    expect(key(seven, "Enter")).toBe(true);
    expect(historyIsEmpty()).toBe(true);
    // Escape inside the calculator still clears it.
    expect(key(seven, "Escape")).toBe(false);
    expect(expression()).toBe("");
  });

  it("after = in the focused display, an operator carries on from the result", async () => {
    const input = display();
    act(() => input.focus()); // focusing the display ends "after =", so it comes first
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "7+3");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => requestAnimationFrame(r)); // the caret is restored a frame later
    });
    expect(expression()).toBe("7+3");
    key(input, "Enter");
    expect(historyIsEmpty()).toBe(false);
    // The same keys typed without focusing the display: 10×, not 7+3×.
    key(input, "*");
    expect(expression()).toBe("10×");
  });

  it("clicking a key does not move focus onto it", () => {
    outside.focus();
    let notPrevented = true;
    act(() => {
      notPrevented = keypad("7").dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    });
    expect(notPrevented).toBe(false);
    // Whatever had focus outside lets go of it, so the next Enter is "=" again.
    expect(document.activeElement).toBe(document.body);
  });
});
