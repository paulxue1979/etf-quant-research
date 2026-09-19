import { describe, expect, it } from "vitest";

import { COMPARISON_METRICS, comparisonMetricReason, formatComparisonMetric, sortComparisonRuns } from "./comparisonMetrics";
import type { ComparisonRunIdentity } from "./types";

const definition = (key: string) => COMPARISON_METRICS.find((item) => item.key === key)!;

function run(id: string, cagr: number | null): ComparisonRunIdentity {
  return {
    backtest_run_id: id,
    strategy_id: `strategy-${id}`,
    strategy_version_id: `version-${id}`,
    strategy_name: `Strategy ${id}`,
    strategy_version: "v1",
    short_display_label: `Strategy ${id}`,
    start_date: "2000-01-03",
    end_date: "2025-01-03",
    asset_universe: ["QQQ"],
    metrics_status: "included",
    metrics: {
      cagr: cagr === null
        ? { value: null, status: "not_evaluable", reason: "elapsed period is too short" }
        : { value: cagr, status: "available", reason: null },
    },
  };
}

describe("comparison metric contract", () => {
  it.each([
    ["cagr", 0.12345, "12.35%"],
    ["total_twr_return", 1.253, "125.30%"],
    ["max_drawdown", -0.205, "-20.50%"],
    ["sharpe_ratio", 1.234, "1.23"],
    ["sortino_ratio", 1.876, "1.88"],
    ["calmar_ratio", 0.543, "0.54"],
    ["portfolio_turnover", 0.334, "33.40%"],
    ["xirr", 0.0987, "9.87%"],
    ["trade_count", 17, "17"],
    ["holding_period_count", 9, "9"],
  ])("formats backend %s without changing the canonical value", (key, value, expected) => {
    expect(formatComparisonMetric({ value, status: "available", reason: null }, definition(key))).toBe(expected);
  });

  it("uses the canonical average gross exposure field", () => {
    expect(formatComparisonMetric(
      { value: { average_gross_exposure: 0.7654 }, status: "available", reason: null },
      definition("exposure"),
    )).toBe("76.54%");
  });

  it("renders missing metrics as N/A and preserves the backend reason", () => {
    const metric = { value: null, status: "not_evaluable" as const, reason: "requires a positive elapsed period" };
    expect(formatComparisonMetric(metric, definition("cagr"))).toBe("N/A");
    expect(comparisonMetricReason(metric, definition("cagr"))).toBe("requires a positive elapsed period");
  });

  it("sorts available values while retaining unavailable runs at the end", () => {
    const runs = [run("low", 0.05), run("missing", null), run("high", 0.2)];
    expect(sortComparisonRuns(runs, definition("cagr"), "desc").map((item) => item.backtest_run_id)).toEqual(["high", "low", "missing"]);
    expect(sortComparisonRuns(runs, definition("cagr"), "asc").map((item) => item.backtest_run_id)).toEqual(["low", "high", "missing"]);
  });
});
