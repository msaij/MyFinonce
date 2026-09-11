import { apiGet } from "./client";

export interface NavHistoryPoint {
  [key: string]: unknown;
  scheme_code: number;
  raw_scheme_name: string;
  base_scheme_name: string;
  scheme_name: string;
  plan_type: string | null;
  option_type: string | null;
  nav_date: string;
  nav: number;
}

export const getNavHistory = (codes: number[], start?: string, end?: string) =>
  apiGet<NavHistoryPoint[]>("/api/schemes/nav-history", {
    codes: codes.join(","),
    start,
    end,
  });
