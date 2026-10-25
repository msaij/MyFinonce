import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

// The chart library needs a real browser; the tiles and schedule do not.
vi.mock("@/components/shared/PlotlyChart", () => ({ PlotlyChart: () => null }));

import { EmiCalculator } from "./EmiCalculator";

const STORE_KEY = "mf.calculator.emi.v1";

describe("EmiCalculator", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    localStorage.clear();
  });

  /** Renders with these saved inputs (the first EMI in Oct 2026, so the months are fixed). */
  const renderWith = async (saved: Record<string, unknown>) => {
    localStorage.setItem(STORE_KEY, JSON.stringify({ start: "2026-10", ...saved }));
    await act(async () => root.render(<EmiCalculator />));
  };
  const text = () => container.textContent ?? "";
  const field = (label: string) => container.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
  const button = (label: string) => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === label) as HTMLButtonElement;

  it("switching the tenure unit keeps the same loan", async () => {
    await renderWith({ tenure: 15, unit: "months" });
    act(() => button("Yr").click());
    expect(field("Tenure").value).toBe("1.25");
    expect(text()).toContain("15 monthly instalments");
    act(() => button("Mo").click());
    expect(field("Tenure").value).toBe("15");
  });

  it("a 0% loan with prepayments shows no NaN", async () => {
    await renderWith({ rate: 0, prepayOn: true, monthly: 5000 });
    expect(text()).toContain("No interest at 0%");
    expect(text()).not.toContain("NaN");
  });

  it("the step-up box is disabled under Reduce EMI", async () => {
    await renderWith({ prepayOn: true, effect: "emi", stepUp: 5 });
    expect(field("EMI step-up % a year").disabled).toBe(true);
  });

  it("flags a one-time prepayment before the first EMI as not counted", async () => {
    await renderWith({ prepayOn: true, oneTime: [{ month: "2020-01", amount: 100_000 }] });
    expect(text()).toContain("Not counted: before the first EMI");
  });

  it("under Reduce EMI, a prepayment that clears the loan says it closed early", async () => {
    await renderWith({ amount: 100_000, rate: 10, tenure: 12, unit: "months", prepayOn: true, effect: "emi", oneTime: [{ month: "2026-10", amount: 1_000_000 }] });
    expect(text()).toContain("Loan closed early by");
    expect(text()).toContain("11 mo");
    expect(text()).toContain("for 1 month");
    expect(text()).not.toContain("EMI reduced by");
  });

  it("refuses an amount too large to work with", async () => {
    await renderWith({ amount: 1e300 });
    expect(container.querySelector(".mf-banner-warning")?.textContent).toContain("at most Rs 1 lakh crore");
  });
});
