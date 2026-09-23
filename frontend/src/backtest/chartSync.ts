import type { IChartApi, IRange, ISeriesApi, MouseEventParams, SeriesType, Time } from "lightweight-charts";

export type SyncSeries = ISeriesApi<SeriesType>;

export type ViewportMode = "INITIAL" | "1Y" | "3Y" | "5Y" | "MAX" | "CUSTOM";

export interface ViewportState {
  mode: ViewportMode;
  range: IRange<Time> | null;
}

export interface SyncedChart {
  chart: IChartApi;
  series: SyncSeries[];
  valuesByTime: Map<string, { series: SyncSeries; value: number }>;
  isUserViewportChange?: () => boolean;
  clearPendingViewportGesture?: () => void;
}

export type ViewportChangeHandler = (range: IRange<Time>, sourceId: string) => void;
export type CrosshairChangeHandler = (time: string | null, sourceId: string) => void;

const DEFAULT_MIN_BAR_SPACING = 0.1;
const MIN_BAR_SPACING_FLOOR = 0.01;

/** Keep long histories fit-able without allowing an unboundedly small bar spacing. */
export function configureResponsiveMinBarSpacing(chart: IChartApi, pointCount: number): number {
  const width = chart.timeScale().width();
  const minBarSpacing = pointCount > 0 && width > 0
    ? Math.max(MIN_BAR_SPACING_FLOOR, Math.min(DEFAULT_MIN_BAR_SPACING, width / pointCount))
    : DEFAULT_MIN_BAR_SPACING;
  chart.applyOptions({ timeScale: { minBarSpacing } });
  return minBarSpacing;
}

export class ChartSyncController {
  private readonly charts = new Map<string, SyncedChart>();
  private readonly viewportChangeHandlers = new Set<ViewportChangeHandler>();
  private readonly crosshairChangeHandlers = new Set<CrosshairChangeHandler>();
  private viewport: ViewportState = { mode: "INITIAL", range: null };
  private syncing = false;

  register(id: string, chart: SyncedChart): () => void {
    this.charts.set(id, chart);
    const rangeHandler = (range: IRange<Time> | null) => {
      if (!range || this.syncing) return;
      const userInitiated = chart.isUserViewportChange?.() ?? true;
      if (!userInitiated && this.viewport.range) return;
      try {
        this.runSynchronized((otherId, other) => {
          if (otherId !== id) this.applyRangeToChart(other, range);
        });
      } finally {
        if (userInitiated) {
          this.viewport = { mode: "CUSTOM", range };
          for (const handler of this.viewportChangeHandlers) handler(range, id);
        }
      }
    };
    const crosshairHandler = (event: MouseEventParams<Time>) => {
      if (this.syncing) return;
      const time = typeof event.time === "string" ? event.time : null;
      for (const handler of this.crosshairChangeHandlers) handler(time, id);
      this.runSynchronized((otherId, other) => {
        if (otherId === id) return;
        if (event.time === undefined || event.time === null) {
          other.chart.clearCrosshairPosition();
          return;
        }
        const target = other.valuesByTime.get(String(event.time));
        if (target) other.chart.setCrosshairPosition(target.value, event.time, target.series);
        else other.chart.clearCrosshairPosition();
      });
    };
    chart.chart.timeScale().subscribeVisibleTimeRangeChange(rangeHandler);
    chart.chart.subscribeCrosshairMove(crosshairHandler);
    if (this.viewport.range) {
      this.applyRangeToChart(chart, this.viewport.range);
      this.scheduleAuthoritativeRange(chart);
    }
    return () => {
      chart.chart.timeScale().unsubscribeVisibleTimeRangeChange(rangeHandler);
      chart.chart.unsubscribeCrosshairMove(crosshairHandler);
      if (this.charts.get(id) === chart) this.charts.delete(id);
    };
  }

  subscribeViewportChange(handler: ViewportChangeHandler): () => void {
    this.viewportChangeHandlers.add(handler);
    return () => this.viewportChangeHandlers.delete(handler);
  }

