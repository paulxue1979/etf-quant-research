import type { BacktestReport, BacktestReportSeries, BacktestReportSeriesName, BacktestReportSeriesPayload } from "./types";

export type ChartStatus = "loading" | "available" | "empty" | "not_available" | "not_evaluable" | "error";
export type ChartUnit = "USD" | "normalized" | "percent";

export interface ChartPoint {
  date: string;
  value: number;
}

export interface ChartSeries {
  key: string;
  label: string;
  unit: ChartUnit;
  color: string;
  lineType?: "line" | "step";
  points: ChartPoint[];
  status: ChartStatus;
  reason?: string;
}

export interface ChartBundle {
  portfolio: ChartSeries[];
  performance: ChartSeries[];
  drawdown: ChartSeries[];
  anchorDate: string | null;
  range: { from: string; to: string } | null;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const COLORS = {
  equity: "#6bd7d0",
  capital: "#f1c878",
  strategy: "#8cb8ff",
  benchmark: "#85d49a",
  drawdown: "#ff8b8b",
  benchmarkDrawdown: "#b993f7",
};

export function parseReportDate(value: unknown): string | null {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value ? null : value;
}

export function toChartPoints(value: unknown): ChartPoint[] {
  if (!Array.isArray(value)) return [];
  const points = value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const date = parseReportDate("date" in item ? item.date : null);
    const numeric = "value" in item ? Number(item.value) : NaN;
    return date && Number.isFinite(numeric) ? [{ date, value: numeric }] : [];
  });
  return [...new Map(points.map((point) => [point.date, point])).values()].sort((a, b) => a.date.localeCompare(b.date));
}

function seriesStatus(value: BacktestReportSeriesPayload | undefined): { status: ChartStatus; reason?: string } {
  const status = value?.status;
  if (status === "not_available" || status === "not_evaluable") return { status, reason: value?.reason ?? undefined };
  const points = toChartPoints(value?.points);
  return { status: points.length ? "available" : "empty" };
}

function reportSeries(report: BacktestReportSeries | null, name: BacktestReportSeriesName): BacktestReportSeriesPayload | undefined {
  return report?.series?.[name];
}

function makeSeries(
  key: string,
  label: string,
  unit: ChartUnit,
  color: string,
  raw: BacktestReportSeriesPayload | undefined,
  lineType?: "line" | "step",
): ChartSeries {
  const state = seriesStatus(raw);
  return { key, label, unit, color, lineType, points: toChartPoints(raw?.points), ...state };
}

export function benchmarkLabel(report: BacktestReport | null): string {
  const summary = report?.summary?.benchmark;
  if (summary && typeof summary === "object" && "benchmark_symbol" in summary && typeof summary.benchmark_symbol === "string") {
    return `${summary.benchmark_symbol} Buy & Hold`;
  }
  const provenance = report?.provenance;
  if (provenance && typeof provenance === "object" && "benchmark_symbol" in provenance && typeof provenance.benchmark_symbol === "string") {
    return `${provenance.benchmark_symbol} Buy & Hold`;
  }
  return "Benchmark unavailable";
}

export function buildChartBundle(report: BacktestReport | null, series: BacktestReportSeries | null): ChartBundle {
  const equity = makeSeries("equity", "Portfolio Value", "USD", COLORS.equity, reportSeries(series, "equity"));
  const capital = makeSeries("capital", "Capital Invested", "USD", COLORS.capital, reportSeries(series, "capital"), "step");
  const strategy = makeSeries("twr", "Strategy TWR", "normalized", COLORS.strategy, reportSeries(series, "twr"));
  const benchmark = makeSeries("benchmark_twr", benchmarkLabel(report), "normalized", COLORS.benchmark, reportSeries(series, "benchmark_twr"));
  const drawdown = makeSeries("drawdown", "Strategy Drawdown", "percent", COLORS.drawdown, reportSeries(series, "drawdown"));
  const benchmarkDrawdown = makeSeries("benchmark_drawdown", `${benchmarkLabel(report)} Drawdown`, "percent", COLORS.benchmarkDrawdown, reportSeries(series, "benchmark_drawdown"));
  const allSeries = [equity, capital, strategy, benchmark, drawdown, benchmarkDrawdown];
  const dates = [...new Set(allSeries.flatMap((item) => item.points.map((point) => point.date)))].sort();
  return {
    portfolio: [equity, capital],
    performance: [strategy, benchmark],
    drawdown: [drawdown, benchmarkDrawdown],
    anchorDate: dates.at(-1) ?? null,
    range: dates.length ? { from: dates[0], to: dates.at(-1) ?? dates[0] } : null,
  };
}

function subtractYears(date: string, years: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCFullYear(value.getUTCFullYear() - years);
  return value.toISOString().slice(0, 10);
}

export function rangeForPreset(bundle: ChartBundle, preset: "1Y" | "3Y" | "5Y" | "MAX"): { from: string; to: string } | null {
  if (!bundle.range) return null;
  if (preset === "MAX") return bundle.range;
  const from = subtractYears(bundle.range.to, Number(preset.slice(0, -1)));
  return { from: from < bundle.range.from ? bundle.range.from : from, to: bundle.range.to };
}

export function formatChartValue(value: number, unit: ChartUnit): string {
  if (!Number.isFinite(value)) return "N/A";
  if (unit === "USD") return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  if (unit === "percent") return `${(value * 100).toFixed(2)}%`;
  return value.toFixed(2);
}

export function availabilityText(series: ChartSeries): string {
  if (series.status === "available") return "AVAILABLE";
  if (series.status === "empty") return "EMPTY";
  if (series.status === "not_evaluable") return "NOT EVALUABLE";
  if (series.status === "not_available") return "NOT AVAILABLE";
  if (series.status === "loading") return "LOADING";
  return "ERROR";
}
