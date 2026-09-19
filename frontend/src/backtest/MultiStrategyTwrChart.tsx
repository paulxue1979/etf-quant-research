import { useEffect, useRef, useState } from "react";
import { ColorType, CrosshairMode, LineSeries, createChart, type Time } from "lightweight-charts";

import { comparisonTooltipRows, type ComparisonChartSeries } from "./comparisonCharts";
import { trackViewportGestures, type SyncedChart, type SyncSeries } from "./chartSync";

interface MultiStrategyTwrChartProps {
  allSeries: ComparisonChartSeries[];
  hiddenRunIds: ReadonlySet<string>;
  focusRunId: string | null;
  visibleSeries: ComparisonChartSeries[];
  onReady: (chart: SyncedChart) => () => void;
}

export function MultiStrategyTwrChart({ allSeries, hiddenRunIds, focusRunId, visibleSeries, onReady }: MultiStrategyTwrChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ date: string; rows: ReturnType<typeof comparisonTooltipRows> } | null>(null);

  useEffect(() => {
    if (!containerRef.current || visibleSeries.length === 0) return undefined;
    const container = containerRef.current;
    const gestures = trackViewportGestures(container);
    const chart = createChart(container, {
      height: 500,
      layout: { background: { type: ColorType.Solid, color: "#0f181d" }, textColor: "#8c9aa4", attributionLogo: false },
      grid: { vertLines: { color: "#1d2a31" }, horzLines: { color: "#1d2a31" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2b3942" },
      timeScale: { borderColor: "#2b3942", timeVisible: false, rightOffset: 4 },
    });
    const resize = () => {
      if (container.clientWidth > 0) chart.resize(container.clientWidth, 500);
    };
    resize();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(container);
    const chartSeries: SyncSeries[] = [];
    const valuesByTime = new Map<string, { series: SyncSeries; value: number }>();
    for (const item of visibleSeries) {
      const line = chart.addSeries(LineSeries, {
        color: item.color,
        lineWidth: focusRunId === item.runId ? 3 : 2,
        title: item.label,
        priceFormat: { type: "custom", formatter: (value: number) => value.toFixed(2) },
      });
      line.setData(item.points.map((point) => ({ time: point.date as Time, value: point.value })));
      chartSeries.push(line);
      for (const point of item.points) {
        if (!valuesByTime.has(point.date)) valuesByTime.set(point.date, { series: line, value: point.value });
      }
    }
    chart.timeScale().fitContent();
    const crosshairHandler = (event: { time?: Time }) => {
      if (typeof event.time !== "string") {
        setHover(null);
        return;
      }
      setHover({ date: event.time, rows: comparisonTooltipRows(allSeries, event.time, hiddenRunIds, focusRunId) });
    };
    chart.subscribeCrosshairMove(crosshairHandler);
    const disposeSync = onReady({ chart, series: chartSeries, valuesByTime, isUserViewportChange: gestures.isUserViewportChange });
    return () => {
      disposeSync();
      gestures.dispose();
      observer?.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.remove();
    };
  }, [allSeries, focusRunId, hiddenRunIds, onReady, visibleSeries]);

  return <section className="comparison-chart" aria-label="Multi-strategy TWR comparison" data-series-count={visibleSeries.length}>
    <div className="financial-chart-heading"><div><h3>Strategy Performance (TWR)</h3><span className="muted">Backend base-100 wealth index · exact trading dates</span></div><span className="chart-unit">BASE 100</span></div>
    {visibleSeries.length === 0
      ? <div className="comparison-empty" role="status">No visible canonical TWR series.</div>
      : <div className="comparison-chart-canvas" ref={containerRef} />}
    {hover && <div className="financial-chart-tooltip comparison-tooltip" role="status"><strong>{hover.date}</strong>{hover.rows.map((row) => <span key={row.runId}><i style={{ background: row.color }} />{row.label}: {row.value === null ? "N/A" : row.value.toFixed(2)}</span>)}</div>}
  </section>;
}
