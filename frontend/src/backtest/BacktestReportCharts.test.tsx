import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BacktestReport, BacktestReportSeries } from "./types";

vi.mock("./FinancialChart", () => ({
  FinancialChart: ({ title, markers = [] }: { title: string; markers?: unknown[] }) => <section aria-label={title} data-marker-count={markers.length}><h3>{title}</h3></section>,
}));

vi.mock("./StrategyRegimeStrip", () => ({
  StrategyRegimeStrip: ({ points }: { points: unknown[] }) => <section aria-label="Strategy Regime" data-point-count={points.length}><h3>Strategy Regime</h3></section>,
}));

import { BacktestReportCharts } from "./BacktestReportCharts";

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
    summary_period: { start_date: "2025-01-02", end_date: "2025-01-03" },
    summary: {},
    capital: { initial_capital: 100000, cumulative_contributions: 0, total_capital_invested: 100000 },
    profit: { investment_profit: 1000 },
    performance: {},
    investor_experience: {},
    series_metadata: {},
    contributions: [],
    strategy_provenance: {
      status: "available",
      records: [{ signal_date: "2025-01-02", matched_rule_id: "risk-on", allocation_source: "rule_match", target_allocation: { QQQ: 1 }, execution_date: "2025-01-03", execution_status: "submitted", omission_reason: null }],
      markers: [{ date: "2025-01-02", marker_type: "signal", signal_date: "2025-01-02", matched_rule_id: "risk-on", allocation_source: "rule_match", target_allocation: { QQQ: 1 }, execution_date: "2025-01-03", execution_status: "submitted" }],
    },
    allocations: {
      target: { status: "available", timeline: [{ date: "2025-01-02", asset_weights: { QQQ: 1 }, cash_weight: 0 }], asset_symbols: ["QQQ"] },
      actual: { status: "available", timeline: [{ date: "2025-01-03", asset_weights: { QQQ: 0.99 }, cash_weight: 0.01 }], asset_symbols: ["QQQ"] },
      cash_semantics: "cash is separate from SGOV",
    },
    holdings: {},
    trades: {},
    configuration: {},
    provenance: {},
  };
}

function reportSeries(): BacktestReportSeries {
  const points = [{ date: "2025-01-02", value: 1 }, { date: "2025-01-03", value: 1.01 }];
  return {
    report_schema_version: "1.0",
    identity: { backtest_run_id: "run-1", strategy_version_id: "version-1" },
    summary_period: { start_date: "2025-01-02", end_date: "2025-01-03" },
    window: { from: null, to: null },
    series: {
      equity: { status: "available", points },
      capital: { status: "available", points },
      twr: { status: "available", points },
      drawdown: { status: "available", points },
    },
  };
}

describe("BacktestReportCharts", () => {
  it("places decision, intent, and portfolio reality in the shared chart stack", () => {
    render(<BacktestReportCharts report={report()} series={reportSeries()} />);

    expect(screen.getByRole("heading", { name: "Strategy Regime" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Target Allocation" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Actual Allocation" })).toBeInTheDocument();
    expect(screen.getByLabelText("Strategy Regime")).toHaveAttribute("data-point-count", "1");
    expect(screen.getByLabelText("Target Allocation")).toHaveAttribute("data-marker-count", "1");
    expect(screen.getByRole("button", { name: "MAX" })).toBeInTheDocument();
  });

  it("keeps loading and error states explicit", () => {
    const { rerender } = render(<BacktestReportCharts report={null} series={null} loading />);
    expect(screen.getByText("Loading report charts...")).toBeInTheDocument();

    rerender(<BacktestReportCharts report={null} series={null} error="report unavailable" />);
    expect(screen.getByText("report unavailable")).toBeInTheDocument();
  });
});
