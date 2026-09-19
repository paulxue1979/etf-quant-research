import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BacktestReport, BacktestReportSeries } from "./types";

const syncHarness = vi.hoisted(() => ({
  listeners: new Set<() => void>(),
  setRange: vi.fn(),
  showFullHistory: vi.fn(),
  resetView: vi.fn(),
}));

vi.mock("./chartSync", () => ({
  ChartSyncController: class {
    register() { return () => undefined; }
    subscribeViewportChange(handler: () => void) {
      syncHarness.listeners.add(handler);
      return () => syncHarness.listeners.delete(handler);
    }
    setRange = syncHarness.setRange;
    showFullHistory = syncHarness.showFullHistory;
    resetView = syncHarness.resetView;
  },
}));

vi.mock("./FinancialChart", () => ({
  FinancialChart: ({ title, markers = [], showDollarDifference = false }: { title: string; markers?: unknown[]; showDollarDifference?: boolean }) => <section aria-label={title} data-marker-count={markers.length} data-dollar-difference={showDollarDifference}><h3>{title}</h3></section>,
}));

vi.mock("./StrategyRegimeStrip", () => ({
  StrategyRegimeStrip: ({ points }: { points: unknown[] }) => <section aria-label="Strategy Regime" data-point-count={points.length}><h3>Strategy Regime</h3></section>,
}));

import { BacktestReportCharts } from "./BacktestReportCharts";

afterEach(() => {
  cleanup();
  syncHarness.listeners.clear();
  syncHarness.setRange.mockClear();
  syncHarness.showFullHistory.mockClear();
  syncHarness.resetView.mockClear();
});

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
    contribution_report: {
      status: "available",
      schedule: { enabled: false, frequency: null, amount: null, requested_date: null, currency: "USD", requested_date_semantics: null },
      event_count: 0,
      integrity: { status: "consistent", event_amount_total: "0", external_cash_flow_total: "0", cumulative_contributions: 0 },
      events: [],
    },
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

  it("keeps contribution markers opt-in on the synchronized portfolio chart", async () => {
    const value = report();
    value.contribution_report = {
      status: "available",
      schedule: { enabled: true, frequency: "monthly", amount: "1000", requested_date: null, currency: "USD", requested_date_semantics: "month_start" },
      event_count: 1,
      integrity: { status: "consistent", event_amount_total: "1000", external_cash_flow_total: "1000", cumulative_contributions: 1000 },
      events: [{ sequence: 1, requested_date: "2025-01-01", effective_date: "2025-01-02", amount: "1000", currency: "USD", frequency: "monthly", source: "ContributionEvent", strategy_signal: false, deployment: { cause: "contribution", status: "rebalance_executed", order_count: 1, fill_count: 1, symbols: ["QQQ"] } }],
    };
    const user = userEvent.setup();
    render(<BacktestReportCharts report={value} series={reportSeries()} />);

    const chart = screen.getByLabelText("Portfolio Value vs Capital Invested");
    expect(chart).toHaveAttribute("data-marker-count", "0");
    expect(chart).toHaveAttribute("data-dollar-difference", "true");
    await user.click(screen.getByRole("checkbox", { name: "Show contributions" }));
    expect(chart).toHaveAttribute("data-marker-count", "1");
  });

  it("restores full history through MAX, Fit All, and Reset View after a custom viewport", async () => {
    const user = userEvent.setup();
    render(<BacktestReportCharts report={report()} series={reportSeries()} />);

    await user.click(screen.getByRole("button", { name: "1Y" }));
    expect(syncHarness.setRange).toHaveBeenCalledWith({
      from: "2025-01-02",
      to: "2025-01-03",
    });

    await user.click(screen.getByRole("button", { name: "MAX" }));
    expect(syncHarness.showFullHistory).toHaveBeenLastCalledWith({
      from: "2025-01-02",
      to: "2025-01-03",
    });

    act(() => {
      for (const listener of syncHarness.listeners) listener();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Custom view");
    expect(screen.getByRole("button", { name: "MAX" })).toHaveClass("button-secondary");

    await user.click(screen.getByRole("button", { name: "Fit All" }));
    expect(syncHarness.showFullHistory).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Custom view")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MAX" })).toHaveClass("button-primary");

    act(() => {
      for (const listener of syncHarness.listeners) listener();
    });
    await user.click(screen.getByRole("button", { name: "Reset View" }));
    expect(syncHarness.resetView).toHaveBeenCalledWith({
      from: "2025-01-02",
      to: "2025-01-03",
    });
    expect(screen.queryByText("Custom view")).not.toBeInTheDocument();
  });
});
