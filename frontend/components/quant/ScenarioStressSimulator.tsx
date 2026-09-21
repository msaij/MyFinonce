"use client";

import React, { useState, useEffect, useMemo } from "react";
import { formatInr, formatSignedPct } from "@/lib/format";
import type { ScenarioReplayResult } from "@/lib/api/quant";

export interface ScenarioStressSimulatorProps {
  schemeCode?: number;
  schemeName?: string;
  category?: string;
  factorBetas?: {
    market?: number;
    size?: number;
    value?: number;
    momentum?: number;
    [key: string]: number | undefined;
  };
  baselineMaxDrawdown?: number;
  historicalScenarios?: ScenarioReplayResult[] | Record<string, ScenarioReplayResult>;
  initialPreset?: string;
  initialShocks?: {
    market?: number;
    size?: number;
    value?: number;
    momentum?: number;
  };
  onShocksChange?: (
    shocks: { market: number; size: number; value: number; momentum: number },
    preset: string
  ) => void;
  className?: string;
}

export interface FactorPerturbationResult {
  simulated_return_delta_pct: number;
  stressed_portfolio_cagr_impact: number;
  factor_contributions_pct: Record<string, number>;
  rupee_capital_impact: number;
  projected_capital_terminal_value: number;
  base_capital: number;
}

export const CRISIS_PRESETS = {
  covid: {
    id: "covid",
    label: "March 2020 COVID Crash",
    description: "Global liquidity crunch & rapid -30% equity contraction",
    shocks: { market: -30.0, size: -15.0, value: -10.0, momentum: -12.0 },
  },
  rate_hike: {
    id: "rate_hike",
    label: "2022 Rate Hikes & Inflation",
    description: "Central bank tightening, value outperforming high-duration growth",
    shocks: { market: -15.0, size: -8.0, value: 8.0, momentum: -10.0 },
  },
  volatility_2024: {
    id: "volatility_2024",
    label: "2024 Volatility Spikes",
    description: "Election event volatility & sudden mid-small cap rotation",
    shocks: { market: -6.0, size: -10.0, value: -4.0, momentum: -7.0 },
  },
  custom: {
    id: "custom",
    label: "Custom Scenario",
    description: "Custom factor shocks defined by analyst sliders",
    shocks: { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 },
  },
} as const;

export type PresetKey = keyof typeof CRISIS_PRESETS;

export function calculateFactorPerturbation(
  factorBetas: Record<string, number | undefined> = {},
  sliderShocksPct: Record<string, number> = {},
  baseCapital = 100000
): FactorPerturbationResult {
  let deltaR = 0;
  const factorContributions: Record<string, number> = {};

  const factorKeyMap: Record<string, string[]> = {
    market: ["market", "mkt", "mkt_excess"],
    size: ["size", "smb"],
    value: ["value", "hml"],
    momentum: ["momentum", "wml"],
  };

  for (const [canonical, aliases] of Object.entries(factorKeyMap)) {
    let beta = 0;
    for (const k of aliases) {
      if (factorBetas[k] !== undefined && factorBetas[k] !== null) {
        beta = factorBetas[k]!;
        break;
      }
    }
    const shock = sliderShocksPct[canonical] ?? sliderShocksPct[aliases[1]] ?? 0;
    const contrib = beta * shock;
    factorContributions[canonical] = Number(contrib.toFixed(4));
    deltaR += contrib;
  }

  const roundedDeltaR = Number(deltaR.toFixed(4));
  const rupeeImpact = Number((baseCapital * (roundedDeltaR / 100)).toFixed(2));
  const terminalVal = Number((baseCapital + rupeeImpact).toFixed(2));

  return {
    simulated_return_delta_pct: roundedDeltaR,
    stressed_portfolio_cagr_impact: roundedDeltaR,
    factor_contributions_pct: factorContributions,
    rupee_capital_impact: rupeeImpact,
    projected_capital_terminal_value: terminalVal,
    base_capital: baseCapital,
  };
}

