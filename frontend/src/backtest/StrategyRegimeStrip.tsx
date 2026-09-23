import { useEffect, useRef, useState } from "react";
import { ColorType, CrosshairMode, HistogramSeries, createChart, type Time } from "lightweight-charts";

import type { ChartStatus, RegimePoint } from "./reportCharts";
import { configureResponsiveMinBarSpacing, trackViewportGestures, type SyncedChart, type SyncSeries } from "./chartSync";

interface StrategyRegimeStripProps {
  points: RegimePoint[];
  status: ChartStatus;
  reason?: string;
  height: number;
  onReady: (chart: SyncedChart) => () => void;
}

function allocationText(allocation: Record<string, number>): string {
  const entries = Object.entries(allocation).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) return "Cash 100.00%";
  return entries.map(([symbol, value]) => `${symbol} ${(value * 100).toFixed(2)}%`).join(" · ");
}

function statusText(status: ChartStatus): string {
  if (status === "not_available") return "NOT AVAILABLE";
  if (status === "not_evaluable") return "NOT EVALUABLE";
  if (status === "loading") return "LOADING";
  if (status === "error") return "ERROR";
  return "EMPTY";
}

export function StrategyRegimeStrip({ points, status, reason, height, onReady }: StrategyRegimeStripProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<RegimePoint | null>(null);

  useEffect(() => {
    if (!containerRef.current || !points.length) return undefined;
    const container = containerRef.current;
    const viewportGestures = trackViewportGestures(container);
    const chart = createChart(container, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#0f181d" }, textColor: "#8c9aa4", attributionLogo: false },
      grid: { vertLines: { color: "#1d2a31" }, horzLines: { visible: false } },
      crosshair: { mode: CrosshairMode.Normal, horzLine: { visible: false, labelVisible: false } },
      rightPriceScale: { visible: false },
      timeScale: { borderColor: "#2b3942", timeVisible: false, rightOffset: 4, minBarSpacing: 0.1 },
    });
    const resize = () => {
      const width = container.clientWidth;
      if (width > 0) {
        chart.resize(width, height);
        configureResponsiveMinBarSpacing(chart, points.length);
      }
    };
    resize();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    resizeObserver?.observe(container);
    const strip = chart.addSeries(HistogramSeries, {
      base: 0,
      priceScaleId: "",
      priceFormat: { type: "custom", formatter: () => "" },
    });
    strip.setData(points.map((point) => ({ time: point.date as Time, value: 1, color: point.color })));
    const pointsByDate = new Map(points.map((point) => [point.date, point]));
    const crosshairHandler = (event: { time?: Time }) => {
      setHover(typeof event.time === "string" ? pointsByDate.get(event.time) ?? null : null);
    };
    chart.subscribeCrosshairMove(crosshairHandler);
    const syncSeries = strip as SyncSeries;
    const valuesByTime = new Map(points.map((point) => [point.date, { series: syncSeries, value: 1 }]));
    const disposeSync = onReady({
      chart,
      series: [syncSeries],
      valuesByTime,
      isUserViewportChange: viewportGestures.isUserViewportChange,
      clearPendingViewportGesture: viewportGestures.clearPendingViewportGesture,
    });
    return () => {
      disposeSync();
      viewportGestures.dispose();
      resizeObserver?.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.remove();
    };
  }, [height, onReady, points]);

  return <section className="financial-chart regime-strip" aria-label="Strategy Regime">
    <div className="financial-chart-heading"><div><h3>Strategy Regime</h3><span className="muted">Decision provenance and stateful target continuity</span></div><span className="chart-unit">DECISION</span></div>
    <div className="financial-chart-canvas" style={{ minHeight: height }} ref={containerRef} />
    {!points.length && <div className={`financial-chart-state ${status}`} role="status">{statusText(status)}{reason ? ` · ${reason}` : ""}</div>}
    {hover && <div className="financial-chart-tooltip" role="status">
      <strong>{hover.date}</strong>
      <span>Source: {hover.source}</span>
      <span>Rule: {hover.label}</span>
      <span>Regime: {hover.allocationLabel}</span>
      <span>Target: {allocationText(hover.targetAllocation)}</span>
      {hover.isContinuity && <span>Stateful continuity: previous target retained</span>}
    </div>}
    <div className="regime-legend" aria-label="Strategy regime legend">
      <span><i className="rule-match" />Rule Match</span>
      <span><i className="fallback" />Fallback</span>
      <span><i className="hold-previous" />Hold Previous</span>
      <small>Labels and allocation details remain available on hover.</small>
    </div>
  </section>;
}
