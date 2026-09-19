import type { ComparisonMetricValue, ComparisonRunIdentity } from "./types";

export type ComparisonMetricKey =
  | "cagr"
  | "total_twr_return"
  | "max_drawdown"
  | "sharpe_ratio"
  | "sortino_ratio"
  | "calmar_ratio"
  | "exposure"
  | "portfolio_turnover"
  | "xirr"
  | "trade_count"
  | "holding_period_count";

export type ComparisonMetricGroup = "Strategy Performance" | "Strategy Behavior" | "Investor Experience";

export interface ComparisonMetricDefinition {
  key: ComparisonMetricKey;
  label: string;
  group: ComparisonMetricGroup;
  format: "percent" | "ratio" | "count" | "exposure";
  sortable: boolean;
}

export const COMPARISON_METRICS: ComparisonMetricDefinition[] = [
  { key: "cagr", label: "CAGR", group: "Strategy Performance", format: "percent", sortable: true },
  { key: "total_twr_return", label: "Total TWR Return", group: "Strategy Performance", format: "percent", sortable: false },
  { key: "max_drawdown", label: "Max Drawdown", group: "Strategy Performance", format: "percent", sortable: true },
  { key: "sharpe_ratio", label: "Sharpe", group: "Strategy Performance", format: "ratio", sortable: true },
  { key: "sortino_ratio", label: "Sortino", group: "Strategy Performance", format: "ratio", sortable: false },
  { key: "calmar_ratio", label: "Calmar", group: "Strategy Performance", format: "ratio", sortable: true },
  { key: "exposure", label: "Exposure", group: "Strategy Behavior", format: "exposure", sortable: true },
  { key: "portfolio_turnover", label: "Turnover", group: "Strategy Behavior", format: "percent", sortable: true },
  { key: "trade_count", label: "Trade Count", group: "Strategy Behavior", format: "count", sortable: false },
  { key: "holding_period_count", label: "Holding Period Count", group: "Strategy Behavior", format: "count", sortable: false },
  { key: "xirr", label: "XIRR", group: "Investor Experience", format: "percent", sortable: true },
];

export function comparisonMetricNumber(metric: ComparisonMetricValue | undefined, format: ComparisonMetricDefinition["format"]): number | null {
  if (!metric || metric.status !== "available") return null;
  const value = format === "exposure" && metric.value && typeof metric.value === "object"
    ? (metric.value as Record<string, unknown>).average_gross_exposure
    : metric.value;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function formatComparisonMetric(metric: ComparisonMetricValue | undefined, definition: ComparisonMetricDefinition): string {
  const numeric = comparisonMetricNumber(metric, definition.format);
  if (numeric === null) return "N/A";
  if (definition.format === "count") return Math.round(numeric).toLocaleString();
  if (definition.format === "ratio") return numeric.toFixed(2);
  return `${(numeric * 100).toFixed(2)}%`;
}

export function comparisonMetricReason(metric: ComparisonMetricValue | undefined, definition: ComparisonMetricDefinition): string | undefined {
  if (comparisonMetricNumber(metric, definition.format) !== null) return undefined;
  return metric?.reason ?? (definition.format === "exposure" ? "Canonical average gross exposure is unavailable." : undefined);
}

export function sortComparisonRuns(
  runs: ComparisonRunIdentity[],
  definition: ComparisonMetricDefinition | null,
  order: "asc" | "desc",
): ComparisonRunIdentity[] {
  if (!definition) return runs;
  const direction = order === "asc" ? 1 : -1;
  return [...runs].sort((left, right) => {
    const leftValue = comparisonMetricNumber(left.metrics[definition.key], definition.format);
    const rightValue = comparisonMetricNumber(right.metrics[definition.key], definition.format);
    if (leftValue === null) return rightValue === null ? 0 : 1;
    if (rightValue === null) return -1;
    return (leftValue - rightValue) * direction;
  });
}
