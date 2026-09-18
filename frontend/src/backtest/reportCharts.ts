import type {
  AllocationTimeline,
  BacktestReport,
  BacktestReportSeries,
  BacktestReportSeriesName,
  BacktestReportSeriesPayload,
  StrategyProvenanceRecord,
  StrategyTimelineMarker,
} from "./types";

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

export interface RegimePoint {
  date: string;
  label: string;
  source: string;
  allocationLabel: string;
  targetAllocation: Record<string, number>;
  isContinuity: boolean;
  color: string;
}

export interface ChartMarker {
  date: string;
  kind: "signal" | "execution" | "contribution";
  label: string;
  color: string;
  details: string[];
}

export interface ChartBundle {
  portfolio: ChartSeries[];
  performance: ChartSeries[];
  drawdown: ChartSeries[];
  regime: RegimePoint[];
  regimeStatus: ChartStatus;
  regimeReason?: string;
  targetAllocation: ChartSeries[];
  actualAllocation: ChartSeries[];
  strategyMarkers: ChartMarker[];
  contributionMarkers: ChartMarker[];
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
  cash: "#aeb8c1",
  signal: "#f1c878",
  execution: "#6bd7d0",
  contribution: "#d2a956",
};
const ASSET_COLORS = ["#63c7c2", "#e3b85f", "#7fa8e8", "#d9879e", "#9fc45f", "#b997d9", "#d58e5c", "#75a9ad", "#c5cf72", "#e29170"];

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

function allocationStatus(timeline: AllocationTimeline | undefined): { status: ChartStatus; reason?: string } {
  if (timeline?.status === "not_available" || timeline?.status === "not_evaluable") {
    return { status: timeline.status, reason: timeline.reason };
  }
  return { status: timeline?.timeline?.length ? "available" : "empty" };
}

function allocationSeries(timeline: AllocationTimeline | undefined, kind: "target" | "actual"): ChartSeries[] {
  const state = allocationStatus(timeline);
  const label = kind === "target" ? "Target Allocation" : "Actual Allocation";
  if (state.status !== "available") {
    return [{ key: `${kind}-allocation`, label, unit: "percent", color: COLORS.cash, lineType: kind === "target" ? "step" : "line", points: [], ...state }];
  }
  const rows = (timeline?.timeline ?? []).flatMap((item) => {
    const date = parseReportDate(item?.date);
    return date ? [{ ...item, date }] : [];
  });
  const symbols = new Set((timeline?.asset_symbols ?? []).filter((symbol) => typeof symbol === "string" && symbol.trim()));
  for (const row of rows) for (const symbol of Object.keys(row.asset_weights ?? {})) symbols.add(symbol);
  const sortedSymbols = [...symbols].sort();
  const result = sortedSymbols.map((symbol, index): ChartSeries => ({
    key: `${kind}-${symbol}`,
    label: symbol,
    unit: "percent",
    color: ASSET_COLORS[index % ASSET_COLORS.length],
    lineType: kind === "target" ? "step" : "line",
    points: rows.flatMap((row) => {
      const value = Number(row.asset_weights?.[symbol] ?? 0);
      return Number.isFinite(value) ? [{ date: row.date, value }] : [];
    }),
    status: "available",
  }));
  result.push({
    key: `${kind}-Cash`,
    label: "Cash",
    unit: "percent",
    color: COLORS.cash,
    lineType: kind === "target" ? "step" : "line",
    points: rows.flatMap((row) => {
      const value = Number(row.cash_weight);
      return Number.isFinite(value) ? [{ date: row.date, value }] : [];
    }),
    status: "available",
  });
  return result;
}

function allocationLabel(record: StrategyProvenanceRecord, target: AllocationTimeline | undefined): string {
  const row = target?.timeline?.find((item) => item.date === record.signal_date);
  const active = Object.entries(record.target_allocation ?? {}).filter(([, weight]) => Number(weight) > 1e-12).map(([symbol]) => symbol).sort();
  if (!active.length) return "Cash";
  if (active.length > 1 || Number(row?.cash_weight ?? 0) > 1e-12) return "Mixed Allocation";
  return active[0];
}

function regimeLabel(record: StrategyProvenanceRecord): string {
  if (record.allocation_source === "hold_previous") return "Hold Previous";
  if (record.allocation_source === "fallback") return "Fallback";
  return record.matched_rule_id?.trim() || "Rule Match";
}

function regimeColor(source: string): string {
  if (source === "hold_previous") return "#7f97a3";
  if (source === "fallback") return "#d2a956";
  return "#58b9b4";
}

function regimePoints(report: BacktestReport | null): RegimePoint[] {
  const records = report?.strategy_provenance?.records;
  if (!Array.isArray(records)) return [];
  return records.flatMap((record) => {
    const date = parseReportDate(record?.signal_date);
    if (!date || typeof record?.allocation_source !== "string") return [];
    return [{
      date,
      label: regimeLabel(record),
      source: record.allocation_source,
      allocationLabel: allocationLabel(record, report?.allocations?.target),
      targetAllocation: record.target_allocation ?? {},
      isContinuity: record.allocation_source === "hold_previous",
      color: regimeColor(record.allocation_source),
    }];
  });
}

function formatAllocation(allocation: Record<string, number> | undefined): string {
  const entries = Object.entries(allocation ?? {}).filter(([, value]) => Number.isFinite(Number(value))).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) return "Cash 100.00%";
  return entries.map(([symbol, value]) => `${symbol} ${(Number(value) * 100).toFixed(2)}%`).join(" · ");
}

