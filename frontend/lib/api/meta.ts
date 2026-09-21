import { apiGet } from "./client";

export interface MetaStatus {
  data_version: number;
  schemes_count: number;
  amc_count: number;
  nav_count: number;
  file_size_mb: number;
  min_date: string | null;
  max_date: string | null;
  db_path: string;
  is_stale: boolean;
  expected_date: string | null;
  ter_records_count?: number;
  ter_official_schemes?: number;
  flags?: ProductFlags;
}

export interface ProductFlags {
  holdout_portfolios: boolean;
  bl_ui: boolean;
  admin_auth_required: boolean;
  admin_token_configured: boolean;
  amfi_ssl_insecure: boolean;
}

export interface DataQuality {
  schemes_count: number;
  nav_count: number;
  ter_official_schemes: number;
  ter_unknown_schemes: number;
  ter_legacy_schemes: number;
  ter_official_coverage_ratio: number;
  nav_gap_rate: number | null;
  nav_max_date_lag_days?: number | null;
  factor_market_last_nav: string | null;
  factor_momentum_last_nav: string | null;
  summary_table_built_at: string | null;
  ter_official_by_amc: { fund_house: string; official: number; total: number; coverage: number }[];
  ter_official_by_broad_category: { broad_category: string; official: number; total: number; coverage: number }[];
  factor_proxy_codes: Record<string, number>;
}

export interface MetaFilters {
  amcs: string[];
  broad_categories: string[];
  sub_categories: string[];
  options: string[];
  plans: string[];
}

export const getMetaStatus = () => apiGet<MetaStatus>("/api/meta/status");
export const getMetaFilters = (broadCategory?: string) =>
  apiGet<MetaFilters>("/api/meta/filters", { broad_category: broadCategory });
export const getDataQuality = () => apiGet<DataQuality>("/api/meta/data-quality");
