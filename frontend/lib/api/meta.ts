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
