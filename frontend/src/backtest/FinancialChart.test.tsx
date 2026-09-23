import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChartSeries } from "./reportCharts";

const chartHarness = vi.hoisted(() => ({
  createChart: vi.fn(),
  timeScaleWidth: 1000,
}));

vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" },
  CrosshairMode: { Magnet: "magnet" },
  LineSeries: "line",
  LineType: { Simple: "simple", WithSteps: "steps" },
  createChart: chartHarness.createChart,
  createSeriesMarkers: vi.fn(),
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
  chartHarness.timeScaleWidth = 1000;
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
});
