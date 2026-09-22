import { useEffect, useMemo, useState } from "react";
import { ResearchApiError, researchApi } from "./api";
import type {
  OptimizationAnalysisPoint,
  OptimizationFilter,
  OptimizationHeatmap,
  OptimizationMetricDefinition,
  OptimizationMetricRegistry,
  OptimizationPareto,
  OptimizationResults,
  OptimizationSensitivity,
  OptimizationStability,
} from "./types";

interface OptimizationResultsExplorerProps {
  protocolId: string;
  experimentId: string;
}

type ExplorerView = "results" | "heatmap" | "pareto" | "stability" | "sensitivity";

const DEFAULT_METRICS = ["cagr", "max_drawdown", "sharpe_ratio", "calmar_ratio", "turnover", "trade_count"];

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function optionKey(value: unknown): string {
  return JSON.stringify(value);
}

function formatMetric(value: number | null, definition?: OptimizationMetricDefinition): string {
  if (value === null) return "N/A";
  if (definition?.format === "percent") return `${(value * 100).toFixed(2)}%`;
  if (definition?.format === "currency") return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
  if (definition?.format === "integer") return Math.round(value).toLocaleString();
  return value.toFixed(3);
}

function numericInput(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function parameters(results: OptimizationResults | null): string[] {
  return results?.parameter_space.parameters.map((item) => item.name) ?? [];
}

function parameterDomains(results: OptimizationResults | null): Record<string, unknown[]> {
  const domains: Record<string, unknown[]> = {};
  for (const name of parameters(results)) {
    const seen = new Map<string, unknown>();
    for (const candidate of results?.candidates ?? []) {
      const value = candidate.parameter_values[name];
      seen.set(optionKey(value), value);
    }
    domains[name] = [...seen.values()];
  }
  return domains;
}

function fixedSlice(
  names: string[],
  excluded: string[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(names.filter((name) => !excluded.includes(name)).map((name) => [name, values[name]]));
}

function MetricValue({ metricId, results, registry }: { metricId: string; results: OptimizationResults["candidates"][number]; registry: Map<string, OptimizationMetricDefinition> }) {
  const metric = results.metrics[metricId];
  if (!metric || metric.status !== "available") return <span className="metric-unavailable" title={metric?.reason ?? "Metric unavailable"}>N/A</span>;
  return <span>{formatMetric(metric.value, registry.get(metricId))}</span>;
}

function CandidateTable({ results, registry }: { results: OptimizationResults; registry: Map<string, OptimizationMetricDefinition> }) {
  return (
    <div className="table-scroll optimization-table-scroll">
      <table className="data-table optimization-table">
        <thead><tr><th>Candidate</th><th>Parameters</th>{DEFAULT_METRICS.map((id) => <th key={id}>{registry.get(id)?.display_name ?? id}</th>)}<th>Status</th></tr></thead>
        <tbody>{results.candidates.map((candidate) => <tr key={candidate.candidate_id}>
          <td><strong>#{candidate.candidate_index}</strong><small>{candidate.candidate_id.slice(0, 12)}</small></td>
          <td><code>{Object.entries(candidate.parameter_values).map(([key, value]) => `${key}=${displayValue(value)}`).join(" · ")}</code></td>
          {DEFAULT_METRICS.map((id) => <td key={id}><MetricValue metricId={id} results={candidate} registry={registry} /></td>)}
          <td><span className={`status-pill ${candidate.result_status === "failed" ? "is-error" : "is-valid"}`}>{candidate.result_status}</span>{candidate.failure_code && <small>{candidate.failure_code}</small>}</td>
        </tr>)}</tbody>
      </table>
    </div>
  );
}

function HeatmapView({ heatmap }: { heatmap: OptimizationHeatmap }) {
  const available = heatmap.cells.map((cell) => cell.metric.value).filter((value): value is number => value !== null && Number.isFinite(value));
  const minimum = available.length ? Math.min(...available) : 0;
  const maximum = available.length ? Math.max(...available) : 0;
  return <div className="optimization-heatmap" style={{ gridTemplateColumns: `90px repeat(${heatmap.x_values.length}, minmax(86px, 1fr))` }} data-testid="optimization-heatmap">
    <div className="heatmap-corner">{heatmap.y_parameter} / {heatmap.x_parameter}</div>
    {heatmap.x_values.map((value) => <div className="heatmap-axis" key={optionKey(value)}>{displayValue(value)}</div>)}
    {heatmap.y_values.flatMap((yValue) => [
      <div className="heatmap-axis" key={`axis-${optionKey(yValue)}`}>{displayValue(yValue)}</div>,
      ...heatmap.x_values.map((xValue) => {
        const cell = heatmap.cells.find((item) => optionKey(item.x_value) === optionKey(xValue) && optionKey(item.y_value) === optionKey(yValue));
        const intensity = !cell || cell.metric.value === null || maximum === minimum ? 0.5 : (cell.metric.value - minimum) / (maximum - minimum);
        const style = cell?.metric.status === "available" ? { backgroundColor: `rgba(54, 179, 162, ${0.2 + intensity * 0.65})` } : undefined;
        const title = cell ? `Candidate ${cell.candidate_index ?? "N/A"}; ${cell.metric.reason ?? cell.metric.status}` : "Empty";
        return <div className={`heatmap-cell is-${cell?.cell_status ?? "pruned"}`} key={`${optionKey(xValue)}-${optionKey(yValue)}`} style={style} title={title}>
          <strong>{cell?.metric.status === "available" ? formatMetric(cell.metric.value, heatmap.metric) : cell?.cell_status ?? "pruned"}</strong>
          <small>{cell?.candidate_index === null || cell?.candidate_index === undefined ? "No candidate" : `#${cell.candidate_index}`}</small>
        </div>;
      }),
    ])}
  </div>;
}

function ParetoView({ pareto }: { pareto: OptimizationPareto }) {
  const xId = pareto.objectives[0].metric_id;
  const yId = pareto.objectives[1].metric_id;
  const all = [...pareto.frontier.map((item) => ({ ...item, frontier: true })), ...pareto.dominated.map((item) => ({ ...item, frontier: false }))];
  const xs = all.map((item) => item.metrics[xId].value).filter((value): value is number => value !== null);
  const ys = all.map((item) => item.metrics[yId].value).filter((value): value is number => value !== null);
  const scale = (value: number, values: number[], reverse = false) => {
    const min = Math.min(...values); const max = Math.max(...values); const fraction = max === min ? 0.5 : (value - min) / (max - min);
    return 28 + (reverse ? 1 - fraction : fraction) * 244;
  };
  return <div className="pareto-plot" data-testid="pareto-plot">
    <svg viewBox="0 0 300 300" role="img" aria-label="Pareto frontier scatter plot">
      <line x1="28" y1="272" x2="282" y2="272" /><line x1="28" y1="18" x2="28" y2="272" />
      {all.map((item) => {
        const x = item.metrics[xId].value; const y = item.metrics[yId].value;
        if (x === null || y === null) return null;
        return <circle key={item.candidate_id} cx={scale(x, xs)} cy={scale(y, ys, true)} r={item.frontier ? 6 : 4} className={item.frontier ? "is-frontier" : "is-dominated"}><title>#{item.candidate_index} {xId}={x} {yId}={y}</title></circle>;
      })}
    </svg>
    <div className="pareto-legend"><span><i className="frontier-dot" />Frontier {pareto.frontier.length}</span><span><i className="dominated-dot" />Dominated {pareto.dominated.length}</span><span>Excluded {pareto.excluded_count}</span></div>
  </div>;
}

function StabilityView({ stability }: { stability: OptimizationStability }) {
  return <div className="stability-layout" data-testid="stability-panel">
    <dl className="optimization-statistics">{Object.entries(stability.statistics).map(([name, value]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{value === null ? "N/A" : Number(value).toFixed(4)}</dd></div>)}</dl>
    <div className="table-scroll"><table className="data-table"><thead><tr><th>Parameter</th><th>Step</th><th>Candidate</th><th>Metric</th><th>Status</th></tr></thead><tbody>{stability.neighbors.map((neighbor) => <tr key={`${neighbor.changed_parameter}-${optionKey(neighbor.to_value)}`}><td>{neighbor.changed_parameter}</td><td>{displayValue(neighbor.from_value)} → {displayValue(neighbor.to_value)}</td><td>{neighbor.candidate_index === null ? "N/A" : `#${neighbor.candidate_index}`}</td><td>{formatMetric(neighbor.metric.value, stability.metric)}</td><td>{neighbor.status}</td></tr>)}</tbody></table></div>
  </div>;
}

function SensitivityView({ sensitivity }: { sensitivity: OptimizationSensitivity }) {
  const metricId = sensitivity.metrics[0].metric_id;
  const values = sensitivity.points.map((point) => point.metrics[metricId]?.value ?? null);
  const available = values.filter((value): value is number => value !== null);
  const min = available.length ? Math.min(...available) : 0; const max = available.length ? Math.max(...available) : 0;
  const point = (value: number, index: number) => ({ x: 24 + (sensitivity.points.length <= 1 ? 0.5 : index / (sensitivity.points.length - 1)) * 252, y: 18 + (max === min ? 0.5 : 1 - (value - min) / (max - min)) * 124 });
  return <div className="sensitivity-view" data-testid="sensitivity-chart">
    <svg viewBox="0 0 300 170" role="img" aria-label="Parameter sensitivity chart">
      {values.slice(0, -1).map((value, index) => { const next = values[index + 1]; if (value === null || next === null) return null; const a = point(value, index); const b = point(next, index + 1); return <line key={index} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="sensitivity-segment" />; })}
      {values.map((value, index) => { if (value === null) return null; const location = point(value, index); return <circle key={index} cx={location.x} cy={location.y} r="5"><title>{displayValue(sensitivity.points[index].parameter_value)}: {value}</title></circle>; })}
    </svg>
    <div className="sensitivity-points">{sensitivity.points.map((item) => <span key={optionKey(item.parameter_value)} className={item.status === "available" ? "" : "is-gap"}><strong>{displayValue(item.parameter_value)}</strong>{item.status === "available" ? formatMetric(item.metrics[metricId]?.value ?? null, sensitivity.metrics[0]) : `Gap · ${item.status}`}</span>)}</div>
  </div>;
}

export function OptimizationResultsExplorer({ protocolId, experimentId }: OptimizationResultsExplorerProps) {
  const [registry, setRegistry] = useState<OptimizationMetricRegistry | null>(null);
  const [results, setResults] = useState<OptimizationResults | null>(null);
  const [view, setView] = useState<ExplorerView>("results");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minimumTrades, setMinimumTrades] = useState("");
  const [maximumTurnover, setMaximumTurnover] = useState("");
  const [minimumCagr, setMinimumCagr] = useState("");
  const [appliedFilter, setAppliedFilter] = useState<OptimizationFilter>({});
  const [xParameter, setXParameter] = useState("");
  const [yParameter, setYParameter] = useState("");
  const [metricId, setMetricId] = useState("cagr");
  const [secondaryMetricId, setSecondaryMetricId] = useState("max_drawdown");
  const [fixedValues, setFixedValues] = useState<Record<string, unknown>>({});
  const [centerCandidateId, setCenterCandidateId] = useState("");
  const [heatmap, setHeatmap] = useState<OptimizationHeatmap | null>(null);
  const [pareto, setPareto] = useState<OptimizationPareto | null>(null);
  const [stability, setStability] = useState<OptimizationStability | null>(null);
  const [sensitivity, setSensitivity] = useState<OptimizationSensitivity | null>(null);
  const metricMap = useMemo(() => new Map((registry?.metrics ?? []).map((item) => [item.metric_id, item])), [registry]);
  const names = parameters(results);
  const domains = parameterDomains(results);

  useEffect(() => {
    let active = true;
    setBusy(true); setError(null);
    Promise.all([researchApi.getOptimizationMetricRegistry(), researchApi.getOptimizationResults(protocolId, experimentId)])
      .then(([metricRegistry, researchResults]) => {
        if (!active) return;
        setRegistry(metricRegistry); setResults(researchResults);
        const parameterNames = parameters(researchResults);
        setXParameter(parameterNames[0] ?? ""); setYParameter(parameterNames[1] ?? parameterNames[0] ?? "");
        setFixedValues({ ...(researchResults.candidates[0]?.parameter_values ?? {}) });
        setCenterCandidateId(researchResults.candidates[0]?.candidate_id ?? "");
      })
      .catch((reason) => active && setError(reason instanceof ResearchApiError ? reason.message : "Optimization research could not be loaded."))
      .finally(() => active && setBusy(false));
    return () => { active = false; };
  }, [protocolId, experimentId]);

  async function applyFilters() {
    const filter: OptimizationFilter = {
      minimum_trade_count: numericInput(minimumTrades),
      maximum_turnover: numericInput(maximumTurnover),
      minimum_cagr: numericInput(minimumCagr),
    };
    setBusy(true); setError(null);
    try { const response = await researchApi.getOptimizationResults(protocolId, experimentId, filter); setResults(response); setAppliedFilter(filter); if (!response.candidates.some((item) => item.candidate_id === centerCandidateId)) setCenterCandidateId(response.candidates[0]?.candidate_id ?? ""); }
    catch (reason) { setError(reason instanceof ResearchApiError ? reason.message : "Filters could not be applied."); }
    finally { setBusy(false); }
  }

  async function analyze() {
    if (!results) return;
    setBusy(true); setError(null);
    try {
      if (view === "heatmap") setHeatmap(await researchApi.getOptimizationHeatmap(protocolId, experimentId, { x_parameter: xParameter, y_parameter: yParameter, metric_id: metricId, fixed_parameter_values: fixedSlice(names, [xParameter, yParameter], fixedValues), filter: appliedFilter }));
      if (view === "pareto") setPareto(await researchApi.getOptimizationPareto(protocolId, experimentId, [metricId, secondaryMetricId], appliedFilter));
      if (view === "stability") setStability(await researchApi.getOptimizationStability(protocolId, experimentId, centerCandidateId, metricId, appliedFilter));
      if (view === "sensitivity") setSensitivity(await researchApi.getOptimizationSensitivity(protocolId, experimentId, { parameter: xParameter, metric_ids: [metricId, secondaryMetricId], fixed_parameter_values: fixedSlice(names, [xParameter], fixedValues), filter: appliedFilter }));
    } catch (reason) { setError(reason instanceof ResearchApiError ? reason.message : "Optimization analysis could not be completed."); }
    finally { setBusy(false); }
  }

  const analysisMetrics = registry?.metrics.filter((item) => item.heatmap_eligible) ?? [];
  const paretoMetrics = registry?.metrics.filter((item) => item.pareto_eligible) ?? [];
  const fixedNames = view === "heatmap" ? names.filter((name) => name !== xParameter && name !== yParameter) : names.filter((name) => name !== xParameter);
  return <section className="panel optimization-explorer">
    <div className="section-header compact"><div><span className="eyebrow">IS OPTIMIZATION RESEARCH</span><h2>Parameter structure explorer</h2></div><span className="status-pill is-valid">IS ONLY</span></div>
    {error && <p className="research-error" role="alert">{error}</p>}
    {busy && !results && <p className="muted">Loading optimization summaries...</p>}
    {results && <>
      <div className="optimization-summary"><span>Source <strong>{results.source_count.toLocaleString()}</strong></span><span>Filtered <strong>{results.filtered_count.toLocaleString()}</strong></span><span>Range <strong>{results.is_start} to {results.is_end}</strong></span><span>Full runs <strong>Not loaded</strong></span></div>
      <div className="optimization-filters"><label>Minimum trades<input aria-label="Minimum trades" inputMode="numeric" value={minimumTrades} onChange={(event) => setMinimumTrades(event.target.value)} /></label><label>Maximum turnover<input aria-label="Maximum turnover" inputMode="decimal" value={maximumTurnover} onChange={(event) => setMaximumTurnover(event.target.value)} /></label><label>Minimum CAGR<input aria-label="Minimum CAGR" inputMode="decimal" value={minimumCagr} onChange={(event) => setMinimumCagr(event.target.value)} /></label><button className="button button-secondary" type="button" disabled={busy} onClick={() => void applyFilters()}>Apply filters</button></div>
      <div className="optimization-filter-context"><strong>Applied filters</strong><code>{JSON.stringify(appliedFilter)}</code></div>
      <div className="optimization-tabs" role="tablist">{(["results", "heatmap", "pareto", "stability", "sensitivity"] as ExplorerView[]).map((item) => <button key={item} role="tab" aria-selected={view === item} className={view === item ? "is-active" : ""} type="button" onClick={() => setView(item)}>{item}</button>)}</div>
      {view !== "results" && <div className="optimization-controls">
        {(view === "heatmap" || view === "sensitivity") && <label>{view === "heatmap" ? "X parameter" : "Sweep parameter"}<select aria-label={view === "heatmap" ? "X parameter" : "Sweep parameter"} value={xParameter} onChange={(event) => setXParameter(event.target.value)}>{names.map((name) => <option key={name}>{name}</option>)}</select></label>}
        {view === "heatmap" && <label>Y parameter<select aria-label="Y parameter" value={yParameter} onChange={(event) => setYParameter(event.target.value)}>{names.map((name) => <option key={name}>{name}</option>)}</select></label>}
        <label>{view === "pareto" ? "X objective" : "Metric"}<select aria-label={view === "pareto" ? "X objective" : "Metric"} value={metricId} onChange={(event) => setMetricId(event.target.value)}>{(view === "pareto" ? paretoMetrics : analysisMetrics).map((metric) => <option value={metric.metric_id} key={metric.metric_id}>{metric.display_name}</option>)}</select></label>
        {(view === "pareto" || view === "sensitivity") && <label>{view === "pareto" ? "Y objective" : "Additional metric"}<select aria-label={view === "pareto" ? "Y objective" : "Additional metric"} value={secondaryMetricId} onChange={(event) => setSecondaryMetricId(event.target.value)}>{(view === "pareto" ? paretoMetrics : analysisMetrics).map((metric) => <option value={metric.metric_id} key={metric.metric_id}>{metric.display_name}</option>)}</select></label>}
        {view === "stability" && <label>Center candidate<select aria-label="Center candidate" value={centerCandidateId} onChange={(event) => setCenterCandidateId(event.target.value)}>{results.candidates.map((candidate) => <option value={candidate.candidate_id} key={candidate.candidate_id}>#{candidate.candidate_index}</option>)}</select></label>}
        {(view === "heatmap" || view === "sensitivity") && fixedNames.map((name) => <label key={name}>Fix {name}<select aria-label={`Fix ${name}`} value={optionKey(fixedValues[name])} onChange={(event) => setFixedValues((current) => ({ ...current, [name]: domains[name].find((value) => optionKey(value) === event.target.value) }))}>{domains[name].map((value) => <option key={optionKey(value)} value={optionKey(value)}>{displayValue(value)}</option>)}</select></label>)}
        <button className="button button-primary" type="button" disabled={busy || (view === "heatmap" && xParameter === yParameter) || (view === "pareto" && metricId === secondaryMetricId)} onClick={() => void analyze()}>{busy ? "Analyzing..." : "Analyze"}</button>
      </div>}
      {view === "results" && <CandidateTable results={results} registry={metricMap} />}
      {view === "heatmap" && heatmap && <><p className="optimization-context">Slice: {JSON.stringify(heatmap.fixed_parameter_values)} · Aggregation: NONE · Interpolation: OFF</p><HeatmapView heatmap={heatmap} /></>}
      {view === "pareto" && pareto && <ParetoView pareto={pareto} />}
      {view === "stability" && stability && <StabilityView stability={stability} />}
      {view === "sensitivity" && sensitivity && <><p className="optimization-context">Slice: {JSON.stringify(sensitivity.fixed_parameter_values)} · Gaps are not interpolated</p><SensitivityView sensitivity={sensitivity} /></>}
    </>}
  </section>;
}

export type { OptimizationResultsExplorerProps, OptimizationAnalysisPoint };
