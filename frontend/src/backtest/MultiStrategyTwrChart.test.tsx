import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ComparisonChartSeries } from "./comparisonCharts";

const chartHarness = vi.hoisted(() => ({
  lines: [] as Array<{ setData: ReturnType<typeof vi.fn> }>,
  crosshairHandler: null as ((event: { time?: string }) => void) | null,
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

import { MultiStrategyTwrChart } from "./MultiStrategyTwrChart";

const allSeries: ComparisonChartSeries[] = [
  { runId: "run-1", label: "Strategy 1", color: "#111111", status: "available", points: [{ date: "2025-01-02", value: 100 }, { date: "2025-01-03", value: 101 }] },
  { runId: "run-2", label: "Strategy 2", color: "#222222", status: "available", points: [{ date: "2025-01-03", value: 99 }] },
];

beforeEach(() => {
  chartHarness.lines = [];
  chartHarness.crosshairHandler = null;
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
    subscribeCrosshairMove: vi.fn((handler) => { chartHarness.crosshairHandler = handler; }),
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
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} onReady={() => () => undefined} />);

    expect(chartHarness.lines).toHaveLength(2);
    expect(chartHarness.lines[0].setData).toHaveBeenCalledWith([{ time: "2025-01-02", value: 100 }, { time: "2025-01-03", value: 101 }]);
    expect(chartHarness.lines[1].setData).toHaveBeenCalledWith([{ time: "2025-01-03", value: 99 }]);
  });

  it("shows a shared exact-date tooltip and reports missing dates as N/A", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} onReady={() => () => undefined} />);

    act(() => chartHarness.crosshairHandler?.({ time: "2025-01-02" }));
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 1: 100.00");
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 2: N/A");
  });

  it("excludes hidden runs from the tooltip contract", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set(["run-2"])} focusRunId={null} visibleSeries={[allSeries[0]]} onReady={() => () => undefined} />);

    act(() => chartHarness.crosshairHandler?.({ time: "2025-01-03" }));
    expect(screen.getByRole("status")).toHaveTextContent("Strategy 1: 101.00");
    expect(screen.getByRole("status")).not.toHaveTextContent("Strategy 2");
  });

  it("resizes the date-aware chart through ResizeObserver", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set()} focusRunId={null} visibleSeries={allSeries} onReady={() => () => undefined} />);
    expect(chartHarness.resize).toHaveBeenCalledWith(840, 500);

    act(() => chartHarness.resizeCallback?.());
    expect(chartHarness.resize).toHaveBeenCalledTimes(2);
  });

  it("renders an explicit empty state instead of creating a blank chart", () => {
    render(<MultiStrategyTwrChart allSeries={allSeries} hiddenRunIds={new Set(["run-1", "run-2"])} focusRunId={null} visibleSeries={[]} onReady={() => () => undefined} />);

    expect(screen.getByRole("status")).toHaveTextContent("No visible canonical TWR series");
    expect(chartHarness.createChart).not.toHaveBeenCalled();
  });
});
