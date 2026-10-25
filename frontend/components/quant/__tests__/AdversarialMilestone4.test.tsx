import { describe, it, expect } from "vitest";
import {
  calculateFactorPerturbation,
  CRISIS_PRESETS,
  ScenarioStressSimulator,
} from "../ScenarioStressSimulator";
import {
  buildFactorWaterfallFigure,
  FactorWaterfallChart,
} from "../FactorWaterfallChart";

describe("Milestone 4 Adversarial Stress Harness: ScenarioStressSimulator Math & Edge Cases", () => {
  // ---------------------------------------------------------------------------
  // 1. Boundary Slider Perturbations: Exact -30%, +30%, -20%, +20%
  // ---------------------------------------------------------------------------
  it("adversarially tests maximum boundary shocks across all sliders simultaneously", () => {
    const betas = { market: 1.2, size: 0.6, value: -0.4, momentum: 0.3 };
    const maxNegativeShocks = { market: -30.0, size: -20.0, value: -20.0, momentum: -20.0 };
    const maxPositiveShocks = { market: 30.0, size: 20.0, value: 20.0, momentum: 20.0 };

    // Expected Negative:
    // (1.2 * -30) + (0.6 * -20) + (-0.4 * -20) + (0.3 * -20)
    // = -36.0 - 12.0 + 8.0 - 6.0 = -46.0%
    const resNeg = calculateFactorPerturbation(betas, maxNegativeShocks, 100000);
    expect(resNeg.simulated_return_delta_pct).toBe(-46.0);
    expect(resNeg.stressed_portfolio_cagr_impact).toBe(-46.0);
    expect(resNeg.rupee_capital_impact).toBe(-46000.0);
    expect(resNeg.projected_capital_terminal_value).toBe(54000.0);

    // Expected Positive:
    // (1.2 * 30) + (0.6 * 20) + (-0.4 * 20) + (0.3 * 20)
    // = 36.0 + 12.0 - 8.0 + 6.0 = +46.0%
    const resPos = calculateFactorPerturbation(betas, maxPositiveShocks, 100000);
    expect(resPos.simulated_return_delta_pct).toBe(46.0);
    expect(resPos.stressed_portfolio_cagr_impact).toBe(46.0);
    expect(resPos.rupee_capital_impact).toBe(46000.0);
    expect(resPos.projected_capital_terminal_value).toBe(146000.0);
  });

  it("handles mixed alternating extreme boundary perturbations", () => {
    // Alternating shocks: market -30%, size +20%, value -20%, momentum +20%
    const betas = { market: 1.5, size: -0.5, value: 0.8, momentum: -0.2 };
    const mixedShocks = { market: -30.0, size: 20.0, value: -20.0, momentum: 20.0 };

    // Expected:
    // (1.5 * -30) + (-0.5 * 20) + (0.8 * -20) + (-0.2 * 20)
    // = -45.0 - 10.0 - 16.0 - 4.0 = -75.0%
    const res = calculateFactorPerturbation(betas, mixedShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(-75.0);
    expect(res.rupee_capital_impact).toBe(-75000.0);
    expect(res.projected_capital_terminal_value).toBe(25000.0);
  });

  it("tests fractional boundary slider step perturbations (e.g. -29.5%, +29.5%)", () => {
    const betas = { market: 1.0, size: 0.5, value: 0.2, momentum: 0.1 };
    const shocks = { market: -29.5, size: 19.5, value: -19.5, momentum: 19.5 };

    // Expected:
    // (1.0 * -29.5) + (0.5 * 19.5) + (0.2 * -19.5) + (0.1 * 19.5)
    // = -29.5 + 9.75 - 3.90 + 1.95 = -21.7%
    const res = calculateFactorPerturbation(betas, shocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(-21.7);
    expect(res.rupee_capital_impact).toBe(-21700.0);
    expect(res.projected_capital_terminal_value).toBe(78300.0);
  });

  // ---------------------------------------------------------------------------
  // 2. Zero Shocks & Invariance Properties
  // ---------------------------------------------------------------------------
  it("preserves exact zero shock invariance across positive, negative, and extreme betas", () => {
    const zeroShocks = { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 };
    const extremeBetas = { market: 5.0, size: -4.8, value: 3.2, momentum: -5.0 };

    const res = calculateFactorPerturbation(extremeBetas, zeroShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(0.0);
    expect(res.stressed_portfolio_cagr_impact).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(0.0);
    expect(res.projected_capital_terminal_value).toBe(100000.0);
    expect(res.factor_contributions_pct).toEqual({
      market: 0.0,
      size: 0.0,
      value: 0.0,
      momentum: 0.0,
    });
  });

  it("preserves zero beta invariance when fund has zero factor exposures", () => {
    const zeroBetas = { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 };
    const hugeShocks = { market: -30.0, size: 20.0, value: -20.0, momentum: 20.0 };

    const res = calculateFactorPerturbation(zeroBetas, hugeShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(0.0);
    expect(res.projected_capital_terminal_value).toBe(100000.0);
  });

  // ---------------------------------------------------------------------------
  // 3. Extreme Betas (+5.0, -5.0, and Asymmetric Extremes)
  // ---------------------------------------------------------------------------
  it("stress tests extreme high positive beta (+5.0 across all factors)", () => {
    const extremePositiveBetas = { market: 5.0, size: 5.0, value: 5.0, momentum: 5.0 };
    const crashShocks = { market: -30.0, size: -20.0, value: -10.0, momentum: -10.0 };

    // Expected: 5.0 * (-30 - 20 - 10 - 10) = 5.0 * -70.0 = -350.0%
    const res = calculateFactorPerturbation(extremePositiveBetas, crashShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(-350.0);
    expect(res.rupee_capital_impact).toBe(-350000.0);
    expect(res.projected_capital_terminal_value).toBe(-250000.0);
    expect(res.factor_contributions_pct.market).toBe(-150.0);
    expect(res.factor_contributions_pct.size).toBe(-100.0);
    expect(res.factor_contributions_pct.value).toBe(-50.0);
    expect(res.factor_contributions_pct.momentum).toBe(-50.0);
  });

  it("stress tests extreme negative beta (-5.0 across all factors - short/inverse fund behavior)", () => {
    const extremeNegativeBetas = { market: -5.0, size: -5.0, value: -5.0, momentum: -5.0 };
    const crashShocks = { market: -30.0, size: -20.0, value: -10.0, momentum: -10.0 };

    // Expected: -5.0 * (-70.0) = +350.0% (Inverse fund gains under market collapse)
    const res = calculateFactorPerturbation(extremeNegativeBetas, crashShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(350.0);
    expect(res.rupee_capital_impact).toBe(350000.0);
    expect(res.projected_capital_terminal_value).toBe(450000.0);
  });

  it("handles out-of-distribution high-magnitude betas (+50.0 and -50.0) without NaN or overflow", () => {
    const oodBetas = { market: 50.0, size: -50.0, value: 25.0, momentum: -25.0 };
    const shocks = { market: -5.0, size: 4.0, value: -2.0, momentum: 3.0 };

    // Expected: (50 * -5) + (-50 * 4) + (25 * -2) + (-25 * 3) = -250 - 200 - 50 - 75 = -575.0%
    const res = calculateFactorPerturbation(oodBetas, shocks, 100000);
    expect(Number.isFinite(res.simulated_return_delta_pct)).toBe(true);
    expect(Number.isNaN(res.simulated_return_delta_pct)).toBe(false);
    expect(res.simulated_return_delta_pct).toBe(-575.0);
    expect(res.rupee_capital_impact).toBe(-575000.0);
  });

  // ---------------------------------------------------------------------------
  // 4. Beta Key Aliases & Robust Fallbacks
  // ---------------------------------------------------------------------------
  it("correctly resolves alternative factor beta keys (mkt_excess, smb, hml, wml)", () => {
    const backendBetas = {
      mkt_excess: 1.15,
      smb: 0.35,
      hml: -0.15,
      wml: 0.25,
    };
    const shocks = { market: -10.0, size: -5.0, value: 5.0, momentum: 10.0 };

    // Expected: (1.15 * -10) + (0.35 * -5) + (-0.15 * 5) + (0.25 * 10)
    // = -11.5 - 1.75 - 0.75 + 2.5 = -11.5%
    const res = calculateFactorPerturbation(backendBetas, shocks);
    expect(res.simulated_return_delta_pct).toBe(-11.5);
    expect(res.factor_contributions_pct.market).toBe(-11.5);
    expect(res.factor_contributions_pct.size).toBe(-1.75);
    expect(res.factor_contributions_pct.value).toBe(-0.75);
    expect(res.factor_contributions_pct.momentum).toBe(2.5);
  });

  it("handles empty beta dictionary `{}` and missing keys gracefully with zero defaults", () => {
    const emptyBetas = {};
    const shocks = { market: -15.0, size: 5.0, value: -3.0, momentum: 8.0 };

    const res = calculateFactorPerturbation(emptyBetas, shocks);
    expect(res.simulated_return_delta_pct).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(0.0);
    expect(res.projected_capital_terminal_value).toBe(100000.0);
  });

  it("handles undefined, null, and partial beta values without runtime errors", () => {
    const partialBetas = {
      market: 1.2,
      size: undefined,
      value: null as any,
      // momentum omitted entirely
    };
    const shocks = { market: -10.0, size: -5.0, value: 2.0, momentum: 3.0 };

    // Only market should contribute: 1.2 * -10 = -12.0%
    const res = calculateFactorPerturbation(partialBetas, shocks);
    expect(res.simulated_return_delta_pct).toBe(-12.0);
    expect(res.factor_contributions_pct.size).toBe(0.0);
    expect(res.factor_contributions_pct.value).toBe(0.0);
    expect(res.factor_contributions_pct.momentum).toBe(0.0);
  });

  // ---------------------------------------------------------------------------
  // 5. Mathematical Linearity & Superposition
  // ---------------------------------------------------------------------------
  it("proves linearity property: Delta R(k * beta, shock) == k * Delta R(beta, shock)", () => {
    const baseBetas = { market: 1.1, size: 0.3, value: -0.2, momentum: 0.15 };
    const scaledBetas = { market: 2.2, size: 0.6, value: -0.4, momentum: 0.3 };
    const shocks = { market: -12.5, size: 8.0, value: -5.0, momentum: 4.0 };

    const resBase = calculateFactorPerturbation(baseBetas, shocks);
    const resScaled = calculateFactorPerturbation(scaledBetas, shocks);

    expect(resScaled.simulated_return_delta_pct).toBeCloseTo(resBase.simulated_return_delta_pct * 2, 3);
  });

  it("proves superposition property: Delta R(beta, shockA + shockB) == Delta R(beta, shockA) + Delta R(beta, shockB)", () => {
    const betas = { market: 1.25, size: 0.45, value: -0.35, momentum: 0.2 };
    const shockA = { market: -10.0, size: -5.0, value: 2.0, momentum: -4.0 };
    const shockB = { market: -5.0, size: 10.0, value: -4.0, momentum: 6.0 };
    const shockCombined = { market: -15.0, size: 5.0, value: -2.0, momentum: 2.0 };

    const resA = calculateFactorPerturbation(betas, shockA);
    const resB = calculateFactorPerturbation(betas, shockB);
    const resCombined = calculateFactorPerturbation(betas, shockCombined);

    const sumDelta = Number((resA.simulated_return_delta_pct + resB.simulated_return_delta_pct).toFixed(4));
    expect(resCombined.simulated_return_delta_pct).toBeCloseTo(sumDelta, 3);
  });
});

describe("Milestone 4 Adversarial Stress Harness: FactorWaterfallChart Contract & Invariance", () => {
  it("handles completely empty inputs with default values safely", () => {
    const fig = buildFactorWaterfallFigure({});
    expect(fig.data).toBeDefined();
    expect(fig.data.length).toBe(1);

    const trace = fig.data[0] as any;
    expect(trace.type).toBe("waterfall");
    expect(trace.orientation).toBe("v");
    expect(trace.x).toEqual(["Manager Alpha", "Net Return"]);
    expect(trace.y).toEqual([0.0, 0.0]);
    expect(trace.measure).toEqual(["relative", "total"]);
  });

  it("preserves strict mathematical identity: Net Return == Alpha + sum(Factors) - TER", () => {
    const alpha = 3.4567;
    const factors = {
      market: 11.2345,
      size: 2.8765,
      value: -1.9876,
      momentum: 4.1234,
    };
    const ter = 0.85;

    // Net return = 3.4567 + 11.2345 + 2.8765 - 1.9876 + 4.1234 - 0.85 = 18.8535%
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: alpha,
      factorContributions: factors,
      feeDragPct: ter,
    });

    const trace = fig.data[0] as any;
    const yVals = trace.y;
    expect(yVals[0]).toBe(3.4567);
    expect(yVals[yVals.length - 2]).toBe(-0.85); // TER Fee Drag must be negative
    expect(yVals[yVals.length - 1]).toBe(18.8535); // Net Return
  });

  it("guarantees feeDragPct is always rendered as negative drag even if positive or negative passed", () => {
    const figPos = buildFactorWaterfallFigure({
      alphaAnnPct: 2.0,
      feeDragPct: 0.95,
    });
    const figNeg = buildFactorWaterfallFigure({
      alphaAnnPct: 2.0,
      feeDragPct: -0.95,
    });

    const tracePos = figPos.data[0] as any;
    const traceNeg = figNeg.data[0] as any;

    const terIdxPos = tracePos.x.indexOf("TER Fee Drag");
    const terIdxNeg = traceNeg.x.indexOf("TER Fee Drag");

    expect(tracePos.y[terIdxPos]).toBe(-0.95);
    expect(traceNeg.y[terIdxNeg]).toBe(-0.95);
  });

  it("enforces measure sequence: exactly all 'relative' until the final 'total'", () => {
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: 1.0,
      factorContributions: { market: 8.0, size: 2.0, value: 1.0, momentum: -1.0, quality: 0.5 },
      feeDragPct: 0.5,
    });

    const trace = fig.data[0] as any;
    const measures: string[] = trace.measure;
    expect(measures[measures.length - 1]).toBe("total");
    expect(measures.slice(0, -1).every((m) => m === "relative")).toBe(true);
    expect(measures.length).toBe(trace.x.length);
  });

  it("formats text array with appropriate +/- sign prefixes matching values", () => {
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: 2.5,
      factorContributions: { market: 10.0, value: -3.0 },
      feeDragPct: 0.75,
    });

    const trace = fig.data[0] as any;
    expect(trace.text).toEqual([
      "+2.50%",
      "+10.00%",
      "-3.00%",
      "-0.75%",
      "8.75%", // Total does not prefix with '+'
    ]);
  });
});

