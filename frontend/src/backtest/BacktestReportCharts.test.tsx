import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BacktestMarkerReport, BacktestMarkerType, BacktestReport, BacktestReportSeries } from "./types";

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

function markerReport(): BacktestMarkerReport {
  const byType = {} as Record<BacktestMarkerType, number>;
  return {
    marker_schema_version: "1.0",
    identity: { backtest_run_id: "run-1", strategy_version_id: "version-1" },
    source: "derived_from_immutable_backtest_run",
    persisted: false,
    filters: { types: [], from: null, to: null, group_same_day: true, major_only: false, major_threshold: null },
    counts: { markers: 2, groups: 2, by_type: byType, hidden: 0, truncated: false },
    markers: [
      {
        marker_id: "marker-execution",
        marker_type: "EXECUTION",
        event_date: "2025-01-03",
        display_date: "2025-01-03",
        strategy_version_id: "version-1",
        run_id: "run-1",
        title: "Execution",
        short_label: "E",
        summary: "1 execution",
        significance: { always_major: false, metric_name: "notional", metric_value: 0.5, is_major: false },
        source_event_type: "BacktestResult.fills",
        source_event_reference: "fill-1",
        details: { execution_date: "2025-01-03", related_signal_date: "2025-01-02", rebalance_cause: "target", fills: [{ side: "BUY", symbol: "QQQ", quantity: 1, fill_price: 100, commission: 0 }] },
        group_key: "run-1:2025-01-03",
      },
      {
        marker_id: "marker-contribution",
        marker_type: "CONTRIBUTION",
        event_date: "2025-01-02",
        display_date: "2025-01-02",
        strategy_version_id: "version-1",
        run_id: "run-1",
        title: "External Contribution",
        short_label: "C",
        summary: "USD 1000",
        significance: { always_major: true, metric_name: null, metric_value: null, is_major: true },
        source_event_type: "BacktestResult.contribution_events",
        source_event_reference: "contribution-1",
        details: { requested_date: "2025-01-01", effective_date: "2025-01-02", amount: "1000" },
        group_key: "run-1:2025-01-02",
      },
    ],
    groups: [],
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

  it("keeps contribution markers opt-in on synchronized Portfolio Value and TWR charts", async () => {
    const user = userEvent.setup();
    render(<BacktestReportCharts report={report()} series={reportSeries()} markerReport={markerReport()} />);

    const chart = screen.getByLabelText("Portfolio Value vs Capital Invested");
    const twr = screen.getByLabelText("Strategy Performance (TWR)");
    expect(chart).toHaveAttribute("data-marker-count", "1");
    expect(twr).toHaveAttribute("data-marker-count", "1");
    expect(chart).toHaveAttribute("data-dollar-difference", "true");
    await user.click(screen.getByRole("checkbox", { name: "Contributions" }));
    expect(chart).toHaveAttribute("data-marker-count", "2");
    expect(twr).toHaveAttribute("data-marker-count", "2");
    expect(screen.getByText("2 events shown")).toBeInTheDocument();
  });

  it("supports marker filters, deterministic major mode, and explicit marker errors", async () => {
    const user = userEvent.setup();
    render(<BacktestReportCharts report={report()} series={reportSeries()} markerReport={markerReport()} markerError="marker service unavailable" />);

    expect(screen.getByRole("alert")).toHaveTextContent("marker service unavailable");
    await user.click(screen.getByRole("checkbox", { name: "Major only" }));
    expect(screen.getByText("1 events shown · 1 hidden by filters")).toBeInTheDocument();
    await user.clear(screen.getByRole("spinbutton", { name: "Major marker threshold" }));
    await user.type(screen.getByRole("spinbutton", { name: "Major marker threshold" }), "0.6");
    expect(screen.getByText("0 events shown · 2 hidden by filters")).toBeInTheDocument();
  });

  it("notifies the parent about marker filters outside the child state updater", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Parent() {
      const [types, setTypes] = useState<BacktestMarkerType[]>([]);
      return <><output data-testid="selected-marker-types">{types.join(",")}</output><BacktestReportCharts report={report()} series={reportSeries()} markerReport={markerReport()} onMarkerTypesChange={setTypes} /></>;
    }

    render(<Parent />);
    await user.click(screen.getByRole("checkbox", { name: "Signals" }));

    expect(screen.getByTestId("selected-marker-types")).toHaveTextContent("SIGNAL");
    expect(consoleError.mock.calls.flat().join(" ")).not.toContain("Cannot update a component");
    consoleError.mockRestore();
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
    expect(screen.getByText("Custom view")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MAX" })).toHaveClass("button-secondary");

    await user.click(screen.getByRole("button", { name: "Fit All" }));
    expect(syncHarness.showFullHistory).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Custom view")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MAX" })).toHaveClass("button-primary");

    act(() => {
      for (const listener of syncHarness.listeners) listener();
    });
    syncHarness.resetView.mockImplementationOnce(() => {
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
