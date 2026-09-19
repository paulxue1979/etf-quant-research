import type { ComparisonRunIdentity, ResearchComparison } from "./types";

export const COMPARISON_COLORS = [
  "#6bd7d0", "#ff8b8b", "#ffd166", "#77aaff", "#b993f7",
  "#85d49a", "#f39c6b", "#8fd3ff", "#d7a6e8", "#b6c96d",
] as const;

export interface ComparisonChartSeries {
  runId: string;
  label: string;
  color: string;
  status: "available" | "not_available" | "excluded";
  reason?: string;
  points: Array<{ date: string; value: number }>;
}

export function buildComparisonSeries(comparison: ResearchComparison): ComparisonChartSeries[] {
  const identities = new Map(comparison.runs.map((run) => [run.backtest_run_id, run]));
  return comparison.series.map((series, index) => ({
    runId: series.backtest_run_id,
    label: displayLabel(identities.get(series.backtest_run_id), series.backtest_run_id),
    color: COMPARISON_COLORS[index % COMPARISON_COLORS.length],
    status: series.twr.status,
    reason: series.twr.reason,
    points: series.twr.points,
  }));
}

function displayLabel(identity: ComparisonRunIdentity | undefined, fallback: string): string {
  return identity?.short_display_label?.trim() || fallback;
}

export function visibleComparisonSeries(
  series: ComparisonChartSeries[],
  hiddenRunIds: ReadonlySet<string>,
  focusRunId: string | null,
): ComparisonChartSeries[] {
  return series.filter((item) => (
    item.status === "available"
    && !hiddenRunIds.has(item.runId)
    && (focusRunId === null || item.runId === focusRunId)
  ));
}

export function comparisonTooltipRows(
  series: ComparisonChartSeries[],
  date: string,
  hiddenRunIds: ReadonlySet<string>,
  focusRunId: string | null,
): Array<{ runId: string; label: string; color: string; value: number | null }> {
  return visibleComparisonSeries(series, hiddenRunIds, focusRunId).map((item) => ({
    runId: item.runId,
    label: item.label,
    color: item.color,
    value: item.points.find((point) => point.date === date)?.value ?? null,
  }));
}

export function comparisonFullRange(series: ComparisonChartSeries[]): { from: string; to: string } | null {
  const dates = series.flatMap((item) => item.points.map((point) => point.date)).sort();
  return dates.length ? { from: dates[0], to: dates[dates.length - 1] } : null;
}

export function comparisonPresetRange(
  fullRange: { from: string; to: string } | null,
  preset: "1Y" | "3Y" | "5Y" | "MAX",
): { from: string; to: string } | null {
  if (!fullRange || preset === "MAX") return fullRange;
  const end = new Date(`${fullRange.to}T00:00:00Z`);
  end.setUTCFullYear(end.getUTCFullYear() - Number(preset.slice(0, -1)));
  const from = end.toISOString().slice(0, 10);
  return { from: from < fullRange.from ? fullRange.from : from, to: fullRange.to };
}
