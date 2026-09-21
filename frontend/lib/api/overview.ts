import { apiGet } from "./client";

export interface NamedReturn {
  name: string;
  return_pct: number | null;
}

export interface AssetDistRow {
  [key: string]: unknown;
  broad_category: string;
  count: number;
  avg_1d: number | null;
  avg_7d: number | null;
  avg_30d: number | null;
  avg_90d: number | null;
  avg_1y: number | null;
  med_30d: number | null;
  med_90d: number | null;
}

export interface TopAmcRow {
  [key: string]: unknown;
  fund_house: string;
  schemes_count: number;
  avg_30d: number | null;
  avg_90d: number | null;
  avg_1y: number | null;
}

export interface OverviewStats {
  total_schemes: number;
  total_amcs: number;
  total_nav_records: number;
  min_date: string | null;
  max_date: string | null;
  asset_dist: AssetDistRow[];
  top_amcs: TopAmcRow[];
  best_cat: NamedReturn | null;
}

export interface OverviewKpis {
  total_schemes: number;
  latest_date: string | null;
  top_performer: NamedReturn | null;
  lag_performer: NamedReturn | null;
  advancers: number;
  decliners: number;
  unchanged: number;
  median_return: number;
  avg_return: number;
  best_cat: NamedReturn | null;
}

export interface MacroTrendPoint {
  nav_date: string;
  "Asset Class": string;
  "Indexed Performance": number;
}

export interface CategoryMatrixRow {
  [key: string]: unknown;
  "Asset Class": string;
  Category: string;
  Schemes: number;
  "Avg TER %": number | null;
  "Avg 1D %": number | null;
  "Avg 7D %": number | null;
  "Avg 30D %": number | null;
  "Avg 90D %": number | null;
  "Avg 1Y %": number | null;
  "52W High Gap %": number | null;
  "Top Fund (30D) %": number | null;
  "Bottom Fund (30D) %": number | null;
}

export const getOverviewStats = () => apiGet<OverviewStats>("/api/overview/stats");

export const getOverviewKpis = (planType: string, optionType: string = "All Options", startDate?: string, endDate?: string) =>
  apiGet<OverviewKpis>("/api/overview/kpis", {
    plan_type: planType,
    option_type: optionType,
    start_date: startDate,
    end_date: endDate,
  });

export const getMacroTrend = (startDate: string, endDate: string, planType: string, optionType: string = "All Options") =>
  apiGet<MacroTrendPoint[]>("/api/overview/macro-trend", {
    start_date: startDate,
    end_date: endDate,
    plan_type: planType,
    option_type: optionType,
  });

export const getCategoryMatrix = (broadCategory: string) =>
  apiGet<CategoryMatrixRow[]>("/api/overview/category-matrix", { broad_category: broadCategory });
