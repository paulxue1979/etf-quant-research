import { useCallback, useEffect, useMemo, useState } from "react";

import type { BacktestReport, BacktestReportSeries } from "./types";
import { ChartSyncController } from "./chartSync";
import { FinancialChart } from "./FinancialChart";
import { buildChartBundle, rangeForPreset } from "./reportCharts";
import { StrategyRegimeStrip } from "./StrategyRegimeStrip";

interface BacktestReportChartsProps {
  report: BacktestReport | null;
  series: BacktestReportSeries | null;
  loading?: boolean;
  error?: string | null;
}

const presets = ["1Y", "3Y", "5Y", "MAX"] as const;
type RangeMode = typeof presets[number] | "CUSTOM";

export function BacktestReportCharts({ report, series, loading = false, error = null }: BacktestReportChartsProps) {
  const bundle = useMemo(() => buildChartBundle(report, series), [report, series]);
  const [selectedRange, setSelectedRange] = useState<RangeMode>("MAX");
  const [showContributions, setShowContributions] = useState(false);
  const [sync] = useState(() => new ChartSyncController());
  const registerPortfolio = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("portfolio", chart), [sync]);
  const registerPerformance = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("performance", chart), [sync]);
  const registerDrawdown = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("drawdown", chart), [sync]);
  const registerRegime = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("regime", chart), [sync]);
  const registerTargetAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("target-allocation", chart), [sync]);
  const registerActualAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("actual-allocation", chart), [sync]);
  useEffect(() => sync.subscribeViewportChange(() => setSelectedRange("CUSTOM")), [sync]);
  const fullRange = bundle.range ? { from: bundle.range.from, to: bundle.range.to } : undefined;
  const selectRange = (preset: typeof presets[number]) => {
    setSelectedRange(preset);
    if (preset === "MAX") {
      sync.showFullHistory(fullRange);
      return;
    }
    const range = rangeForPreset(bundle, preset);
    if (range) sync.setRange({ from: range.from, to: range.to });
  };
  const fitAll = () => {
    setSelectedRange("MAX");
    sync.showFullHistory(fullRange);
  };
  const resetView = () => {
    setSelectedRange("MAX");
    sync.resetView(fullRange);
  };
  if (loading) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Loading report charts...</p></section>;
  if (error) return <section className="panel financial-charts" aria-label="Financial charts"><p className="research-error">{error}</p></section>;
  if (!report || !series) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Financial report is not available for this run.</p></section>;
  return <section className="financial-charts" aria-label="Core financial charts">
    <div className="financial-charts-toolbar"><div><span className="eyebrow">VERSIONED REPORT SERIES</span><h2>Financial time series</h2></div><div className="financial-chart-controls">{bundle.contributionMarkers.length > 0 && <label className="marker-toggle"><input type="checkbox" checked={showContributions} onChange={(event) => setShowContributions(event.target.checked)} />Show contributions</label>}<div className="range-controls" aria-label="Chart range">{presets.map((preset) => <button className={`button ${selectedRange === preset ? "button-primary" : "button-secondary"}`} type="button" key={preset} onClick={() => selectRange(preset)}>{preset}</button>)}<button className="button button-secondary" type="button" onClick={fitAll}>Fit All</button><button className="button button-secondary" type="button" onClick={resetView}>Reset View</button>{selectedRange === "CUSTOM" && <span className="custom-range-label" role="status">Custom view</span>}</div></div></div>
    <div className="financial-chart-stack">
      <FinancialChart title="Portfolio Value vs Capital Invested" description="Raw account equity compared with the external capital basis" series={bundle.portfolio} markers={showContributions ? bundle.contributionMarkers : []} showDollarDifference height={500} onReady={registerPortfolio} />
      <FinancialChart title="Strategy Performance (TWR)" description="Flow-adjusted normalized wealth with optional benchmark" series={bundle.performance} height={500} onReady={registerPerformance} />
      <FinancialChart title="Drawdown" description="Percentage decline from normalized wealth peaks" series={bundle.drawdown} height={320} onReady={registerDrawdown} />
      <StrategyRegimeStrip points={bundle.regime} status={bundle.regimeStatus} reason={bundle.regimeReason} height={140} onReady={registerRegime} />
      <FinancialChart title="Target Allocation" description="Strategy intent resolved at the signal close" series={bundle.targetAllocation} markers={bundle.strategyMarkers} height={340} onReady={registerTargetAllocation} />
      <FinancialChart title="Actual Allocation" description="Portfolio reality after integer-share execution, costs, and cash remainder" series={bundle.actualAllocation} height={340} onReady={registerActualAllocation} />
    </div>
  </section>;
}
