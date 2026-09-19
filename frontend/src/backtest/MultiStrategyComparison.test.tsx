import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ComparisonCompatibilityStatus, ResearchComparison } from "./types";

const harness = vi.hoisted(() => ({
  listeners: new Set<() => void>(),
  crosshairListeners: new Set<(time: string | null) => void>(),
  setRange: vi.fn(),
  showFullHistory: vi.fn(),
  resetView: vi.fn(),
}));

vi.mock("./chartSync", () => ({
  ChartSyncController: class {
    register() { return () => undefined; }
    subscribeViewportChange(handler: () => void) { harness.listeners.add(handler); return () => harness.listeners.delete(handler); }
    subscribeCrosshairChange(handler: (time: string | null) => void) { harness.crosshairListeners.add(handler); return () => harness.crosshairListeners.delete(handler); }
    setRange = harness.setRange;
    showFullHistory = harness.showFullHistory;
    resetView = harness.resetView;
  },
}));

vi.mock("./MultiStrategyTwrChart", () => ({
  MultiStrategyTwrChart: ({ visibleSeries, hoverDate }: { visibleSeries: Array<{ runId: string }>; hoverDate: string | null }) => <section aria-label="Multi-strategy TWR comparison" data-runs={visibleSeries.map((item) => item.runId).join(",")} data-hover-date={hoverDate} />,
  MultiStrategyDrawdownChart: ({ visibleSeries, hoverDate }: { visibleSeries: Array<{ runId: string }>; hoverDate: string | null }) => <section aria-label="Multi-strategy drawdown comparison" data-runs={visibleSeries.map((item) => item.runId).join(",")} data-hover-date={hoverDate} />,
}));

import { MultiStrategyComparison } from "./MultiStrategyComparison";

afterEach(() => {
  cleanup();
  harness.listeners.clear();
  harness.crosshairListeners.clear();
  harness.setRange.mockClear();
  harness.showFullHistory.mockClear();
  harness.resetView.mockClear();
});

function payload(count = 2, status: ComparisonCompatibilityStatus = "COMPARABLE"): ResearchComparison {
  const runIds = Array.from({ length: count }, (_, index) => `run-${index + 1}`);
  return {
    comparison_schema_version: "2.0",
    ordering: "request_order",
    include: { twr: true, drawdown: true, portfolio_value: false, metrics: true },
    compatibility: { twr: {
      status,
      reason_codes: status === "COMPARABLE" ? [] : [`${status}_REASON`],
      human_readable_reasons: status === "COMPARABLE" ? [] : [`${status} research context.`],
      dimensions: {},
    } },
    runs: runIds.map((runId, index) => ({
      backtest_run_id: runId, strategy_id: `s-${index}`, strategy_version_id: `v-${index}`, strategy_name: `Strategy ${index + 1}`, strategy_version: `v${index + 1}`, short_display_label: `Strategy ${index + 1} · v${index + 1} · ${runId}`, start_date: "2000-01-03", end_date: "2025-01-03", asset_universe: ["QQQ"], metrics_status: "included",
      metrics: {
        cagr: { value: 0.1 + index / 100, status: "available", reason: null },
        total_twr_return: { value: 1 + index / 10, status: "available", reason: null },
        max_drawdown: { value: -0.2 - index / 100, status: "available", reason: null },
        sharpe_ratio: { value: 1 + index / 10, status: "available", reason: null },
        sortino_ratio: { value: 1.5 + index / 10, status: "available", reason: null },
        calmar_ratio: { value: 0.5 + index / 10, status: "available", reason: null },
        exposure: { value: { average_gross_exposure: 0.8 + index / 100 }, status: "available", reason: null },
        portfolio_turnover: { value: 0.25 + index / 100, status: "available", reason: null },
        xirr: { value: 0.09 + index / 100, status: "available", reason: null },
        trade_count: { value: 5 + index, status: "available", reason: null },
        holding_period_count: { value: 7 + index, status: "available", reason: null },
      },
    })),
    series: runIds.map((runId, index) => ({ backtest_run_id: runId, strategy_version_id: `v-${index}`, start_date: "2000-01-03", end_date: "2025-01-03", twr: { status: "available", points: [{ date: "2000-01-03", value: 100 }, { date: "2025-01-03", value: 110 + index }] }, drawdown: { status: "available", points: [{ date: "2000-01-03", value: 0 }, { date: "2025-01-03", value: -0.2 - index / 100 }] }, portfolio_value: { status: "excluded", points: [] } })),
    provenance_notice: "Immutable comparison provenance.",
  };
}

