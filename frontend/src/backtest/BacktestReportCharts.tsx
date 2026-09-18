import { useCallback, useMemo, useState } from "react";

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

export function BacktestReportCharts({ report, series, loading = false, error = null }: BacktestReportChartsProps) {
  const bundle = useMemo(() => buildChartBundle(report, series), [report, series]);
  const [selectedRange, setSelectedRange] = useState<typeof presets[number]>("MAX");
  const [sync] = useState(() => new ChartSyncController());
  const registerPortfolio = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("portfolio", chart), [sync]);
  const registerPerformance = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("performance", chart), [sync]);
  const registerDrawdown = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("drawdown", chart), [sync]);
  const registerRegime = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("regime", chart), [sync]);
  const registerTargetAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("target-allocation", chart), [sync]);
  const registerActualAllocation = useCallback((chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("actual-allocation", chart), [sync]);
  const selectRange = (preset: typeof presets[number]) => {
    const range = rangeForPreset(bundle, preset);
    setSelectedRange(preset);
    if (range) sync.setRange({ from: range.from, to: range.to });
  };
  if (loading) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Loading report charts...</p></section>;
  if (error) return <section className="panel financial-charts" aria-label="Financial charts"><p className="research-error">{error}</p></section>;
  if (!report || !series) return <section className="panel financial-charts" aria-label="Financial charts"><p className="muted">Financial report is not available for this run.</p></section>;
  return <section className="financial-charts" aria-label="Core financial charts">
    <div className="financial-charts-toolbar"><div><span className="eyebrow">VERSIONED REPORT SERIES</span><h2>Financial time series</h2></div><div className="range-controls" aria-label="Chart range">{presets.map((preset) => <button className={`button ${selectedRange === preset ? "button-primary" : "button-secondary"}`} type="button" key={preset} onClick={() => selectRange(preset)}>{preset}</button>)}</div></div>
    <div className="financial-chart-stack">
      <FinancialChart title="Portfolio Value" description="Account value and cumulative capital invested" series={bundle.portfolio} height={500} onReady={registerPortfolio} />
      <FinancialChart title="Strategy Performance" description="Normalized TWR with optional benchmark" series={bundle.performance} height={500} onReady={registerPerformance} />
      <FinancialChart title="Drawdown" description="Percentage decline from normalized wealth peaks" series={bundle.drawdown} height={320} onReady={registerDrawdown} />
      <StrategyRegimeStrip points={bundle.regime} status={bundle.regimeStatus} reason={bundle.regimeReason} height={140} onReady={registerRegime} />
      <FinancialChart title="Target Allocation" description="Strategy intent resolved at the signal close" series={bundle.targetAllocation} markers={bundle.strategyMarkers} height={340} onReady={registerTargetAllocation} />
      <FinancialChart title="Actual Allocation" description="Portfolio reality after integer-share execution, costs, and cash remainder" series={bundle.actualAllocation} height={340} onReady={registerActualAllocation} />
    </div>
  </section>;
}