function signalMarker(marker: StrategyTimelineMarker, date: string): ChartMarker {
  const rule = marker.matched_rule_id?.trim() || (marker.allocation_source === "fallback" ? "Fallback" : "Rule Match");
  const omitted = marker.execution_status === "omitted" || !marker.execution_date;
  return {
    date,
    kind: "signal",
    label: `Signal · ${rule}`,
    color: COLORS.signal,
    details: [
      `Signal date: ${marker.signal_date ?? date}`,
      `Rule: ${rule}`,
      `Allocation source: ${marker.allocation_source ?? "N/A"}`,
      `Target: ${formatAllocation(marker.target_allocation)}`,
      omitted ? "Execution: Not executed" : `Expected execution: ${marker.execution_date}`,
      `Status: ${marker.execution_status ?? "N/A"}`,
    ],
  };
}

function executionMarker(marker: StrategyTimelineMarker, date: string): ChartMarker {
  const cause = marker.rebalance_cause?.toUpperCase() || "TARGET";
  const symbols = marker.symbols?.length ? marker.symbols.join(", ") : "N/A";
  return {
    date,
    kind: "execution",
    label: `Execution · ${cause}`,
    color: COLORS.execution,
    details: [
      `Execution date: ${marker.execution_date ?? date}`,
      `Related signal date: ${marker.signal_date ?? "N/A"}`,
      `Cause: ${cause}`,
      `Orders / fills: ${marker.order_count ?? 0} / ${marker.fill_count ?? 0}`,
      `Symbols: ${symbols}`,
    ],
  };
}

function strategyMarkers(report: BacktestReport | null): ChartMarker[] {
  const markers = report?.strategy_provenance?.markers;
  if (!Array.isArray(markers)) return [];
  return markers.flatMap((marker) => {
    const date = parseReportDate(marker?.date);
    if (!date) return [];
    if (marker.marker_type === "signal") return [signalMarker(marker, date)];
    if (marker.marker_type === "execution") return [executionMarker(marker, date)];
    return [];
  }).sort((left, right) => left.date.localeCompare(right.date) || left.kind.localeCompare(right.kind));
}

function contributionMarkers(report: BacktestReport | null): ChartMarker[] {
  if (report?.contribution_report?.status !== "available") return [];
  return report.contribution_report.events.flatMap((event) => {
    const effectiveDate = parseReportDate(event.effective_date);
    const amount = Number(event.amount);
    if (!effectiveDate || !Number.isFinite(amount)) return [];
    const formatted = amount.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return [{
      date: effectiveDate,
      kind: "contribution" as const,
      label: `External Cash Flow · ${formatted}`,
      color: COLORS.contribution,
      details: [
        `Requested date: ${event.requested_date}`,
        `Effective date: ${event.effective_date}`,
        `Amount: ${formatted}`,
        `Schedule: ${event.frequency}`,
        "Strategy signal: NONE",
        `Contribution rebalance: ${event.deployment.status === "rebalance_executed" ? "executed" : "not executed"}`,
      ],
    }];
  });
}

export function buildChartBundle(report: BacktestReport | null, series: BacktestReportSeries | null): ChartBundle {
  const equity = makeSeries("equity", "Portfolio Value", "USD", COLORS.equity, reportSeries(series, "equity"));
  const capital = makeSeries("capital", "Capital Invested", "USD", COLORS.capital, reportSeries(series, "capital"), "step");
  const strategy = makeSeries("twr", "Strategy TWR", "normalized", COLORS.strategy, reportSeries(series, "twr"));
  const benchmark = makeSeries("benchmark_twr", benchmarkLabel(report), "normalized", COLORS.benchmark, reportSeries(series, "benchmark_twr"));
  const drawdown = makeSeries("drawdown", "Strategy Drawdown", "percent", COLORS.drawdown, reportSeries(series, "drawdown"));
  const benchmarkDrawdown = makeSeries("benchmark_drawdown", `${benchmarkLabel(report)} Drawdown`, "percent", COLORS.benchmarkDrawdown, reportSeries(series, "benchmark_drawdown"));
  const regime = regimePoints(report);
  const targetAllocation = allocationSeries(report?.allocations?.target, "target");
  const actualAllocation = allocationSeries(report?.allocations?.actual, "actual");
  const markers = strategyMarkers(report);
  const capitalMarkers = contributionMarkers(report);
  const allSeries = [equity, capital, strategy, benchmark, drawdown, benchmarkDrawdown, ...targetAllocation, ...actualAllocation];
  const dates = [...new Set([
    ...allSeries.flatMap((item) => item.points.map((point) => point.date)),
    ...regime.map((point) => point.date),
    ...markers.map((marker) => marker.date),
    ...capitalMarkers.map((marker) => marker.date),
  ])].sort();
  const provenanceStatus = report?.strategy_provenance?.status;
  return {
    portfolio: [equity, capital],
    performance: [strategy, benchmark],
    drawdown: [drawdown, benchmarkDrawdown],
    regime,
    regimeStatus: provenanceStatus === "not_available" || provenanceStatus === "not_evaluable" ? provenanceStatus : regime.length ? "available" : "empty",
    regimeReason: report?.strategy_provenance?.reason,
    targetAllocation,
    actualAllocation,
    strategyMarkers: markers,
    contributionMarkers: capitalMarkers,
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