describe("Milestone 4 Adversarial Stress Harness: 422 Fallback & Missing Factor Handling", () => {
  it("verifies fallback scenario simulation when factor attribution returns 422 error", () => {
    // In quant/page.tsx:
    // when factorsResult is undefined (422 response / insufficient history),
    // fallback factorBetas = { market: result?.benchmark.metrics?.beta ?? 1.0, size: 0.0, value: 0.0, momentum: 0.0 }
    const fallbackBetas = {
      market: 1.08, // CAPM beta fallback
      size: 0.0,
      value: 0.0,
      momentum: 0.0,
    };
    const covidShocks = CRISIS_PRESETS.covid.shocks; // market: -30%, size: -15%, value: -10%, momentum: -12%

    // With fallback betas, only market shock applies: 1.08 * -30% = -32.4%
    const res = calculateFactorPerturbation(fallbackBetas, covidShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(-32.4);
    expect(res.factor_contributions_pct.market).toBe(-32.4);
    expect(res.factor_contributions_pct.size).toBe(0.0);
    expect(res.factor_contributions_pct.value).toBe(0.0);
    expect(res.factor_contributions_pct.momentum).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(-32400.0);
    expect(res.projected_capital_terminal_value).toBe(67600.0);
  });

  it("verifies default fallback when no CAPM beta exists (defaults to market = 1.0)", () => {
    const defaultFallbackBetas = {
      market: 1.0,
      size: 0.0,
      value: 0.0,
      momentum: 0.0,
    };
    const rateHikeShocks = CRISIS_PRESETS.rate_hike.shocks; // market: -15%

    const res = calculateFactorPerturbation(defaultFallbackBetas, rateHikeShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(-15.0);
    expect(res.rupee_capital_impact).toBe(-15000.0);
    expect(res.projected_capital_terminal_value).toBe(85000.0);
  });
});
