import { describe, expect, it, vi } from "vitest";

import type { SyncedChart } from "./chartSync";
import { ChartSyncController } from "./chartSync";

function fakeChart() {
  let rangeHandler: ((range: { from: string; to: string } | null) => void) | undefined;
  let crosshairHandler: ((event: { time?: string | null; seriesData: Map<unknown, unknown> }) => void) | undefined;
  const timeScale = {
    subscribeVisibleTimeRangeChange: (handler: typeof rangeHandler) => { rangeHandler = handler; },
    unsubscribeVisibleTimeRangeChange: () => { rangeHandler = undefined; },
    setVisibleRange: (range: { from: string; to: string }) => rangeHandler?.(range),
  };
  const chart = {
    timeScale: () => timeScale,
    subscribeCrosshairMove: (handler: typeof crosshairHandler) => { crosshairHandler = handler; },
    unsubscribeCrosshairMove: () => { crosshairHandler = undefined; },
    setCrosshairPosition: () => undefined,
    clearCrosshairPosition: () => undefined,
  };
  return {
    chart,
    triggerRange: (range: { from: string; to: string } | null) => rangeHandler?.(range),
    triggerCrosshair: (event: { time?: string | null; seriesData: Map<unknown, unknown> }) => crosshairHandler?.(event),
  };
}

function synced(fake: ReturnType<typeof fakeChart>, valuesByTime: Map<string, { series: object; value: number }>): SyncedChart {
  return { chart: fake.chart as never, series: [], valuesByTime: valuesByTime as never };
}

describe("ChartSyncController", () => {
  it("synchronizes visible ranges without recursively rebroadcasting them", () => {
    const source = fakeChart();
    const target = fakeChart();
    const sourceChart = synced(source, new Map());
    const targetChart = synced(target, new Map());
    const setRange = vi.spyOn(targetChart.chart.timeScale(), "setVisibleRange");
    const controller = new ChartSyncController();
    controller.register("source", sourceChart);
    controller.register("target", targetChart);

    source.triggerRange({ from: "2024-01-01", to: "2025-01-01" });

    expect(setRange).toHaveBeenCalledTimes(1);
    expect(setRange).toHaveBeenCalledWith({ from: "2024-01-01", to: "2025-01-01" });
  });

  it("synchronizes crosshairs by date using each chart's own value", () => {
    const source = fakeChart();
    const target = fakeChart();
    const sourceSeries = {};
    const targetSeries = {};
    const sourceChart = synced(source, new Map([["2025-01-02", { series: sourceSeries, value: 100 }]]));
    const targetChart = synced(target, new Map([["2025-01-02", { series: targetSeries, value: 0.98 }]]));
    const setCrosshair = vi.spyOn(targetChart.chart, "setCrosshairPosition");
    const controller = new ChartSyncController();
    controller.register("source", sourceChart);
    controller.register("target", targetChart);

    source.triggerCrosshair({ time: "2025-01-02", seriesData: new Map() });

    expect(setCrosshair).toHaveBeenCalledWith(0.98, "2025-01-02", targetSeries);
  });

  it("clears a sibling crosshair when the date has no point or the pointer leaves", () => {
    const source = fakeChart();
    const target = fakeChart();
    const sourceChart = synced(source, new Map());
    const targetChart = synced(target, new Map([["2025-01-02", { series: {}, value: 0.98 }]]));
    const clearCrosshair = vi.spyOn(targetChart.chart, "clearCrosshairPosition");
    const controller = new ChartSyncController();
    controller.register("source", sourceChart);
    controller.register("target", targetChart);

    source.triggerCrosshair({ time: "2025-01-03", seriesData: new Map() });
    source.triggerCrosshair({ time: null, seriesData: new Map() });

    expect(clearCrosshair).toHaveBeenCalledTimes(2);
  });
});
