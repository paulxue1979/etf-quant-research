import { useEffect, useRef, useState } from "react";
import { ColorType, CrosshairMode, LineSeries, LineType, createChart, createSeriesMarkers, type SeriesMarker, type Time } from "lightweight-charts";

import { availabilityText, formatChartValue, type ChartMarker, type ChartSeries } from "./reportCharts";
import { trackViewportGestures, type SyncedChart, type SyncSeries } from "./chartSync";

interface FinancialChartProps {
  title: string;
  description: string;
  series: ChartSeries[];
  markers?: ChartMarker[];
  height: number;
  onReady: (chart: SyncedChart) => () => void;
  showDollarDifference?: boolean;
}

function ChartState({ series }: { series: ChartSeries[] }) {
  if (series.some((item) => item.status === "available")) return null;
  const state = series[0];
  const status = state?.status ?? "empty";
  return <div className={`financial-chart-state ${status}`} role="status">{state ? availabilityText(state) : "EMPTY"}{state?.reason ? ` · ${state.reason}` : ""}</div>;
}

export function FinancialChart({ title, description, series, markers = [], height, onReady, showDollarDifference = false }: FinancialChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ date: string; values: Array<{ label: string; value: string; color: string }>; details: string[] } | null>(null);
  useEffect(() => {
    if (!containerRef.current || !series.some((item) => item.points.length)) return undefined;
    const container = containerRef.current;
    const viewportGestures = trackViewportGestures(container);
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
    const chartSeries: SyncSeries[] = [];
    const valuesByTime = new Map<string, { series: SyncSeries; value: number }>();
    const byTime = new Map<string, Array<{ label: string; value: string; color: string; raw: number }>>();
    const detailsByTime = new Map<string, string[]>();
    for (const marker of markers) {
      const current = detailsByTime.get(marker.date) ?? [];
      current.push(marker.label, ...marker.details);
      detailsByTime.set(marker.date, current);
    }
    let markerAnchor: SyncSeries | null = null;
    let markerDates: Set<string> | null = null;
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
      if (!markerAnchor) {
        markerAnchor = line;
        markerDates = new Set(item.points.map((point) => point.date));
      }
      for (const point of item.points) {
        if (!valuesByTime.has(point.date)) valuesByTime.set(point.date, { series: line, value: point.value });
        const existing = byTime.get(point.date) ?? [];
        existing.push({ label: item.label, value: formatChartValue(point.value, item.unit), color: item.color, raw: point.value });
        byTime.set(point.date, existing);
      }
    }
    if (markerAnchor && markerDates) {
      const visibleMarkers: SeriesMarker<Time>[] = markers.flatMap((marker) => markerDates?.has(marker.date) ? [{
        time: marker.date as Time,
        position: marker.kind === "signal" ? "aboveBar" as const : "belowBar" as const,
        shape: marker.kind === "signal" ? "arrowDown" as const : marker.kind === "execution" ? "arrowUp" as const : "circle" as const,
        color: marker.color,
        text: marker.kind === "signal" ? "S" : marker.kind === "execution" ? "E" : "C",
        size: 1,
      }] : []);
      createSeriesMarkers(markerAnchor, visibleMarkers);
    }
    chart.timeScale().fitContent();
    const crosshairHandler = (event: { time?: Time; point?: { x: number; y: number } | null }) => {
      const time = event.time;
      if (typeof time !== "string") { setHover(null); return; }
      const available = new Map((byTime.get(time) ?? []).map((item) => [item.label, item]));
      const values = series.map((item) => available.get(item.label) ?? { label: item.label, value: "N/A", color: item.color, raw: Number.NaN });
      const portfolioValue = available.get("Portfolio Value")?.raw;
      const capitalInvested = available.get("Capital Invested")?.raw;
      if (showDollarDifference && Number.isFinite(portfolioValue) && Number.isFinite(capitalInvested)) {
        const difference = Number(portfolioValue) - Number(capitalInvested);
        values.push({ label: "Dollar Profit", value: formatChartValue(difference, "USD"), color: "#d8e1e5", raw: difference });
      }
      setHover({
        date: time,
        values,
        details: detailsByTime.get(time) ?? [],
      });
    };
    chart.subscribeCrosshairMove(crosshairHandler);
    const disposeSync = onReady({
      chart,
      series: chartSeries,
      valuesByTime,
      isUserViewportChange: viewportGestures.isUserViewportChange,
    });
    return () => {
      disposeSync();
      viewportGestures.dispose();
      resizeObserver?.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.remove();
    };
  }, [height, markers, onReady, series, showDollarDifference]);

  return <section className="financial-chart" aria-label={title}>
    <div className="financial-chart-heading"><div><h3>{title}</h3><span className="muted">{description}</span></div><span className="chart-unit">{series[0]?.unit ?? "-"}</span></div>
    <div className="financial-chart-canvas" style={{ minHeight: height }} ref={containerRef} />
    <ChartState series={series} />
    {hover && <div className="financial-chart-tooltip" role="status"><strong>{hover.date}</strong>{hover.values.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}: {item.value}</span>)}{hover.details.map((detail, index) => <span className="marker-detail" key={`${detail}-${index}`}>{detail}</span>)}</div>}
    <div className="financial-chart-legend">{series.map((item) => <span key={item.key} className={item.status !== "available" ? "is-unavailable" : undefined}><i style={{ background: item.color }} />{item.label}<small>{availabilityText(item)}</small></span>)}</div>
    {markers.length > 0 && <div className="marker-legend" aria-label="Chart marker legend">{markers.some((item) => item.kind === "signal") && <span><i className="signal-marker">S</i>Signal at close</span>}{markers.some((item) => item.kind === "execution") && <span><i className="execution-marker">E</i>Execution at next open</span>}{markers.some((item) => item.kind === "contribution") && <span><i className="contribution-marker">C</i>External cash flow</span>}</div>}
  </section>;
}
