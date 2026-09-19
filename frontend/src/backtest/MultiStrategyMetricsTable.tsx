import { useMemo, useState } from "react";

import { COMPARISON_METRICS, comparisonMetricReason, formatComparisonMetric, sortComparisonRuns, type ComparisonMetricKey } from "./comparisonMetrics";
import type { ComparisonChartSeries } from "./comparisonCharts";
import type { ResearchComparison } from "./types";

export function MultiStrategyMetricsTable({ comparison, colors, hiddenRunIds, focusRunId }: {
  comparison: ResearchComparison;
  colors: ComparisonChartSeries[];
  hiddenRunIds: ReadonlySet<string>;
  focusRunId: string | null;
}) {
  const [sortKey, setSortKey] = useState<ComparisonMetricKey | null>(null);
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const definition = COMPARISON_METRICS.find((item) => item.key === sortKey) ?? null;
  const runs = useMemo(
    () => sortComparisonRuns(comparison.runs, definition, sortOrder),
    [comparison.runs, definition, sortOrder],
  );
  const colorsByRun = new Map(colors.map((item) => [item.runId, item.color]));
  const sortBy = (key: ComparisonMetricKey) => {
    if (sortKey === key) setSortOrder((current) => current === "desc" ? "asc" : "desc");
    else {
      setSortKey(key);
      setSortOrder("desc");
    }
  };

  return <section className="comparison-metrics" aria-label="Multi-strategy metrics comparison">
    <div className="subsection-heading"><div><span className="eyebrow">CANONICAL ANALYTICS</span><h3>Metrics comparison</h3></div><span className="muted">Sorting is exploratory; no strategy is selected automatically.</span></div>
    <div className="table-scroll comparison-metrics-scroll">
      <table className="comparison-metrics-table">
        <thead><tr><th className="strategy-column" scope="col">Strategy</th>{COMPARISON_METRICS.map((metric) => <th key={metric.key} data-group={metric.group} scope="col"><button type="button" disabled={!metric.sortable} onClick={() => sortBy(metric.key)} aria-label={`Sort by ${metric.label}`}>{metric.label}{sortKey === metric.key ? <span aria-hidden="true"> {sortOrder === "desc" ? "↓" : "↑"}</span> : null}</button><small>{metric.group}</small></th>)}</tr></thead>
        <tbody>{runs.map((run) => {
          const hidden = hiddenRunIds.has(run.backtest_run_id);
          const focused = focusRunId === run.backtest_run_id;
          return <tr key={run.backtest_run_id} className={`${hidden ? "is-hidden" : ""} ${focused ? "is-focused" : ""}`}>
            <th className="strategy-column" scope="row"><i style={{ background: colorsByRun.get(run.backtest_run_id) }} /><span>{run.short_display_label}</span>{hidden && <small>Hidden</small>}{focused && <small>Focus</small>}</th>
            {COMPARISON_METRICS.map((metric) => {
              const value = run.metrics[metric.key];
              const reason = comparisonMetricReason(value, metric);
              return <td key={metric.key} title={reason}>{formatComparisonMetric(value, metric)}{reason && <small>{reason}</small>}</td>;
            })}
          </tr>;
        })}</tbody>
      </table>
    </div>
    <p className="metrics-semantics"><strong>XIRR is investor experience.</strong> TWR and strategy-performance metrics remain separate canonical analytics.</p>
  </section>;
}
