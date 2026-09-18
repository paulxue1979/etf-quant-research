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
    contribution_report: {
      status: "available",
      schedule: { enabled: false, frequency: null, amount: null, requested_date: null, currency: "USD", requested_date_semantics: null },
      event_count: 0,
      integrity: { status: "consistent", event_amount_total: "0", external_cash_flow_total: "0", cumulative_contributions: 0 },
      events: [],
    },
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

  it("creates external-cash-flow markers without calling them strategy signals", () => {
    const value = report();
    value.contribution_report = {
      status: "available",
      schedule: { enabled: true, frequency: "monthly", amount: "1000", requested_date: null, currency: "USD", requested_date_semantics: "month_start" },
      event_count: 1,
      integrity: { status: "consistent", event_amount_total: "1000", cumulative_contributions: 1000 },
      events: [{
        sequence: 1,
        requested_date: "2025-01-01",
        effective_date: "2025-01-02",
        amount: "1000",
        currency: "USD",
        frequency: "monthly",
        source: "ContributionEvent",
        strategy_signal: false,
        deployment: { cause: "contribution", status: "rebalance_executed", order_count: 1, fill_count: 1, symbols: ["QQQ"] },
      }],
    };

    const bundle = buildChartBundle(value, series());

    expect(bundle.contributionMarkers).toEqual([{
      date: "2025-01-02",
      kind: "contribution",
      label: "External Cash Flow · $1,000.00",
      color: "#d2a956",
      details: [
        "Requested date: 2025-01-01",
        "Effective date: 2025-01-02",
        "Amount: $1,000.00",
        "Schedule: monthly",
        "Strategy signal: NONE",
        "Contribution rebalance: executed",
      ],
    }]);
  });

  it("adapts canonical provenance into continuous regimes and meaningful markers", () => {
    const value = report();
    value.strategy_provenance = {
      status: "available",
      records: [
        { signal_date: "2025-01-02", matched_rule_id: "buy", allocation_source: "rule_match", target_allocation: { QQQ: 1 }, execution_date: "2025-01-03", execution_status: "submitted", omission_reason: null },
        { signal_date: "2025-01-03", matched_rule_id: null, allocation_source: "hold_previous", target_allocation: { QQQ: 1 }, execution_date: "2025-01-04", execution_status: "submitted", omission_reason: null },
        { signal_date: "2025-01-04", matched_rule_id: "sell", allocation_source: "rule_match", target_allocation: {}, execution_date: null, execution_status: "omitted", omission_reason: "no future trading day in backtest range" },
      ],
      markers: [
        { date: "2025-01-02", marker_type: "signal", signal_date: "2025-01-02", allocation_source: "rule_match", matched_rule_id: "buy", target_allocation: { QQQ: 1 }, execution_date: "2025-01-03", execution_status: "submitted" },
        { date: "2025-01-03", marker_type: "execution", signal_date: "2025-01-02", execution_date: "2025-01-03", execution_status: "filled", rebalance_cause: "target", order_count: 1, fill_count: 1 },
        { date: "2025-01-04", marker_type: "signal", signal_date: "2025-01-04", allocation_source: "rule_match", matched_rule_id: "sell", target_allocation: {}, execution_date: null, execution_status: "omitted" },
        { date: "2025-01-05", marker_type: "execution", signal_date: "2025-01-05", execution_date: "2025-01-05", execution_status: "filled", rebalance_cause: "contribution", order_count: 1, fill_count: 1 },
      ],
    };
    value.allocations = {
      target: {
        status: "available",
        timeline: [
          { date: "2025-01-02", asset_weights: { QQQ: 1 }, cash_weight: 0, allocation_source: "rule_match", matched_rule_id: "buy" },
          { date: "2025-01-03", asset_weights: { QQQ: 1 }, cash_weight: 0, allocation_source: "hold_previous", matched_rule_id: null },
          { date: "2025-01-04", asset_weights: {}, cash_weight: 1, allocation_source: "rule_match", matched_rule_id: "sell" },
        ],
        asset_symbols: ["QQQ"],
      },
      actual: { status: "available", timeline: [], asset_symbols: [] },
      cash_semantics: "cash is ledger cash; SGOV remains an asset",
    };

    const bundle = buildChartBundle(value, series());

    expect(bundle.regime.map((point) => [point.date, point.label, point.allocationLabel])).toEqual([
      ["2025-01-02", "buy", "QQQ"],
      ["2025-01-03", "Hold Previous", "QQQ"],
      ["2025-01-04", "sell", "Cash"],
    ]);
    expect(bundle.strategyMarkers.map((marker) => [marker.kind, marker.date, marker.label])).toEqual([
      ["signal", "2025-01-02", "Signal · buy"],
      ["execution", "2025-01-03", "Execution · TARGET"],
      ["signal", "2025-01-04", "Signal · sell"],
      ["execution", "2025-01-05", "Execution · CONTRIBUTION"],
    ]);
    expect(bundle.strategyMarkers.filter((marker) => marker.kind === "signal")).toHaveLength(2);
    expect(bundle.strategyMarkers.at(2)?.details).toContain("Execution: Not executed");
  });

  it("builds dynamic target and actual allocation series without treating SGOV as cash", () => {
    const value = report();
    value.allocations = {
      target: {
        status: "available",
        timeline: [{ date: "2025-01-02", asset_weights: { QQQ: 0.6, TQQQ: 0.3, SGOV: 0.1 }, cash_weight: 0, allocation_source: "rule_match", matched_rule_id: "mixed" }],
        asset_symbols: ["QQQ", "SGOV", "TQQQ"],
      },
      actual: {
        status: "available",
        timeline: [{ date: "2025-01-03", asset_weights: { QQQ: 0.58, TQQQ: 0.28, SGOV: 0.09 }, target_asset_weights: { QQQ: 0.6, TQQQ: 0.3, SGOV: 0.1 }, cash_weight: 0.05 }],
        asset_symbols: ["QQQ", "SGOV", "TQQQ"],
      },
      cash_semantics: "cash is ledger cash; SGOV remains an asset",
    };

    const bundle = buildChartBundle(value, series());

    expect(bundle.targetAllocation.map((item) => item.label)).toEqual(["QQQ", "SGOV", "TQQQ", "Cash"]);
    expect(bundle.actualAllocation.map((item) => item.label)).toEqual(["QQQ", "SGOV", "TQQQ", "Cash"]);
    expect(bundle.targetAllocation.find((item) => item.label === "SGOV")?.points).toEqual([{ date: "2025-01-02", value: 0.1 }]);
    expect(bundle.targetAllocation.find((item) => item.label === "Cash")?.points).toEqual([{ date: "2025-01-02", value: 0 }]);
    expect(bundle.actualAllocation.find((item) => item.label === "Cash")?.points).toEqual([{ date: "2025-01-03", value: 0.05 }]);
  });

  it("labels fallback and mixed multi-asset regimes without hard-coded risk states", () => {
    const value = report();
    value.strategy_provenance = {
      status: "available",
      records: [
        { signal_date: "2025-01-02", matched_rule_id: null, allocation_source: "fallback", target_allocation: { SGOV: 1 }, execution_date: "2025-01-03", execution_status: "submitted", omission_reason: null },
        { signal_date: "2025-01-03", matched_rule_id: "balanced", allocation_source: "rule_match", target_allocation: { QQQ: 0.6, TQQQ: 0.3, SGOV: 0.1 }, execution_date: "2025-01-04", execution_status: "submitted", omission_reason: null },
      ],
      markers: [],
    };
    value.allocations = {
      target: {
        status: "available",
        timeline: [
          { date: "2025-01-02", asset_weights: { SGOV: 1 }, cash_weight: 0, allocation_source: "fallback", matched_rule_id: null },
          { date: "2025-01-03", asset_weights: { QQQ: 0.6, TQQQ: 0.3, SGOV: 0.1 }, cash_weight: 0, allocation_source: "rule_match", matched_rule_id: "balanced" },
        ],
        asset_symbols: ["QQQ", "SGOV", "TQQQ"],
      },
      actual: { status: "available", timeline: [], asset_symbols: [] },
    };

    const regimes = buildChartBundle(value, series()).regime;

    expect(regimes.map((point) => [point.label, point.allocationLabel])).toEqual([
      ["Fallback", "SGOV"],
      ["balanced", "Mixed Allocation"],
    ]);
  });
});