export function ScenarioStressSimulator({
  schemeCode,
  schemeName = "Mutual Fund Scheme",
  category,
  factorBetas,
  historicalScenarios,
  initialPreset = "custom",
  initialShocks,
  onShocksChange,
  className,
}: ScenarioStressSimulatorProps) {
  const [selectedPreset, setSelectedPreset] = useState<string>(initialPreset);
  const [shocks, setShocks] = useState({
    market: initialShocks?.market ?? 0,
    size: initialShocks?.size ?? 0,
    value: initialShocks?.value ?? 0,
    momentum: initialShocks?.momentum ?? 0,
  });

  const isDebtOrLiquid = useMemo(() => {
    if (!category) return false;
    const c = category.toLowerCase();
    return (
      c.includes("liquid") ||
      c.includes("debt") ||
      c.includes("overnight") ||
      c.includes("money market") ||
      c.includes("gilt") ||
      c.includes("arbitrage") ||
      c.includes("floater") ||
      c.includes("treasury")
    );
  }, [category]);

  const effectiveFactorBetas = useMemo(() => {
    if (factorBetas && Object.keys(factorBetas).length > 0) {
      // If factorBetas has an uncalibrated default equity beta (market=1.0, others=0) for a debt/liquid fund,
      // override market beta to 0.0 so fixed income funds are not falsely subjected to equity crashes.
      if (
        isDebtOrLiquid &&
        factorBetas.market === 1.0 &&
        (factorBetas.size === 0 || factorBetas.size === undefined) &&
        (factorBetas.value === 0 || factorBetas.value === undefined) &&
        (factorBetas.momentum === 0 || factorBetas.momentum === undefined)
      ) {
        return { market: 0.0, size: 0.0, value: 0.0, momentum: 0.0 };
      }
      return factorBetas;
    }
    return {
      market: isDebtOrLiquid ? 0.0 : 1.0,
      size: 0.0,
      value: 0.0,
      momentum: 0.0,
    };
  }, [factorBetas, isDebtOrLiquid]);

  const scenarioList = useMemo<ScenarioReplayResult[]>(() => {
    if (!historicalScenarios) return [];
    if (Array.isArray(historicalScenarios)) return historicalScenarios;
    if (Array.isArray((historicalScenarios as unknown as { scenarios?: ScenarioReplayResult[] }).scenarios)) {
      return (historicalScenarios as unknown as { scenarios: ScenarioReplayResult[] }).scenarios;
    }
    return Object.values(historicalScenarios);
  }, [historicalScenarios]);

  // Keep state synchronized if initialShocks change from URL on initial load
  useEffect(() => {
    if (initialShocks) {
      setShocks((prev) => ({
        market: initialShocks.market ?? prev.market,
        size: initialShocks.size ?? prev.size,
        value: initialShocks.value ?? prev.value,
        momentum: initialShocks.momentum ?? prev.momentum,
      }));
    }
    if (initialPreset) {
      setSelectedPreset(initialPreset);
    }
  }, [initialPreset, initialShocks?.market, initialShocks?.size, initialShocks?.value, initialShocks?.momentum]);

  const handlePresetSelect = (presetKey: string) => {
    setSelectedPreset(presetKey);
    const presetConfig = CRISIS_PRESETS[presetKey as PresetKey];
    if (presetConfig && presetKey !== "custom") {
      const nextShocks = { ...presetConfig.shocks };
      setShocks(nextShocks);
      onShocksChange?.(nextShocks, presetKey);
    } else {
      onShocksChange?.(shocks, "custom");
    }
  };

  const handleSliderChange = (factor: keyof typeof shocks, value: number) => {
    const nextShocks = { ...shocks, [factor]: value };
    setShocks(nextShocks);
    setSelectedPreset("custom");
    onShocksChange?.(nextShocks, "custom");
  };

  const handleReset = () => {
    const zeroShocks = { market: 0, size: 0, value: 0, momentum: 0 };
    setShocks(zeroShocks);
    setSelectedPreset("custom");
    onShocksChange?.(zeroShocks, "custom");
  };

  const simResult = useMemo(
    () => calculateFactorPerturbation(effectiveFactorBetas, shocks, 100000),
    [effectiveFactorBetas, shocks]
  );

  return (
    <div className={`space-y-6 ${className ?? ""}`}>
      {/* Header & Presets */}
      <div className="rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h3 className="text-base font-bold" style={{ color: "var(--mf-fg)" }}>
              Macroeconomic Scenario Stress Simulator
            </h3>
            <p className="text-xs mt-0.5" style={{ color: "var(--mf-muted)" }}>
              Simulate parametric factor shocks and replay historical crises for <b>{schemeName}</b>.
            </p>
          </div>
          <button
            type="button"
            onClick={handleReset}
            className="self-start sm:self-auto rounded-lg px-3 py-1.5 text-xs font-semibold border transition-colors hover:bg-slate-100 dark:hover:bg-slate-800"
            style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}
          >
            Reset Sliders
          </button>
        </div>

        {/* Crisis Presets Buttons */}
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
          {Object.entries(CRISIS_PRESETS).map(([key, config]) => {
            const isSelected = selectedPreset === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => handlePresetSelect(key)}
                className={`flex flex-col items-start rounded-lg border p-3 text-left transition-all ${
                  isSelected ? "ring-2 ring-blue-500 shadow-sm" : "hover:border-slate-400"
                }`}
                style={{
                  background: isSelected ? "var(--mf-accent-bg)" : "rgba(148, 163, 184, 0.05)",
                  borderColor: isSelected ? "var(--mf-accent)" : "var(--mf-border)",
                }}
              >
                <div className="flex items-center justify-between w-full">
                  <span className="text-xs font-bold" style={{ color: isSelected ? "var(--mf-accent)" : "var(--mf-fg)" }}>
                    {config.label}
                  </span>
                  {isSelected && <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400">ACTIVE</span>}
                </div>
                <span className="mt-1 text-[11px] leading-tight line-clamp-2" style={{ color: "var(--mf-muted)" }}>
                  {config.description}
                </span>
              </button>
            );
          })}
        </div>

        {/* Liquid / Debt Fund Guidance Note */}
        {isDebtOrLiquid && (
          <div className="mt-4 rounded-xl p-3.5 text-xs border flex items-start gap-2.5" style={{ background: "rgba(59, 130, 246, 0.06)", borderColor: "rgba(59, 130, 246, 0.25)" }}>
            <span className="text-base leading-none">🛡️</span>
            <div>
              <span className="font-bold block" style={{ color: "var(--mf-fg)" }}>
                Fixed Income / Liquid Fund Mandate
              </span>
              <p className="mt-0.5" style={{ color: "var(--mf-muted)" }}>
                This fund allocates to money market instruments (≤91-day maturity) insulated from equity market volatility. Systematic equity market beta is calibrated to <b className="font-mono">{effectiveFactorBetas.market?.toFixed(3) ?? "0.000"}</b>. In empirical AMFI history, liquid fund daily NAV accrual remained non-negative throughout the March 2020 crash (0.00% drawdown).
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Main Grid: Sliders on left, Instant Output on right */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Factor Shock Sliders (7 cols) */}
        <div className="lg:col-span-7 rounded-xl border p-5 shadow-sm space-y-5" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
          <h4 className="text-sm font-bold border-b pb-2" style={{ color: "var(--mf-fg)", borderColor: "var(--mf-border)" }}>
            Parametric Factor Perturbations (Client-Side Instant Recalculation)
          </h4>

          {/* Market Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>
                Market Shock (Nifty 50)
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-medium" style={{ color: "var(--mf-muted)" }}>
                  Beta: {effectiveFactorBetas.market !== undefined ? effectiveFactorBetas.market.toFixed(3) : "1.000"}
                </span>
                <span className={`font-mono font-bold px-1.5 py-0.5 rounded text-xs ${shocks.market < 0 ? "bg-red-500/10 text-red-600 dark:text-red-400" : shocks.market > 0 ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-slate-500/10 text-slate-600 dark:text-slate-400"}`}>
                  {formatSignedPct(shocks.market)}
                </span>
              </div>
            </div>
            <input
              type="range"
              min="-30"
              max="30"
              step="0.5"
              value={shocks.market}
              onChange={(e) => handleSliderChange("market", parseFloat(e.target.value))}
              className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex justify-between text-[10px]" style={{ color: "var(--mf-muted)" }}>
              <span>-30% (Severe Crash)</span>
              <span>0% (Neutral)</span>
              <span>+30% (Bull Surge)</span>
            </div>
          </div>

          {/* Size Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>
                Size Spread (Small/Mid vs Large Spread)
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-medium" style={{ color: "var(--mf-muted)" }}>
                  Beta: {effectiveFactorBetas.size !== undefined ? effectiveFactorBetas.size.toFixed(3) : "0.000"}
                </span>
                <span className={`font-mono font-bold px-1.5 py-0.5 rounded text-xs ${shocks.size < 0 ? "bg-red-500/10 text-red-600 dark:text-red-400" : shocks.size > 0 ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-slate-500/10 text-slate-600 dark:text-slate-400"}`}>
                  {formatSignedPct(shocks.size)}
                </span>
              </div>
            </div>
            <input
              type="range"
              min="-20"
              max="20"
              step="0.5"
              value={shocks.size}
              onChange={(e) => handleSliderChange("size", parseFloat(e.target.value))}
              className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex justify-between text-[10px]" style={{ color: "var(--mf-muted)" }}>
              <span>-20% (SmallCap Collapse)</span>
              <span>0%</span>
              <span>+20% (MidCap Outperformance)</span>
            </div>
          </div>

          {/* Value Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>
                Value Spread (High Book-to-Market vs Growth)
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-medium" style={{ color: "var(--mf-muted)" }}>
                  Beta: {effectiveFactorBetas.value !== undefined ? effectiveFactorBetas.value.toFixed(3) : "0.000"}
                </span>
                <span className={`font-mono font-bold px-1.5 py-0.5 rounded text-xs ${shocks.value < 0 ? "bg-red-500/10 text-red-600 dark:text-red-400" : shocks.value > 0 ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-slate-500/10 text-slate-600 dark:text-slate-400"}`}>
                  {formatSignedPct(shocks.value)}
                </span>
              </div>
            </div>
            <input
              type="range"
              min="-20"
              max="20"
              step="0.5"
              value={shocks.value}
              onChange={(e) => handleSliderChange("value", parseFloat(e.target.value))}
              className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex justify-between text-[10px]" style={{ color: "var(--mf-muted)" }}>
              <span>-20% (Growth Rallies)</span>
              <span>0%</span>
              <span>+20% (Value Rotation)</span>
            </div>
          </div>

          {/* Momentum Slider */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>
                Momentum Spread (Winners vs Losers)
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-medium" style={{ color: "var(--mf-muted)" }}>
                  Beta: {effectiveFactorBetas.momentum !== undefined ? effectiveFactorBetas.momentum.toFixed(3) : "0.000"}
                </span>
                <span className={`font-mono font-bold px-1.5 py-0.5 rounded text-xs ${shocks.momentum < 0 ? "bg-red-500/10 text-red-600 dark:text-red-400" : shocks.momentum > 0 ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-slate-500/10 text-slate-600 dark:text-slate-400"}`}>
                  {formatSignedPct(shocks.momentum)}
                </span>
              </div>
            </div>
            <input
              type="range"
              min="-20"
              max="20"
              step="0.5"
              value={shocks.momentum}
              onChange={(e) => handleSliderChange("momentum", parseFloat(e.target.value))}
              className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex justify-between text-[10px]" style={{ color: "var(--mf-muted)" }}>
              <span>-20% (Momentum Reversal)</span>
              <span>0%</span>
              <span>+20% (Trend Continuation)</span>
            </div>
          </div>
        </div>

        {/* Output Stat Cards (5 cols) */}
        <div className="lg:col-span-5 rounded-xl border p-5 shadow-sm flex flex-col justify-between" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
          <div>
            <h4 className="text-sm font-bold border-b pb-2" style={{ color: "var(--mf-fg)", borderColor: "var(--mf-border)" }}>
              Projected Capital & Return Impact
            </h4>

            {/* Big Stat Box */}
            <div className="mt-4 rounded-xl p-4 border text-center" style={{ background: "rgba(148, 163, 184, 0.05)", borderColor: "var(--mf-border)" }}>
              <span className="text-xs uppercase tracking-wider font-semibold" style={{ color: "var(--mf-muted)" }}>
                Simulated Net Return Delta (ΔR)
              </span>
              <div className={`mt-1 text-3xl font-extrabold font-mono ${simResult.simulated_return_delta_pct < 0 ? "text-red-600 dark:text-red-400" : simResult.simulated_return_delta_pct > 0 ? "text-emerald-600 dark:text-emerald-400" : ""}`} style={{ color: simResult.simulated_return_delta_pct === 0 ? "var(--mf-fg)" : undefined }}>
                {formatSignedPct(simResult.simulated_return_delta_pct)}
              </div>
              <p className="mt-1 text-[11px]" style={{ color: "var(--mf-muted)" }}>
                ΔR = ∑(β_k × ΔF_k)
              </p>
            </div>

            {/* Rupee Wealth Scaling Box */}
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg p-3 border" style={{ background: "rgba(148, 163, 184, 0.04)", borderColor: "var(--mf-border)" }}>
                <span className="text-[11px] block font-medium" style={{ color: "var(--mf-muted)" }}>
                  Initial Capital
                </span>
                <span className="text-sm font-bold font-mono" style={{ color: "var(--mf-fg)" }}>
                  {formatInr(100000)}
                </span>
              </div>
              <div className="rounded-lg p-3 border" style={{ background: "rgba(148, 163, 184, 0.04)", borderColor: "var(--mf-border)" }}>
                <span className="text-[11px] block font-medium" style={{ color: "var(--mf-muted)" }}>
                  Rupee Impact
                </span>
                <span className={`text-sm font-bold font-mono ${simResult.rupee_capital_impact < 0 ? "text-red-600 dark:text-red-400" : simResult.rupee_capital_impact > 0 ? "text-emerald-600 dark:text-emerald-400" : ""}`} style={{ color: simResult.rupee_capital_impact === 0 ? "var(--mf-fg)" : undefined }}>
                  {simResult.rupee_capital_impact >= 0 ? "+" : ""}{formatInr(simResult.rupee_capital_impact)}
                </span>
              </div>
            </div>

            <div className="mt-3 rounded-lg p-3 border" style={{ background: "rgba(148, 163, 184, 0.04)", borderColor: "var(--mf-border)" }}>
              <span className="text-[11px] block font-medium" style={{ color: "var(--mf-muted)" }}>
                Projected Terminal Value
              </span>
              <span className="text-base font-bold font-mono" style={{ color: "var(--mf-fg)" }}>
                {formatInr(simResult.projected_capital_terminal_value)}
              </span>
            </div>

            {/* Factor Driver Breakdown list */}
            <div className="mt-4 space-y-1.5 text-xs">
              <span className="text-[11px] font-semibold block uppercase" style={{ color: "var(--mf-muted)" }}>
                Factor Contributions to Return Delta
              </span>
              {Object.entries(simResult.factor_contributions_pct).map(([k, val]) => (
                <div key={k} className="flex justify-between items-center py-1 border-b last:border-b-0" style={{ borderColor: "var(--mf-border)" }}>
                  <span className="capitalize" style={{ color: "var(--mf-fg)" }}>
                    {k} Contribution
                  </span>
                  <span className={`font-mono font-semibold ${val < 0 ? "text-red-600 dark:text-red-400" : val > 0 ? "text-emerald-600 dark:text-emerald-400" : ""}`} style={{ color: val === 0 ? "var(--mf-muted)" : undefined }}>
                    {formatSignedPct(val)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Historical Crisis Replay Comparison Table */}
      {scenarioList.length > 0 && (
        <div className="rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 mb-3 border-b gap-2" style={{ borderColor: "var(--mf-border)" }}>
            <div>
              <h4 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>
                Empirical Historical Crisis Replay (Actual AMFI NAV Drawdowns)
              </h4>
              <p className="text-xs mt-0.5" style={{ color: "var(--mf-muted)" }}>
                Verified peak-to-trough drawdowns computed from authentic daily AMFI NAV history vs Nifty 50.
              </p>
            </div>
            {isDebtOrLiquid && (
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800 self-start sm:self-auto">
                Continuous Daily Accrual (Immune)
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left border-collapse">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                  <th className="py-2 px-3 font-semibold">Crisis Window</th>
                  <th className="py-2 px-3 font-semibold">Historical Dates</th>
                  <th className="py-2 px-3 font-semibold text-right">Fund Max DD %</th>
                  <th className="py-2 px-3 font-semibold text-right">Benchmark Max DD %</th>
                  <th className="py-2 px-3 font-semibold text-right">Downside Alpha %</th>
                  <th className="py-2 px-3 font-semibold text-right">Recovery Days</th>
                </tr>
              </thead>
              <tbody>
                {scenarioList.map((s, idx) => {
                  const scenarioTitle = s.scenario_name || s.name || s.scenario || s.scenario_id || s.id || `Scenario ${idx + 1}`;
                  const winStart = s.window_start || s.window?.start || "N/A";
                  const winEnd = s.window_end || s.window?.end || "N/A";
                  const isZeroDrawdown = Math.abs(s.max_drawdown_pct) < 1e-4;
                  const isOutperforming = s.excess_drawdown_pct >= 0;
                  const isRecovered = s.is_recovered ?? s.recovered ?? isZeroDrawdown;

                  return (
                    <tr key={s.id || s.scenario_id || idx} className="border-b last:border-b-0 hover:bg-slate-500/5 transition-colors" style={{ borderColor: "var(--mf-border)" }}>
                      <td className="py-2.5 px-3 font-semibold" style={{ color: "var(--mf-fg)" }}>
                        {scenarioTitle}
                      </td>
                      <td className="py-2.5 px-3 font-mono" style={{ color: "var(--mf-muted)" }}>
                        {winStart} to {winEnd}
                      </td>
                      <td className={`py-2.5 px-3 text-right font-mono font-semibold ${isZeroDrawdown ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                        {formatSignedPct(s.max_drawdown_pct)}
                        {isZeroDrawdown && <span className="ml-1 text-[10px] font-normal text-emerald-600 dark:text-emerald-400">(Zero DD)</span>}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono font-semibold" style={{ color: "var(--mf-muted)" }}>
                        {formatSignedPct(s.benchmark_drawdown_pct)}
                      </td>
                      <td className={`py-2.5 px-3 text-right font-mono font-semibold ${isOutperforming ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                        {formatSignedPct(s.excess_drawdown_pct)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono" style={{ color: "var(--mf-fg)" }}>
                        {isZeroDrawdown
                          ? "0 days (Continuous Accrual)"
                          : isRecovered
                          ? `${s.recovery_days ?? 0} days`
                          : `> ${s.recovery_days ?? 0} days (Unrecovered)`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
