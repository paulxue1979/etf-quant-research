import { useCallback, useEffect, useMemo, useState } from "react";

import type { BacktestMarkerReport, BacktestMarkerType, BacktestReport, BacktestReportSeries } from "./types";
import { ChartSyncController } from "./chartSync";
import { DEFAULT_MARKER_TYPES, MARKER_FILTERS, projectEventMarkers } from "./eventMarkers";
import { FinancialChart } from "./FinancialChart";
import { buildChartBundle, rangeForPreset } from "./reportCharts";
import { StrategyRegimeStrip } from "./StrategyRegimeStrip";

interface BacktestReportChartsProps {
  report: BacktestReport | null;
  series: BacktestReportSeries | null;
  markerReport?: BacktestMarkerReport | null;
  markerLoading?: boolean;
  markerError?: string | null;
  onMarkerTypesChange?: (types: BacktestMarkerType[]) => void;
  loading?: boolean;
  error?: string | null;
}

const presets = ["1Y", "3Y", "5Y", "MAX"] as const;
type RangeMode = typeof presets[number] | "CUSTOM";

export function BacktestReportCharts({ report, series, markerReport = null, markerLoading = false, markerError = null, onMarkerTypesChange, loading = false, error = null }: BacktestReportChartsProps) {
  const bundle = useMemo(() => buildChartBundle(report, series), [report, series]);
  const [selectedRange, setSelectedRange] = useState<RangeMode>("MAX");
  const [markerTypes, setMarkerTypes] = useState<Set<BacktestMarkerType>>(() => new Set(DEFAULT_MARKER_TYPES));
  const [majorOnly, setMajorOnly] = useState(false);
  const [majorThreshold, setMajorThreshold] = useState(0.1);
  const markerProjection = useMemo(
    () => projectEventMarkers(markerReport, markerTypes, majorOnly, majorThreshold),
    [majorOnly, majorThreshold, markerReport, markerTypes],
  );
  const [sync] = useState(() => new ChartSyncController());
  const registerPortfolio = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("portfolio", chart), [sync]);
  const registerPerformance = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("performance", chart), [sync]);
  const registerDrawdown = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("drawdown", chart), [sync]);
  const registerRegime = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("regime", chart), [sync]);
  const registerTargetAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("target-allocation", chart), [sync]);
  const registerActualAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("actual-allocation", chart), [sync]);
  useEffect(() => sync.subscribeViewportChange(() => setSelectedRange("CUSTOM")), [sync]);
  const fullRange = bundle.range ? { from: bundle.range.from, to: bundle.range.to } : undefined;
  const domainKey = report?.identity?.backtest_run_id ?? series?.identity?.backtest_run_id ?? "empty";
  useEffect(() => {
    if (!fullRange) return;
    sync.initialize(fullRange);
    setSelectedRange("MAX");
  }, [domainKey, fullRange?.from, fullRange?.to, sync]);
  const selectRange = (preset: typeof presets[number]) => {
    setSelectedRange(preset);
    if (preset === "MAX") {
      sync.showFullHistory(fullRange);
      return;
    }
    const range = rangeForPreset(bundle, preset);
    if (range) sync.setRange({ from: range.from, to: range.to }, preset);
  };
  const fitAll = () => {
    setSelectedRange("MAX");
    sync.showFullHistory(fullRange);
  };
  const resetView = () => {
    sync.resetView(fullRange);
    setSelectedRange("MAX");
  };
  const toggleMarkerType = (type: BacktestMarkerType) => {
    const next = new Set(markerTypes);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    setMarkerTypes(next);
    onMarkerTypesChange?.([...next].sort());
  };
  if (loading) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Loading report charts...</p></section>;
  if (error) return <section className="panel financial-charts" aria-label="Financial charts"><p className="research-error">{error}</p></section>;
  if (!report || !series) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Financial report is not available for this run.</p></section>;
  return <section className="financial-charts" aria-label="Core financial charts">
    <div className="financial-charts-toolbar"><div><span className="eyebrow">VERSIONED REPORT SERIES</span><h2>Financial time series</h2></div><div className="financial-chart-controls"><div className="range-controls" aria-label="Chart range">{presets.map((preset) => <button className={`button ${selectedRange === preset ? "button-primary" : "button-secondary"}`} type="button" key={preset} onClick={() => selectRange(preset)}>{preset}</button>)}<button className="button button-secondary" type="button" onClick={fitAll}>Fit All</button><button className="button button-secondary" type="button" onClick={resetView}>Reset View</button>{selectedRange === "CUSTOM" && <span className="custom-range-label" role="status">Custom view</span>}</div></div></div>
    <fieldset className="marker-toolbar" aria-label="Event marker filters">
      <legend>Event markers</legend>
      <div className="marker-filter-grid">{MARKER_FILTERS.map((item) => <label className="marker-toggle" key={item.type}><input type="checkbox" checked={markerTypes.has(item.type)} onChange={() => toggleMarkerType(item.type)} />{item.label}</label>)}</div>
      <div className="marker-density-controls"><label className="marker-toggle"><input type="checkbox" checked={majorOnly} onChange={(event) => setMajorOnly(event.target.checked)} />Major only</label><label>Display threshold<input aria-label="Major marker threshold" type="number" min="0" max="1" step="0.01" value={majorThreshold} disabled={!majorOnly} onChange={(event) => setMajorThreshold(Math.min(1, Math.max(0, Number(event.target.value) || 0)))} /></label><span role="status">{markerProjection.visibleEventCount} events shown{markerProjection.hiddenEventCount > 0 ? ` · ${markerProjection.hiddenEventCount} hidden by filters` : ""}</span></div>
      {markerLoading && <p className="muted" role="status">Loading event markers...</p>}
      {markerError && <p className="research-error" role="alert">Event markers unavailable: {markerError}</p>}
    </fieldset>
    <div className="financial-chart-stack">
      <FinancialChart title="Portfolio Value vs Capital Invested" description="Raw account equity compared with the external capital basis" series={bundle.portfolio} markers={markerProjection.markers} showDollarDifference height={500} onReady={registerPortfolio} />
      <FinancialChart title="Strategy Performance (TWR)" description="Flow-adjusted normalized wealth with optional benchmark" series={bundle.performance} markers={markerProjection.markers} height={500} onReady={registerPerformance} />
      <FinancialChart title="Drawdown" description="Percentage decline from normalized wealth peaks" series={bundle.drawdown} height={320} onReady={registerDrawdown} />
      <StrategyRegimeStrip points={bundle.regime} status={bundle.regimeStatus} reason={bundle.regimeReason} height={140} onReady={registerRegime} />
      <FinancialChart title="Target Allocation" description="Strategy intent resolved at the signal close" series={bundle.targetAllocation} markers={bundle.strategyMarkers} height={340} onReady={registerTargetAllocation} />
      <FinancialChart title="Actual Allocation" description="Portfolio reality after integer-share execution, costs, and cash remainder" series={bundle.actualAllocation} height={340} onReady={registerActualAllocation} />
    </div>
  </section>;
}
