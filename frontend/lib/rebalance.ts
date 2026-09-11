/** Mirrors backend/app/portfolio_sim.py's REBALANCE_NONE/MONTHLY/QUARTERLY/ANNUALLY
 * string constants exactly -- these are sent as-is in the backtest request body. */
export const portfolio_sim_REBALANCE = ["None", "Monthly", "Quarterly", "Annually"] as const;
