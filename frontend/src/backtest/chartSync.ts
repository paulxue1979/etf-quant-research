import type { IChartApi, IRange, ISeriesApi, MouseEventParams, SeriesType, Time } from "lightweight-charts";

export type SyncSeries = ISeriesApi<SeriesType>;

export interface SyncedChart {
  chart: IChartApi;
  series: SyncSeries[];
  valuesByTime: Map<string, { series: SyncSeries; value: number }>;
  isUserViewportChange?: () => boolean;
}

export type ViewportChangeHandler = (range: IRange<Time>, sourceId: string) => void;

export class ChartSyncController {
  private readonly charts = new Map<string, SyncedChart>();
  private readonly viewportChangeHandlers = new Set<ViewportChangeHandler>();
  private syncing = false;

  register(id: string, chart: SyncedChart): () => void {
    this.charts.set(id, chart);
    const rangeHandler = (range: IRange<Time> | null) => {
      if (!range || this.syncing) return;
      const userInitiated = chart.isUserViewportChange?.() ?? true;
      try {
        this.runSynchronized((otherId, other) => {
          if (otherId !== id) other.chart.timeScale().setVisibleRange(range);
        });
      } finally {
        if (userInitiated) {
          for (const handler of this.viewportChangeHandlers) handler(range, id);
        }
      }
    };
    const crosshairHandler = (event: MouseEventParams<Time>) => {
      if (this.syncing) return;
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

  setRange(range: IRange<Time>): void {
    this.runSynchronized((_id, chart) => chart.chart.timeScale().setVisibleRange(range));
  }

  showFullHistory(range?: IRange<Time>): void {
    this.runSynchronized((_id, chart) => {
      chart.chart.timeScale().fitContent();
      if (range) chart.chart.timeScale().setVisibleRange(range);
    });
  }

  resetView(range?: IRange<Time>): void {
    try {
      this.runSynchronized((_id, chart) => {
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
}

export function trackViewportGestures(element: HTMLElement): {
  isUserViewportChange: () => boolean;
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
