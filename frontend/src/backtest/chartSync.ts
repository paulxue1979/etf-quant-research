import type { IChartApi, IRange, ISeriesApi, MouseEventParams, SeriesType, Time } from "lightweight-charts";

export type SyncSeries = ISeriesApi<SeriesType>;

export interface SyncedChart {
  chart: IChartApi;
  series: SyncSeries[];
  valuesByTime: Map<string, { series: SyncSeries; value: number }>;
}

export class ChartSyncController {
  private readonly charts = new Map<string, SyncedChart>();
  private syncing = false;

  register(id: string, chart: SyncedChart): () => void {
    this.charts.set(id, chart);
    const rangeHandler = (range: IRange<Time> | null) => {
      if (!range || this.syncing) return;
      this.syncing = true;
      for (const [otherId, other] of this.charts) if (otherId !== id) other.chart.timeScale().setVisibleRange(range);
      this.syncing = false;
    };
    const crosshairHandler = (event: MouseEventParams<Time>) => {
      if (this.syncing) return;
      this.syncing = true;
      if (event.time === undefined || event.time === null) {
        for (const [otherId, other] of this.charts) {
          if (otherId !== id) other.chart.clearCrosshairPosition();
        }
        this.syncing = false;
        return;
      }
      const timeKey = String(event.time);
      for (const [otherId, other] of this.charts) {
        if (otherId === id) continue;
        const target = other.valuesByTime.get(timeKey);
        if (target) other.chart.setCrosshairPosition(target.value, event.time, target.series);
        else other.chart.clearCrosshairPosition();
      }
      this.syncing = false;
    };
    chart.chart.timeScale().subscribeVisibleTimeRangeChange(rangeHandler);
    chart.chart.subscribeCrosshairMove(crosshairHandler);
    return () => {
      chart.chart.timeScale().unsubscribeVisibleTimeRangeChange(rangeHandler);
      chart.chart.unsubscribeCrosshairMove(crosshairHandler);
      this.charts.delete(id);
    };
  }

  setRange(range: IRange<Time>): void {
    this.syncing = true;
    for (const chart of this.charts.values()) chart.chart.timeScale().setVisibleRange(range);
    this.syncing = false;
  }
}
