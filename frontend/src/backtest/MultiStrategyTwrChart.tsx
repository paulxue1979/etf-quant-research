import { useEffect, useRef } from "react";
import { ColorType, CrosshairMode, LineSeries, createChart, type Time } from "lightweight-charts";

import { comparisonTooltipRows, type ComparisonChartSeries } from "./comparisonCharts";
import { trackViewportGestures, type SyncedChart, type SyncSeries } from "./chartSync";

interface MultiStrategyChartProps {
  kind: "twr" | "drawdown";
  allSeries: ComparisonChartSeries[];
  hiddenRunIds: ReadonlySet<string>;
  focusRunId: string | null;
  visibleSeries: ComparisonChartSeries[];
  hoverDate: string | null;
  onReady: (chart: SyncedChart) => () => void;
}

function displayValue(kind: MultiStrategyChartProps["kind"], value: number): string {
  return kind === "drawdown" ? `${(value * 100).toFixed(2)}%` : value.toFixed(2);
}

function MultiStrategyChart({ kind, allSeries, hiddenRunIds, focusRunId, visibleSeries, hoverDate, onReady }: MultiStrategyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const height = kind === "twr" ? 500 : 320;
  const title = kind === "twr" ? "Strategy Performance (TWR)" : "Drawdown";
  const ariaLabel = kind === "twr" ? "Multi-strategy TWR comparison" : "Multi-strategy drawdown comparison";
  const description = kind === "twr"
    ? "Backend base-100 wealth index · exact trading dates"
    : "Backend canonical drawdown path · exact trading dates";

  useEffect(() => {
    if (!containerRef.current || visibleSeries.length === 0) return undefined;
    const container = containerRef.current;
    const gestures = trackViewportGestures(container);
    const chart = createChart(container, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#0f181d" }, textColor: "#8c9aa4", attributionLogo: false },
      grid: { vertLines: { color: "#1d2a31" }, horzLines: { color: "#1d2a31" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2b3942" },
      timeScale: { borderColor: "#2b3942", timeVisible: false, rightOffset: 4 },
    });
    const resize = () => {
      if (container.clientWidth > 0) chart.resize(container.clientWidth, height);
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
        priceFormat: { type: "custom", formatter: (value: number) => displayValue(kind, value) },
      });
      line.setData(item.points.map((point) => ({ time: point.date as Time, value: point.value })));
      chartSeries.push(line);
      for (const point of item.points) {
        if (!valuesByTime.has(point.date)) valuesByTime.set(point.date, { series: line, value: point.value });
      }
    }
    chart.timeScale().fitContent();
    const disposeSync = onReady({ chart, series: chartSeries, valuesByTime, isUserViewportChange: gestures.isUserViewportChange });
    return () => {
      disposeSync();
      gestures.dispose();
      observer?.disconnect();
      chart.remove();
    };
  }, [focusRunId, height, kind, onReady, visibleSeries]);

  const hoverRows = hoverDate === null ? [] : comparisonTooltipRows(allSeries, hoverDate, hiddenRunIds, focusRunId);
  return <section className={`comparison-chart comparison-chart-${kind}`} aria-label={ariaLabel} data-series-count={visibleSeries.length} data-runs={visibleSeries.map((item) => item.runId).join(",")}>
    <div className="financial-chart-heading"><div><h3>{title}</h3><span className="muted">{description}</span></div><span className="chart-unit">{kind === "twr" ? "BASE 100" : "%"}</span></div>
    {visibleSeries.length === 0
      ? <div className="comparison-empty" role="status">No visible canonical {kind === "twr" ? "TWR" : "drawdown"} series.</div>
      : <div className="comparison-chart-canvas" style={{ minHeight: height }} ref={containerRef} />}
    {hoverDate && <div className="financial-chart-tooltip comparison-tooltip" role="status"><strong>{hoverDate}</strong>{hoverRows.map((row) => <span key={row.runId}><i style={{ background: row.color }} />{row.label}: {row.value === null ? "N/A" : displayValue(kind, row.value)}</span>)}</div>}
  </section>;
}

type SharedChartProps = Omit<MultiStrategyChartProps, "kind">;

export function MultiStrategyTwrChart(props: SharedChartProps) {
  return <MultiStrategyChart {...props} kind="twr" />;
}

export function MultiStrategyDrawdownChart(props: SharedChartProps) {
  return <MultiStrategyChart {...props} kind="drawdown" />;
}
