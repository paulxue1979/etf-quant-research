import { describe, expect, it, vi } from "vitest";

import type { SyncedChart } from "./chartSync";
import { ChartSyncController, trackViewportGestures } from "./chartSync";

function fakeChart() {
  let rangeHandler: ((range: { from: string; to: string } | null) => void) | undefined;
  let crosshairHandler: ((event: { time?: string | null; seriesData: Map<unknown, unknown> }) => void) | undefined;
  let rangeError: Error | null = null;
  const timeScale = {
    subscribeVisibleTimeRangeChange: (handler: typeof rangeHandler) => { rangeHandler = handler; },
    unsubscribeVisibleTimeRangeChange: () => { rangeHandler = undefined; },
    setVisibleRange: vi.fn((range: { from: string; to: string }) => {
      if (rangeError) throw rangeError;
      rangeHandler?.(range);
    }),
    fitContent: vi.fn(),
    resetTimeScale: vi.fn(),
  };
  const chart = {
    timeScale: () => timeScale,
    subscribeCrosshairMove: (handler: typeof crosshairHandler) => { crosshairHandler = handler; },
    unsubscribeCrosshairMove: () => { crosshairHandler = undefined; },
    setCrosshairPosition: vi.fn(),
    clearCrosshairPosition: vi.fn(),
  };
  return {
    chart,
    setRangeError: (error: Error | null) => { rangeError = error; },
    triggerRange: (range: { from: string; to: string } | null) => rangeHandler?.(range),
    triggerCrosshair: (event: { time?: string | null; seriesData: Map<unknown, unknown> }) => crosshairHandler?.(event),
  };
}

function synced(
  fake: ReturnType<typeof fakeChart>,
  valuesByTime: Map<string, { series: object; value: number }>,
  isUserViewportChange?: () => boolean,
): SyncedChart {
  return {
    chart: fake.chart as never,
    series: [],
    valuesByTime: valuesByTime as never,
    isUserViewportChange,
  };
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

  it("fits every registered chart to the shared full-history range", () => {
    const first = fakeChart();
    const second = fakeChart();
    const controller = new ChartSyncController();
    controller.register("first", synced(first, new Map()));
    controller.register("second", synced(second, new Map()));

    controller.showFullHistory({ from: "2000-01-03", to: "2025-12-31" });

    for (const chart of [first, second]) {
      expect(chart.chart.timeScale().fitContent).toHaveBeenCalledTimes(1);
      expect(chart.chart.timeScale().setVisibleRange).toHaveBeenCalledWith({
        from: "2000-01-03",
        to: "2025-12-31",
      });
    }
  });

  it("resets transient chart state before restoring full history", () => {
    const first = fakeChart();
    const second = fakeChart();
    const controller = new ChartSyncController();
    controller.register("first", synced(first, new Map()));
    controller.register("second", synced(second, new Map()));

    controller.resetView({ from: "2020-01-02", to: "2025-12-31" });

    for (const chart of [first, second]) {
      expect(chart.chart.clearCrosshairPosition).toHaveBeenCalledTimes(1);
      expect(chart.chart.timeScale().resetTimeScale).toHaveBeenCalledTimes(1);
      expect(chart.chart.timeScale().fitContent).toHaveBeenCalledTimes(1);
    }
  });

  it("reports manual viewport changes but ignores programmatic synchronization", () => {
    const first = fakeChart();
    const second = fakeChart();
    const viewportChanged = vi.fn();
    const controller = new ChartSyncController();
    controller.register("first", synced(first, new Map()));
    controller.register("second", synced(second, new Map()));
    controller.subscribeViewportChange(viewportChanged);

    controller.setRange({ from: "2024-01-01", to: "2025-01-01" });
    expect(viewportChanged).not.toHaveBeenCalled();

    first.triggerRange({ from: "2024-06-01", to: "2025-01-01" });
    expect(viewportChanged).toHaveBeenCalledOnce();
    expect(viewportChanged).toHaveBeenCalledWith(
      { from: "2024-06-01", to: "2025-01-01" },
      "first",
    );
  });

  it("synchronizes resize-driven ranges without reporting a manual viewport change", () => {
    const source = fakeChart();
    const target = fakeChart();
    const viewportChanged = vi.fn();
    const controller = new ChartSyncController();
    controller.register("source", synced(source, new Map(), () => false));
    controller.register("target", synced(target, new Map()));
    controller.subscribeViewportChange(viewportChanged);

    source.triggerRange({ from: "2020-01-01", to: "2025-01-01" });

    expect(target.chart.timeScale().setVisibleRange).toHaveBeenCalledOnce();
    expect(viewportChanged).not.toHaveBeenCalled();
  });

  it("consumes pointer and wheel gestures once for viewport classification", () => {
    const element = document.createElement("div");
    const gestures = trackViewportGestures(element);

    expect(gestures.isUserViewportChange()).toBe(false);
    element.dispatchEvent(new WheelEvent("wheel"));
    expect(gestures.isUserViewportChange()).toBe(true);
    expect(gestures.isUserViewportChange()).toBe(false);

    element.dispatchEvent(new Event("pointerdown"));
    element.dispatchEvent(new Event("pointermove"));
    expect(gestures.isUserViewportChange()).toBe(true);
    element.dispatchEvent(new Event("pointerup"));
    gestures.dispose();
  });

  it("unregisters one chart without affecting the remaining charts", () => {
    const first = fakeChart();
    const second = fakeChart();
    const controller = new ChartSyncController();
    controller.register("first", synced(first, new Map()));
    const unregisterSecond = controller.register("second", synced(second, new Map()));
    unregisterSecond();

    controller.showFullHistory();

    expect(first.chart.timeScale().fitContent).toHaveBeenCalledOnce();
    expect(second.chart.timeScale().fitContent).not.toHaveBeenCalled();
  });

  it("releases the synchronization lock after a chart operation fails", () => {
    const source = fakeChart();
    const failing = fakeChart();
    const healthy = fakeChart();
    const controller = new ChartSyncController();
    controller.register("source", synced(source, new Map()));
    controller.register("failing", synced(failing, new Map()));
    controller.register("healthy", synced(healthy, new Map()));
    failing.setRangeError(new Error("chart failed"));

    expect(() => source.triggerRange({ from: "2024-01-01", to: "2025-01-01" })).toThrow(
      "chart failed",
    );
    failing.setRangeError(null);
    source.triggerRange({ from: "2024-06-01", to: "2025-01-01" });

    expect(healthy.chart.timeScale().setVisibleRange).toHaveBeenCalledTimes(2);
    expect(failing.chart.timeScale().setVisibleRange).toHaveBeenCalledTimes(2);
  });
});