  subscribeCrosshairChange(handler: CrosshairChangeHandler): () => void {
    this.crosshairChangeHandlers.add(handler);
    return () => this.crosshairChangeHandlers.delete(handler);
  }

  getViewportState(): ViewportState {
    return { mode: this.viewport.mode, range: this.viewport.range };
  }

  initialize(range: IRange<Time>): void {
    this.showFullHistory(range, "MAX");
  }

  setRange(range: IRange<Time>, mode: ViewportMode = "CUSTOM"): void {
    this.viewport = { mode, range };
    this.runSynchronized((_id, chart) => {
      this.applyRangeToChart(chart, range);
      this.scheduleAuthoritativeRange(chart);
    });
  }

  showFullHistory(range?: IRange<Time>, mode: ViewportMode = "MAX"): void {
    const nextRange = range ?? this.viewport.range;
    this.viewport = { mode, range: nextRange ?? null };
    this.runSynchronized((id, chart) => {
      chart.clearPendingViewportGesture?.();
      chart.chart.timeScale().fitContent();
      if (nextRange) {
        chart.chart.timeScale().setVisibleRange(nextRange);
      }
      if (nextRange) this.scheduleAuthoritativeRange(chart);
    });
  }

  resetView(range?: IRange<Time>): void {
    try {
      this.runSynchronized((_id, chart) => {
        chart.clearPendingViewportGesture?.();
        chart.chart.clearCrosshairPosition();
        chart.chart.timeScale().resetTimeScale();
      });
    } finally {
      this.showFullHistory(range);
    }
  }

  private runSynchronized(operation: (id: string, chart: SyncedChart) => void): void {
    this.syncing = true;
    let firstError: unknown;
    try {
      for (const [id, chart] of this.charts) {
        try {
          operation(id, chart);
        } catch (error) {
          firstError ??= error;
        }
      }
    } finally {
      this.syncing = false;
    }
    if (firstError !== undefined) throw firstError;
  }

  private applyRangeToChart(chart: SyncedChart, range: IRange<Time>): void {
    this.syncing = true;
    try {
      chart.clearPendingViewportGesture?.();
      chart.chart.timeScale().setVisibleRange(range);
    } finally {
      this.syncing = false;
    }
  }

  private scheduleAuthoritativeRange(chart: SyncedChart): void {
    const reconcile = () => {
      if (!this.viewport.range || ![...this.charts.values()].includes(chart)) return;
      this.applyRangeToChart(chart, this.viewport.range);
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(reconcile);
    else queueMicrotask(reconcile);
  }
}

export function trackViewportGestures(element: HTMLElement): {
  isUserViewportChange: () => boolean;
  clearPendingViewportGesture: () => void;
  dispose: () => void;
} {
  let pointerActive = false;
  let viewportGesturePending = false;
  const markViewportGesture = () => { viewportGesturePending = true; };
  const pointerDown = () => { pointerActive = true; };
  const pointerMove = () => { if (pointerActive) markViewportGesture(); };
  const pointerUp = () => { pointerActive = false; };
  element.addEventListener("pointerdown", pointerDown);
  element.addEventListener("pointermove", pointerMove);
  element.addEventListener("pointerup", pointerUp);
  element.addEventListener("pointercancel", pointerUp);
  element.addEventListener("pointerleave", pointerUp);
  element.addEventListener("wheel", markViewportGesture, { passive: true });
  return {
    isUserViewportChange: () => {
      const userInitiated = viewportGesturePending;
      viewportGesturePending = false;
      return userInitiated;
    },
    clearPendingViewportGesture: () => { viewportGesturePending = false; },
    dispose: () => {
      element.removeEventListener("pointerdown", pointerDown);
      element.removeEventListener("pointermove", pointerMove);
      element.removeEventListener("pointerup", pointerUp);
      element.removeEventListener("pointercancel", pointerUp);
      element.removeEventListener("pointerleave", pointerUp);
      element.removeEventListener("wheel", markViewportGesture);
    },
  };
}