describe("MultiStrategyComparison", () => {
  it("renders two and ten canonical TWR series with backend identity labels", () => {
    const { rerender } = render(<MultiStrategyComparison comparison={payload()} />);
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1,run-2");
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-runs", "run-1,run-2");
    expect(screen.getByRole("button", { name: /Strategy 1/ })).toBeInTheDocument();

    rerender(<MultiStrategyComparison comparison={payload(10)} />);
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1,run-2,run-3,run-4,run-5,run-6,run-7,run-8,run-9,run-10");
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-runs", "run-1,run-2,run-3,run-4,run-5,run-6,run-7,run-8,run-9,run-10");
    expect(screen.getAllByTitle("Click to hide or show; double-click to focus")).toHaveLength(10);
  });

  it("supports hide, show, focus, exit focus, and Show All", async () => {
    const user = userEvent.setup();
    render(<MultiStrategyComparison comparison={payload(3)} />);
    const first = screen.getByRole("button", { name: /Strategy 1/ });

    await user.click(first);
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-2,run-3");
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-runs", "run-2,run-3");
    await user.click(first);
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1,run-2,run-3");
    await user.dblClick(screen.getByRole("button", { name: /Strategy 2/ }));
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-2");
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-runs", "run-2");
    await user.click(screen.getByRole("button", { name: "Exit Focus" }));
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1,run-2,run-3");
    await user.click(first);
    await user.click(screen.getByRole("button", { name: "Show All" }));
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1,run-2,run-3");
  });

  it("keeps global date navigation and Reset View independent from focus mode", async () => {
    const user = userEvent.setup();
    render(<MultiStrategyComparison comparison={payload()} />);

    await user.click(screen.getByRole("button", { name: "1Y" }));
    expect(harness.setRange).toHaveBeenCalledWith({ from: "2024-01-03", to: "2025-01-03" });
    await user.click(screen.getByRole("button", { name: "MAX" }));
    expect(harness.showFullHistory).toHaveBeenLastCalledWith({ from: "2000-01-03", to: "2025-01-03" });
    await user.dblClick(screen.getByRole("button", { name: /Strategy 1/ }));
    await user.click(screen.getByRole("button", { name: "Reset View" }));
    expect(harness.resetView).toHaveBeenCalledWith({ from: "2000-01-03", to: "2025-01-03" });
    expect(screen.getByRole("button", { name: "Exit Focus" })).toBeInTheDocument();

    act(() => { for (const listener of harness.listeners) listener(); });
    expect(screen.getByRole("status")).toHaveTextContent("Custom view");
    await user.click(screen.getByRole("button", { name: "Fit All" }));
    expect(screen.queryByText("Custom view")).not.toBeInTheDocument();
  });

  it("publishes one exact shared crosshair date to both canonical charts", () => {
    render(<MultiStrategyComparison comparison={payload()} />);

    act(() => { for (const listener of harness.crosshairListeners) listener("2025-01-03"); });

    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-hover-date", "2025-01-03");
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-hover-date", "2025-01-03");
  });

  it.each([
    ["WARNING", "TWR comparison warning"],
    ["UNKNOWN", "TWR compatibility unknown"],
  ] as const)("renders %s explicitly while retaining the chart", (status, title) => {
    render(<MultiStrategyComparison comparison={payload(2, status)} />);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(`${status} research context.`)).toBeInTheDocument();
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toBeInTheDocument();
  });

  it("blocks an INCOMPATIBLE TWR plot instead of presenting it as fair", () => {
    render(<MultiStrategyComparison comparison={payload(2, "INCOMPATIBLE")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Plotting is disabled");
    expect(screen.queryByLabelText("Multi-strategy TWR comparison")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MAX" })).toBeDisabled();
  });

  it("renders missing canonical TWR capability as an explicit legend state", () => {
    const value = payload();
    value.series[1].twr = { status: "not_available", reason: "canonical TWR missing", points: [] };
    render(<MultiStrategyComparison comparison={value} />);

    expect(screen.getByText(/canonical TWR missing/)).toBeInTheDocument();
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-runs", "run-1");
  });
});
