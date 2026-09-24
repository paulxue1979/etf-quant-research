import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChartSeries } from "./reportCharts";

const chartHarness = vi.hoisted(() => ({
  createChart: vi.fn(),
  createSeriesMarkers: vi.fn(),
  timeScaleWidth: 1000,
  clickHandler: null as ((event: { time?: string }) => void) | null,
}));

vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" },
  CrosshairMode: { Magnet: "magnet" },
  LineSeries: "line",
  LineType: { Simple: "simple", WithSteps: "steps" },
  createChart: chartHarness.createChart,
  createSeriesMarkers: chartHarness.createSeriesMarkers,
}));

import { FinancialChart } from "./FinancialChart";

const series: ChartSeries[] = [{
  key: "equity",
  label: "Portfolio Value",
  unit: "USD",
  color: "#6bd7d0",
  points: [{ date: "2000-01-03", value: 100 }, { date: "2026-09-16", value: 200 }],
  status: "available",
}];

beforeEach(() => {
  chartHarness.createChart.mockImplementation(() => ({
    applyOptions: vi.fn(),
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: () => ({
      width: vi.fn(() => chartHarness.timeScaleWidth),
      applyOptions: vi.fn(),
      getVisibleRange: vi.fn(() => null),
      subscribeVisibleTimeRangeChange: vi.fn(),
      unsubscribeVisibleTimeRangeChange: vi.fn(),
    }),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    subscribeClick: vi.fn((handler: (event: { time?: string }) => void) => { chartHarness.clickHandler = handler; }),
    unsubscribeClick: vi.fn(),
    resize: vi.fn(),
    remove: vi.fn(),
  }));
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  vi.stubGlobal("ResizeObserver", class {
    observe() { return undefined; }
    disconnect() { return undefined; }
  });
});

afterEach(() => {
  cleanup();
  chartHarness.createChart.mockReset();
  chartHarness.createSeriesMarkers.mockReset();
  chartHarness.timeScaleWidth = 1000;
  chartHarness.clickHandler = null;
  vi.unstubAllGlobals();
});

describe("FinancialChart long-history viewport", () => {
  it("allows MAX to request more than the default two-thousand-bar viewport", () => {
    render(<FinancialChart title="Portfolio" description="Long history" series={series} height={400} onReady={() => () => undefined} />);

    const options = chartHarness.createChart.mock.calls[0]?.[1] as { timeScale: { minBarSpacing: number } };
    expect(options.timeScale.minBarSpacing).toBe(0.1);
  });

  it("derives a narrower spacing for a long history in a narrow chart", () => {
    chartHarness.timeScaleWidth = 208;
    const longSeries: ChartSeries[] = [{
      ...series[0],
      points: Array.from({ length: 4_000 }, (_, index) => ({ date: `day-${index}`, value: index })),
    }];
    render(<FinancialChart title="Portfolio" description="Long history" series={longSeries} height={400} onReady={() => () => undefined} />);

    const chart = chartHarness.createChart.mock.results[0]?.value as { applyOptions: ReturnType<typeof vi.fn> };
    expect(chart.applyOptions).toHaveBeenCalledWith({ timeScale: { minBarSpacing: 0.052 } });
  });

  it.each([
    ["BUY", "QQQ", "arrowUp"],
    ["SELL", "TQQQ", "arrowDown"],
  ])("uses canonical %s side for the %s execution marker", (side, symbol, shape) => {
    const marker = {
      date: "2000-01-03",
      kind: "execution" as const,
      label: `${side} ${symbol}`,
      shortLabel: `${side} ${symbol}`,
      color: side === "SELL" ? "#ff8b8b" : "#6bd7d0",
      details: [`${side} ${symbol}`, "Quantity: 1"],
      direction: side as "BUY" | "SELL",
      markerId: `marker-${side}`,
      events: [],
    };

    render(<FinancialChart title="Portfolio" description="Markers" series={series} markers={[marker]} height={400} onReady={() => () => undefined} />);

    const plotted = chartHarness.createSeriesMarkers.mock.calls.at(-1)?.[1] as Array<{ shape: string; text: string }>;
    expect(plotted[0]).toMatchObject({ shape, text: `${side} ${symbol}` });
  });

  it("opens and closes marker detail without writing a viewport range", async () => {
    const user = userEvent.setup();
    const marker = {
      date: "2000-01-03",
      kind: "group" as const,
      label: "2 Events",
      shortLabel: "2",
      color: "#d8e1e5",
      details: ["Regime transition", "BUY QQQ"],
      groupId: "group-1",
      markerCount: 2,
      markerIds: ["marker-1", "marker-2"],
      markerTypes: ["REGIME_TRANSITION", "EXECUTION"],
      events: [
        { markerId: "marker-1", markerType: "REGIME_TRANSITION", title: "Regime", summary: "DEEP_VALUE -> RECOVERY_HOLD", sourceEventType: "Canonical.Regime", sourceEventReference: "regime-1", details: ["From state: DEEP_VALUE", "To state: RECOVERY_HOLD"] },
        { markerId: "marker-2", markerType: "EXECUTION", title: "Execution", summary: "BUY QQQ", sourceEventType: "BacktestResult.fills", sourceEventReference: "fill-1", details: ["BUY QQQ", "Quantity: 1"] },
      ],
    };
    render(<FinancialChart title="Portfolio" description="Markers" series={series} markers={[marker]} height={400} onReady={() => () => undefined} />);

    act(() => chartHarness.clickHandler?.({ time: "2000-01-03" }));
    expect(screen.getByRole("dialog", { name: "2 Events details" })).toHaveTextContent("DEEP_VALUE -> RECOVERY_HOLD");
    expect(screen.getByRole("dialog", { name: "2 Events details" })).toHaveTextContent("BUY QQQ");
    const chart = chartHarness.createChart.mock.results[0]?.value as { timeScale: () => { setVisibleRange?: ReturnType<typeof vi.fn> } };
    expect(chart.timeScale().setVisibleRange).toBeUndefined();

    await user.click(screen.getByRole("button", { name: "Close event details" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
