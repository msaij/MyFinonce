import { describe, it, expect } from "vitest";
import {
  ScenarioStressSimulator,
  calculateFactorPerturbation,
  CRISIS_PRESETS,
} from "../ScenarioStressSimulator";

describe("ScenarioStressSimulator - calculateFactorPerturbation", () => {
  it("computes single-factor market shock correctly", () => {
    const betas = { market: 1.25 };
    const shocks = { market: -10.0 };

    const res = calculateFactorPerturbation(betas, shocks);
    // 1.25 * -10.0 = -12.5%
    expect(res.simulated_return_delta_pct).toBe(-12.5);
    expect(res.stressed_portfolio_cagr_impact).toBe(-12.5);
    expect(res.rupee_capital_impact).toBe(-12500);
    expect(res.projected_capital_terminal_value).toBe(87500);
  });

  it("computes simultaneous multi-factor perturbation matching institutional math", () => {
    // Betas: Market: 1.15, SMB: 0.40, HML: -0.20, WML: 0.15
    // Shocks: Market: -12.0%, SMB: -4.0%, HML: 2.0%, WML: -5.0%
    const betas = { market: 1.15, size: 0.4, value: -0.2, momentum: 0.15 };
    const shocks = { market: -12.0, size: -4.0, value: 2.0, momentum: -5.0 };

    // Expected: (1.15 * -12) + (0.40 * -4) + (-0.20 * 2) + (0.15 * -5)
    // = -13.80 - 1.60 - 0.40 - 0.75 = -16.55%
    const res = calculateFactorPerturbation(betas, shocks);
    expect(res.simulated_return_delta_pct).toBe(-16.55);
    expect(res.rupee_capital_impact).toBe(-16550);
    expect(res.projected_capital_terminal_value).toBe(83450);
  });

  it("handles zero factor shocks with zero return impact (invariance)", () => {
    const betas = { market: 1.2, size: 0.35, value: 0.1, momentum: -0.05 };
    const shocks = { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 };

    const res = calculateFactorPerturbation(betas, shocks);
    expect(res.simulated_return_delta_pct).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(0.0);
    expect(res.projected_capital_terminal_value).toBe(100000);
  });

  it("supports alternative beta keys (smb, hml, wml) seamlessly", () => {
    const betas = { mkt_excess: 0.9, smb: 0.5, hml: -0.3, wml: 0.2 };
    const shocks = { market: -10, size: 2, value: 5, momentum: -1 };

    // (0.9 * -10) + (0.5 * 2) + (-0.3 * 5) + (0.2 * -1) = -9.0 + 1.0 - 1.5 - 0.2 = -9.7
    const res = calculateFactorPerturbation(betas, shocks);
    expect(res.simulated_return_delta_pct).toBe(-9.7);
  });

  it("calibrates historical crisis presets with accurate macro shock parameters", () => {
    expect(CRISIS_PRESETS.covid.shocks).toEqual({
      market: -30.0,
      size: -15.0,
      value: -10.0,
      momentum: -12.0,
    });
    expect(CRISIS_PRESETS.rate_hike.shocks).toEqual({
      market: -15.0,
      size: -8.0,
      value: 8.0,
      momentum: -10.0,
    });
    expect(CRISIS_PRESETS.volatility_2024.shocks).toEqual({
      market: -6.0,
      size: -10.0,
      value: -4.0,
      momentum: -7.0,
    });
    expect(CRISIS_PRESETS.custom.shocks).toEqual({
      market: 0.0,
      size: 0.0,
      value: 0.0,
      momentum: 0.0,
    });
  });

  it("calibrates liquid fund perturbations to near-zero under severe equity crash shocks", () => {
    // Authentic liquid fund market beta ~ 0.001 (0.1% sensitivity)
    const liquidBetas = { market: 0.001, size: 0.0, value: 0.0, momentum: 0.0 };
    const covidShocks = CRISIS_PRESETS.covid.shocks; // market: -30%

    const res = calculateFactorPerturbation(liquidBetas, covidShocks, 100000);
    // 0.001 * -30.0 = -0.03%
    expect(res.simulated_return_delta_pct).toBe(-0.03);
    expect(res.rupee_capital_impact).toBe(-30.0);
    expect(res.projected_capital_terminal_value).toBe(99970.0);
  });

  it("handles pure zero-beta fixed income fund with invariant capital under equity shocks", () => {
    const zeroBetas = { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 };
    const covidShocks = CRISIS_PRESETS.covid.shocks;

    const res = calculateFactorPerturbation(zeroBetas, covidShocks, 100000);
    expect(res.simulated_return_delta_pct).toBe(0.0);
    expect(res.rupee_capital_impact).toBe(0.0);
    expect(res.projected_capital_terminal_value).toBe(100000.0);
  });

  it("exports ScenarioStressSimulator React component function", () => {
    expect(typeof ScenarioStressSimulator).toBe("function");
  });
});
