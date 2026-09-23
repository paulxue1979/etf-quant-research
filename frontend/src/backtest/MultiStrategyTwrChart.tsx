import { useEffect, useRef } from "react";
import { ColorType, CrosshairMode, LineSeries, LineType, createChart, type Time } from "lightweight-charts";

import { comparisonTooltipRows, type ComparisonChartSeries } from "./comparisonCharts";
import { configureResponsiveMinBarSpacing, trackViewportGestures, type SyncedChart, type SyncSeries } from "./chartSync";

interface MultiStrategyChartProps {
  kind: "twr" | "drawdown" | "portfolio_value" | "capital_invested" | "investment_profit";
  allSeries: ComparisonChartSeries[];
  hiddenRunIds: ReadonlySet<string>;
  focusRunId: string | null;
  visibleSeries: ComparisonChartSeries[];
  hoverDate: string | null;
  onReady: (chart: SyncedChart) => () => void;
}

function displayValue(kind: MultiStrategyChartProps["kind"], value: number): string {
  if (kind === "drawdown") return `${(value * 100).toFixed(2)}%`;
  if (kind === "twr") return value.toFixed(2);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}

function chartCopy(kind: MultiStrategyChartProps["kind"]) {
  if (kind === "twr") return { title: "Strategy Performance (TWR)", ariaLabel: "Multi-strategy TWR comparison", description: "Backend base-100 wealth index · exact trading dates", unit: "BASE 100" };
  if (kind === "drawdown") return { title: "Drawdown", ariaLabel: "Multi-strategy drawdown comparison", description: "Backend canonical drawdown path · exact trading dates", unit: "%" };
  if (kind === "portfolio_value") return { title: "Portfolio Value", ariaLabel: "Multi-strategy Portfolio Value comparison", description: "Canonical account value · actual USD · no rebasing", unit: "USD" };
  if (kind === "capital_invested") return { title: "Capital Invested", ariaLabel: "Multi-strategy Capital Invested comparison", description: "Initial capital plus effective external contributions · step path", unit: "USD" };
  return { title: "Investment Profit", ariaLabel: "Multi-strategy Investment Profit comparison", description: "Portfolio Value minus Capital Invested · dollar gain or loss", unit: "USD" };
}

function MultiStrategyChart({ kind, allSeries, hiddenRunIds, focusRunId, visibleSeries, hoverDate, onReady }: MultiStrategyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const height = kind === "twr" ? 500 : 320;
  const copy = chartCopy(kind);
  const emptyLabel = kind === "twr" ? "TWR" : kind === "drawdown" ? "drawdown" : copy.title;

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
      timeScale: { borderColor: "#2b3942", timeVisible: false, rightOffset: 4, minBarSpacing: 0.1 },
    });
    const pointCount = new Set(visibleSeries.flatMap((item) => item.points.map((point) => point.date))).size;
    const resize = () => {
      if (container.clientWidth > 0) {
        chart.resize(container.clientWidth, height);
        configureResponsiveMinBarSpacing(chart, pointCount);
      }
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
        lineType: kind === "capital_invested" ? LineType.WithSteps : LineType.Simple,
        priceFormat: { type: "custom", formatter: (value: number) => displayValue(kind, value) },
      });
      line.setData(item.points.map((point) => ({ time: point.date as Time, value: point.value })));
      chartSeries.push(line);
      for (const point of item.points) {
        if (!valuesByTime.has(point.date)) valuesByTime.set(point.date, { series: line, value: point.value });
      }
    }
    const disposeSync = onReady({
      chart,
      series: chartSeries,
      valuesByTime,
      isUserViewportChange: gestures.isUserViewportChange,
      clearPendingViewportGesture: gestures.clearPendingViewportGesture,
    });
    return () => {
      disposeSync();
      gestures.dispose();
      observer?.disconnect();
      chart.remove();
    };
  }, [focusRunId, height, kind, onReady, visibleSeries]);

  const hoverRows = hoverDate === null ? [] : comparisonTooltipRows(allSeries, hoverDate, hiddenRunIds, focusRunId);
  return <section className={`comparison-chart comparison-chart-${kind}`} aria-label={copy.ariaLabel} data-series-count={visibleSeries.length} data-runs={visibleSeries.map((item) => item.runId).join(",")}>
    <div className="financial-chart-heading"><div><h3>{copy.title}</h3><span className="muted">{copy.description}</span></div><span className="chart-unit">{copy.unit}</span></div>
    {visibleSeries.length === 0
      ? <div className="comparison-empty" role="status">No visible canonical {emptyLabel} series.</div>
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

export function MultiStrategyWealthChart(
  props: SharedChartProps & { kind: "portfolio_value" | "capital_invested" | "investment_profit" },
) {
  return <MultiStrategyChart {...props} />;
}
