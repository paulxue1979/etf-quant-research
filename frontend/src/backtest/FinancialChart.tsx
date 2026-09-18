import { useEffect, useRef, useState } from "react";
import { ColorType, CrosshairMode, LineSeries, LineType, createChart, type ISeriesApi, type Time } from "lightweight-charts";

import { availabilityText, formatChartValue, type ChartSeries } from "./reportCharts";
import type { SyncedChart } from "./chartSync";

interface FinancialChartProps {
  title: string;
  description: string;
  series: ChartSeries[];
  height: number;
  onReady: (chart: SyncedChart) => () => void;
}

function ChartState({ series }: { series: ChartSeries[] }) {
  if (series.some((item) => item.status === "available")) return null;
  const state = series[0];
  const status = state?.status ?? "empty";
  return <div className={`financial-chart-state ${status}`} role="status">{state ? availabilityText(state) : "EMPTY"}{state?.reason ? ` · ${state.reason}` : ""}</div>;
}

export function FinancialChart({ title, description, series, height, onReady }: FinancialChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ date: string; values: Array<{ label: string; value: string; color: string }> } | null>(null);
  useEffect(() => {
    if (!containerRef.current || !series.some((item) => item.points.length)) return undefined;
    const container = containerRef.current;
    const chart = createChart(container, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#0f181d" }, textColor: "#8c9aa4", attributionLogo: false },
      grid: { vertLines: { color: "#1d2a31" }, horzLines: { color: "#1d2a31" } },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: { borderColor: "#2b3942" },
      timeScale: { borderColor: "#2b3942", timeVisible: false, rightOffset: 4 },
    });
    const resize = () => {
      const width = container.clientWidth;
      if (width > 0) chart.resize(width, height);
    };
    resize();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    resizeObserver?.observe(container);
    const chartSeries: ISeriesApi<"Line">[] = [];
    const valuesByTime = new Map<string, { series: ISeriesApi<"Line">; value: number }>();
    const byTime = new Map<string, Array<{ label: string; value: string; color: string }>>();
    for (const item of series) {
      if (item.status !== "available" || !item.points.length) continue;
      const line = chart.addSeries(LineSeries, {
        color: item.color,
        lineWidth: 2,
        lineType: item.lineType === "step" ? LineType.WithSteps : LineType.Simple,
        title: item.label,
        priceFormat: item.unit === "USD" ? { type: "custom", formatter: (value: number) => formatChartValue(value, item.unit) } : { type: "custom", formatter: (value: number) => formatChartValue(value, item.unit) },
      });
      line.setData(item.points.map((point) => ({ time: point.date as Time, value: point.value })));
      chartSeries.push(line);
      for (const point of item.points) {
        if (!valuesByTime.has(point.date)) valuesByTime.set(point.date, { series: line, value: point.value });
        const existing = byTime.get(point.date) ?? [];
        existing.push({ label: item.label, value: formatChartValue(point.value, item.unit), color: item.color });
        byTime.set(point.date, existing);
      }
    }
    chart.timeScale().fitContent();
    const crosshairHandler = (event: { time?: Time; point?: { x: number; y: number } | null }) => {
      const time = event.time;
      if (typeof time !== "string") { setHover(null); return; }
      const available = new Map((byTime.get(time) ?? []).map((item) => [item.label, item]));
      setHover({
        date: time,
        values: series.map((item) => available.get(item.label) ?? { label: item.label, value: "N/A", color: item.color }),
      });
    };
    chart.subscribeCrosshairMove(crosshairHandler);
    const disposeSync = onReady({ chart, series: chartSeries, valuesByTime });
    return () => {
      disposeSync();
      resizeObserver?.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.remove();
    };
  }, [height, onReady, series]);

  return <section className="financial-chart" aria-label={title}>
    <div className="financial-chart-heading"><div><h3>{title}</h3><span className="muted">{description}</span></div><span className="chart-unit">{series[0]?.unit ?? "-"}</span></div>
    <div className="financial-chart-canvas" style={{ minHeight: height }} ref={containerRef} />
    <ChartState series={series} />
    {hover && <div className="financial-chart-tooltip" role="status"><strong>{hover.date}</strong>{hover.values.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}: {item.value}</span>)}</div>}
    <div className="financial-chart-legend">{series.map((item) => <span key={item.key} className={item.status !== "available" ? "is-unavailable" : undefined}><i style={{ background: item.color }} />{item.label}<small>{availabilityText(item)}</small></span>)}</div>
  </section>;
}
