import { describe, expect, it } from "vitest";

import type { BacktestReport, BacktestReportSeries } from "./types";
import { buildChartBundle, benchmarkLabel, rangeForPreset, toChartPoints } from "./reportCharts";

function report(): BacktestReport {
  return {
    report_schema_version: "1.0",
    identity: {
      backtest_run_id: "run-1",
      strategy_id: "strategy-1",
      strategy_version_id: "version-1",
      strategy_version_content_hash: "hash-1",
      created_at: "2026-01-01T00:00:00Z",
      engine_version: "phase-3.0",
    },
    availability: {},
    summary_period: { start_date: "2020-01-01", end_date: "2025-01-01" },
    summary: { benchmark: { benchmark_symbol: "SPY" } },
    capital: { initial_capital: 100000, cumulative_contributions: 0, total_capital_invested: 100000 },
    profit: { investment_profit: 1000 },
    performance: {},
    investor_experience: {},
    series_metadata: {},
    contributions: [],
    strategy_provenance: {},
    allocations: {},
    holdings: {},
    trades: {},
    configuration: {},
    provenance: {},
  };
}

function series(overrides: Partial<BacktestReportSeries["series"]> = {}): BacktestReportSeries {
  return {
    report_schema_version: "1.0",
    identity: { backtest_run_id: "run-1", strategy_version_id: "version-1" },
    summary_period: { start_date: "2020-01-01", end_date: "2025-01-01" },
    window: { from: null, to: null },
    series: {
      equity: { status: "available", points: [{ date: "2020-01-01", value: 100000 }, { date: "2025-01-01", value: 101000 }] },
      capital: { status: "available", points: [{ date: "2020-01-01", value: 100000 }, { date: "2025-01-01", value: 100000 }] },
      twr: { status: "available", points: [{ date: "2020-01-01", value: 1 }, { date: "2025-01-01", value: 1.01 }] },
      drawdown: { status: "available", points: [{ date: "2020-01-01", value: 0 }, { date: "2025-01-01", value: -0.02 }] },
      ...overrides,
    },
  };
}

describe("report chart projection", () => {
  it("discards invalid points and keeps the last value for duplicate dates deterministically", () => {
    expect(toChartPoints([
      { date: "2025-01-02", value: 2 },
      { date: "not-a-date", value: 3 },
      { date: "2025-02-30", value: 4 },
      { date: "2025-01-01", value: Number.NaN },
      { date: "2025-01-02", value: 5 },
      { date: "2025-01-03", value: Number.POSITIVE_INFINITY },
    ])).toEqual([{ date: "2025-01-02", value: 5 }]);
  });

  it("keeps strategy charts available when the benchmark is unavailable", () => {
    const bundle = buildChartBundle(report(), series({
      benchmark_twr: { status: "not_available", reason: "benchmark data missing" },
      benchmark_drawdown: { status: "not_available", reason: "benchmark data missing" },
    }));
    expect(bundle.performance[0].status).toBe("available");
    expect(bundle.performance[1].status).toBe("not_available");
    expect(bundle.drawdown[0].status).toBe("available");
    expect(bundle.drawdown[1].status).toBe("not_available");
    expect(benchmarkLabel(report())).toBe("SPY Buy & Hold");
  });

  it("degrades missing legacy series without producing invalid points", () => {
    const bundle = buildChartBundle(report(), series({ twr: undefined, drawdown: undefined }));
    expect(bundle.performance[0].status).toBe("empty");
    expect(bundle.drawdown[0].status).toBe("empty");
    expect(bundle.performance[0].points).toEqual([]);
  });

  it("anchors presets to the last returned date instead of the current date", () => {
    const bundle = buildChartBundle(report(), series());
    expect(bundle.anchorDate).toBe("2025-01-01");
    expect(rangeForPreset(bundle, "1Y")).toEqual({ from: "2024-01-01", to: "2025-01-01" });
    expect(rangeForPreset(bundle, "MAX")).toEqual({ from: "2020-01-01", to: "2025-01-01" });
  });
});
