import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ComparisonChartSeries } from "./comparisonCharts";

const chartHarness = vi.hoisted(() => ({
  lines: [] as Array<{ setData: ReturnType<typeof vi.fn> }>,
  resize: vi.fn(),
  remove: vi.fn(),
  createChart: vi.fn(),
  resizeCallback: null as (() => void) | null,
}));

vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: "normal" },
  LineSeries: "line",
  createChart: chartHarness.createChart,
}));

import { MultiStrategyDrawdownChart, MultiStrategyTwrChart } from "./MultiStrategyTwrChart";

const allSeries: ComparisonChartSeries[] = [
  { runId: "run-1", label: "Strategy 1", color: "#111111", status: "available", points: [{ date: "2025-01-02", value: 100 }, { date: "2025-01-03", value: 101 }] },
  { runId: "run-2", label: "Strategy 2", color: "#222222", status: "available", points: [{ date: "2025-01-03", value: 99 }] },
];

beforeEach(() => {
  chartHarness.lines = [];
  chartHarness.resize.mockClear();
  chartHarness.remove.mockClear();
  chartHarness.createChart.mockImplementation(() => ({
    addSeries: vi.fn(() => {
      const line = { setData: vi.fn() };
      chartHarness.lines.push(line);
      return line;
    }),
    timeScale: () => ({
      fitContent: vi.fn(),
      subscribeVisibleTimeRangeChange: vi.fn(),
      unsubscribeVisibleTimeRangeChange: vi.fn(),
    }),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    resize: chartHarness.resize,
    remove: chartHarness.remove,
  }));
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 840 });
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { chartHarness.resizeCallback = callback; }
    observe() { return undefined; }
    disconnect() { return undefined; }
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MultiStrategyTwrChart", () => {
  it("passes two sparse backend series to Lightweight Charts without aligning their indexes", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} hoverDate={null} onReady={() => () => undefined} />);

    expect(chartHarness.lines).toHaveLength(2);
    expect(chartHarness.lines[0].setData).toHaveBeenCalledWith([{ time: "2025-01-02", value: 100 }, { time: "2025-01-03", value: 101 }]);
    expect(chartHarness.lines[1].setData).toHaveBeenCalledWith([{ time: "2025-01-03", value: 99 }]);
  });

  it("shows a shared exact-date tooltip and reports missing dates as N/A", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} hoverDate="2025-01-02" onReady={() => () => undefined} />);
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 1: 100.00");
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 2: N/A");
  });

  it("excludes hidden runs from the tooltip contract", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set(["run-2"])} focusRunId={null} visibleSeries={[allSeries[0]]} hoverDate="2025-01-03" onReady={() => () => undefined} />);
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 1: 101.00");
    expect(screen.getByRole("status")).not.toHaveTextContent("Strategy 2");
  });

  it("resizes the date-aware chart through ResizeObserver", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} hoverDate={null} onReady={() => () => undefined} />);
    expect(chartHarness.resize).toHaveBeenCalledWith(840, 500);

    act(() => chartHarness.resizeCallback?.());
    expect(chartHarness.resize).toHaveBeenCalledTimes(2);
  });

  it("renders an explicit empty state instead of creating a blank chart", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set(["run-1", "run-2"])} focusRunId={null} visibleSeries={[]} hoverDate={null} onReady={() => () => undefined} />);

    expect(screen.getByRole("status")).toHaveTextContent("No visible canonical TWR series");
    expect(chartHarness.createChart).not.toHaveBeenCalled();
  });

  it("renders ten canonical drawdown series without changing their exact dates", () => {
    const drawdownSeries = Array.from({ length: 10 }, (_, index) => ({
      runId: `run-${index + 1}`,
      label: `Strategy ${index + 1}`,
      color: `#00000${index}`,
      status: "available" as const,
      points: [{ date: `2025-01-${String(index + 2).padStart(2, "0")}`, value: -index / 100 }],
    }));
    render(<MultiStrategyDrawdownChart allSeries={drawdownSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={drawdownSeries} hoverDate={null} onReady={() => () => undefined} />);

    expect(chartHarness.lines).toHaveLength(10);
    expect(chartHarness.lines[9].setData).toHaveBeenCalledWith([{ time: "2025-01-11", value: -0.09 }]);
    expect(screen.getByLabelText("Multi-strategy drawdown comparison")).toHaveAttribute("data-series-count", "10");
    expect(chartHarness.resize).toHaveBeenCalledWith(840, 320);
  });

  it("formats drawdown at the shared exact date and keeps missing dates as N/A", () => {
    const drawdownSeries = allSeries.map((item, index) => ({
      ...item,
      points: index === 0 ? [{ date: "2025-01-02", value: -0.125 }] : [{ date: "2025-01-03", value: -0.08 }],
    }));
    render(<MultiStrategyDrawdownChart allSeries={drawdownSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={drawdownSeries} hoverDate="2025-01-02" onReady={() => () => undefined} />);

    expect(screen.getByRole("status")).toHaveTextContent("Strategy 1: -12.50%");
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 2: N/A");
  });
});
