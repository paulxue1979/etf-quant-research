import { useCallback, useEffect, useMemo, useState } from "react";

import { ChartSyncController } from "./chartSync";
import {
  buildComparisonSeries,
  comparisonFullRange,
  comparisonPresetRange,
  visibleComparisonSeries,
} from "./comparisonCharts";
import { MultiStrategyMetricsTable } from "./MultiStrategyMetricsTable";
import { MultiStrategyDrawdownChart, MultiStrategyTwrChart, MultiStrategyWealthChart } from "./MultiStrategyTwrChart";
import type { ComparisonCompatibilityStatus, ResearchComparison } from "./types";

const presets = ["1Y", "3Y", "5Y", "MAX"] as const;
type RangeMode = typeof presets[number] | "CUSTOM";
type WealthMode = "portfolio_value" | "capital_invested" | "investment_profit";

const wealthModes: Array<{ key: WealthMode; label: string }> = [
  { key: "portfolio_value", label: "Portfolio Value" },
  { key: "capital_invested", label: "Capital Invested" },
  { key: "investment_profit", label: "Investment Profit" },
];

function formatCurrency(value: number | undefined): string {
  return typeof value === "number"
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value)
    : "N/A";
}

function formatXirr(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "N/A";
}

function compatibilityTitle(status: ComparisonCompatibilityStatus): string {
  if (status === "COMPARABLE") return "Comparable TWR context";
  if (status === "WARNING") return "TWR comparison warning";
  if (status === "INCOMPATIBLE") return "TWR comparison blocked";
  return "TWR compatibility unknown";
}

export function MultiStrategyComparison({ comparison }: { comparison: ResearchComparison }) {
  const twrSeries = useMemo(() => buildComparisonSeries(comparison, "twr"), [comparison]);
  const drawdownSeries = useMemo(() => buildComparisonSeries(comparison, "drawdown"), [comparison]);
  const portfolioSeries = useMemo(() => buildComparisonSeries(comparison, "portfolio_value"), [comparison]);
  const capitalSeries = useMemo(() => buildComparisonSeries(comparison, "capital_invested"), [comparison]);
  const profitSeries = useMemo(() => buildComparisonSeries(comparison, "investment_profit"), [comparison]);
  const [hiddenRunIds, setHiddenRunIds] = useState<Set<string>>(() => new Set());
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const [hoverDate, setHoverDate] = useState<string | null>(null);
  const [selectedRange, setSelectedRange] = useState<RangeMode>("MAX");
  const [wealthMode, setWealthMode] = useState<WealthMode>("portfolio_value");
  const [sync] = useState(() => new ChartSyncController());
  const compatibility = comparison.compatibility.twr;
  const wealthCompatibility = comparison.compatibility.portfolio_value ?? {
    status: "UNKNOWN" as const,
    reason_codes: ["WEALTH_COMPATIBILITY_UNAVAILABLE"],
    human_readable_reasons: ["Wealth compatibility was not returned by this comparison contract."],
    dimensions: {},
  };
  const blocked = compatibility.status === "INCOMPATIBLE";
  const wealthContractAvailable = [portfolioSeries, capitalSeries, profitSeries]
    .some((series) => series.some((item) => item.status === "available"));
  const navigationDisabled = blocked && !wealthContractAvailable;
  const visibleTwr = useMemo(
    () => visibleComparisonSeries(twrSeries, hiddenRunIds, focusRunId),
    [focusRunId, hiddenRunIds, twrSeries],
  );
  const visibleDrawdown = useMemo(
    () => visibleComparisonSeries(drawdownSeries, hiddenRunIds, focusRunId),
    [drawdownSeries, focusRunId, hiddenRunIds],
  );
  const wealthSeries = wealthMode === "portfolio_value"
    ? portfolioSeries
    : wealthMode === "capital_invested" ? capitalSeries : profitSeries;
  const visibleWealth = useMemo(
    () => visibleComparisonSeries(wealthSeries, hiddenRunIds, focusRunId),
    [focusRunId, hiddenRunIds, wealthSeries],
  );
  const fullRange = useMemo(
    () => comparisonFullRange([
      ...twrSeries,
      ...drawdownSeries,
      ...portfolioSeries,
      ...capitalSeries,
      ...profitSeries,
    ]),
    [capitalSeries, drawdownSeries, portfolioSeries, profitSeries, twrSeries],
  );
  const registerTwrChart = useCallback(
    (chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("twr-comparison", chart),
    [sync],
  );
  const registerDrawdownChart = useCallback(
    (chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("drawdown-comparison", chart),
    [sync],
  );
  const registerWealthChart = useCallback(
    (chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("wealth-comparison", chart),
    [sync],
  );
  useEffect(() => {
    const disposeViewport = sync.subscribeViewportChange(() => setSelectedRange("CUSTOM"));
    const disposeCrosshair = sync.subscribeCrosshairChange((time) => setHoverDate(time));
    return () => {
      disposeViewport();
      disposeCrosshair();
    };
  }, [sync]);
  const comparisonKey = comparison.runs.map((run) => run.backtest_run_id).join(",");
  useEffect(() => {
    if (!fullRange) return;
    sync.initialize(fullRange);
    setSelectedRange("MAX");
  }, [comparisonKey, fullRange?.from, fullRange?.to, sync]);

  const toggleRun = (runId: string) => {
    setHiddenRunIds((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
    if (focusRunId === runId) setFocusRunId(null);
  };
  const focusRun = (runId: string) => {
    setHiddenRunIds((current) => {
      const next = new Set(current);
      next.delete(runId);
      return next;
    });
    setFocusRunId(runId);
  };
  const showAll = () => {
    setHiddenRunIds(new Set());
    setFocusRunId(null);
  };
  const selectRange = (preset: typeof presets[number]) => {
    setSelectedRange(preset);
    const range = comparisonPresetRange(fullRange, preset);
    if (!range) return;
    if (preset === "MAX") sync.showFullHistory(range);
    else sync.setRange(range, preset);
  };
  const fitAll = () => {
    setSelectedRange("MAX");
    if (fullRange) sync.showFullHistory(fullRange);
  };
  const resetView = () => {
    setHoverDate(null);
    sync.resetView(fullRange ?? undefined);
    setSelectedRange("MAX");
  };

  return <section className="research-comparison" aria-label="Multi-strategy comparison workspace">
    <section className={`panel compatibility comparison-${compatibility.status.toLowerCase()}`}>
      <span className="eyebrow">TWR COMPARISON CONTRACT · V2.0</span>
      <h2>{compatibilityTitle(compatibility.status)}</h2>
      {compatibility.human_readable_reasons.length > 0
        ? <ul>{compatibility.human_readable_reasons.map((reason, index) => <li key={`${compatibility.reason_codes[index] ?? "reason"}-${index}`}><strong>{compatibility.reason_codes[index] ?? compatibility.status}</strong><span>{reason}</span></li>)}</ul>
        : <p>Backend provenance checks found no TWR comparison differences.</p>}
    </section>
    <section className="panel comparison-workspace">
      <div className="financial-charts-toolbar">
        <div><span className="eyebrow">MULTI-STRATEGY RESEARCH</span><h2>Canonical strategy comparison</h2></div>
        <div className="range-controls" aria-label="Comparison chart range">
          {presets.map((preset) => <button className={`button ${selectedRange === preset ? "button-primary" : "button-secondary"}`} type="button" key={preset} disabled={navigationDisabled} onClick={() => selectRange(preset)}>{preset}</button>)}
          <button className="button button-secondary" type="button" disabled={navigationDisabled} onClick={fitAll}>Fit All</button>
          <button className="button button-secondary" type="button" disabled={navigationDisabled} onClick={resetView}>Reset View</button>
          {selectedRange === "CUSTOM" && <span className="custom-range-label" role="status">Custom view</span>}
        </div>
      </div>
      <div className="comparison-legend-toolbar">
        <div className="comparison-legend" aria-label="Comparison legend">
          {twrSeries.map((item, index) => {
            const hidden = hiddenRunIds.has(item.runId);
            const focusedOut = focusRunId !== null && focusRunId !== item.runId;
            const drawdown = drawdownSeries[index];
            return <button
              type="button"
              key={item.runId}
              className={`comparison-legend-item ${hidden || focusedOut ? "is-hidden" : ""} ${focusRunId === item.runId ? "is-focused" : ""}`}
              onClick={() => toggleRun(item.runId)}
              onDoubleClick={() => focusRun(item.runId)}
              aria-pressed={!hidden && !focusedOut}
              title="Click to hide or show; double-click to focus"
            ><i style={{ background: item.color }} /><span>{item.label}</span>{item.status !== "available" && <small>TWR: {item.reason ?? item.status}</small>}{drawdown?.status !== "available" && <small>Drawdown: {drawdown?.reason ?? drawdown?.status}</small>}</button>;
          })}
        </div>
        <button className="button button-secondary" type="button" onClick={showAll}>Show All</button>
        {focusRunId && <button className="button button-secondary" type="button" onClick={() => setFocusRunId(null)}>Exit Focus</button>}
      </div>
      {blocked
        ? <div className="comparison-empty incompatible" role="alert">The backend marked this TWR comparison INCOMPATIBLE. Plotting is disabled to avoid presenting an unfair comparison.</div>
        : <>
          <div className="comparison-chart-stack">
            <MultiStrategyTwrChart allSeries={twrSeries} hiddenRunIds={hiddenRunIds} focusRunId={focusRunId} visibleSeries={visibleTwr} hoverDate={hoverDate} onReady={registerTwrChart} />
            <MultiStrategyDrawdownChart allSeries={drawdownSeries} hiddenRunIds={hiddenRunIds} focusRunId={focusRunId} visibleSeries={visibleDrawdown} hoverDate={hoverDate} onReady={registerDrawdownChart} />
          </div>
          <MultiStrategyMetricsTable comparison={comparison} colors={twrSeries} hiddenRunIds={hiddenRunIds} focusRunId={focusRunId} />
        </>}
      {wealthContractAvailable && <section className="wealth-comparison" aria-label="Wealth Accumulation comparison">
        <div className={`compatibility wealth-compatibility comparison-${wealthCompatibility.status.toLowerCase()}`}>
          <span className="eyebrow">WEALTH ACCUMULATION CONTRACT · V{comparison.comparison_schema_version}</span>
          <h3>Wealth compatibility: {wealthCompatibility.status}</h3>
          {wealthCompatibility.human_readable_reasons.length > 0
            ? <ul>{wealthCompatibility.human_readable_reasons.map((reason, index) => <li key={`${wealthCompatibility.reason_codes[index] ?? "wealth-reason"}-${index}`}><strong>{wealthCompatibility.reason_codes[index] ?? wealthCompatibility.status}</strong><span>{reason}</span></li>)}</ul>
            : <p>Capital path, execution context, and persisted provenance are comparable.</p>}
          <p>Portfolio Value shows actual wealth. TWR isolates strategy performance. XIRR describes investor experience under the effective cash-flow schedule.</p>
        </div>
        <div className="wealth-heading">
          <div><span className="eyebrow">ACTUAL ACCOUNT WEALTH</span><h3>Wealth Accumulation</h3></div>
          <div className="wealth-mode-control" role="group" aria-label="Wealth comparison mode">
            {wealthModes.map((mode) => <button key={mode.key} type="button" className={`button ${wealthMode === mode.key ? "button-primary" : "button-secondary"}`} aria-pressed={wealthMode === mode.key} onClick={() => setWealthMode(mode.key)}>{mode.label}</button>)}
          </div>
        </div>
        <MultiStrategyWealthChart kind={wealthMode} allSeries={wealthSeries} hiddenRunIds={hiddenRunIds} focusRunId={focusRunId} visibleSeries={visibleWealth} hoverDate={hoverDate} onReady={registerWealthChart} />
        <div className="wealth-summary" aria-label="Wealth final values">
          {comparison.runs.map((run, index) => {
            const hidden = hiddenRunIds.has(run.backtest_run_id) || (focusRunId !== null && focusRunId !== run.backtest_run_id);
            return <article key={run.backtest_run_id} className={hidden ? "is-hidden" : ""}>
              <header><i style={{ background: twrSeries[index]?.color }} /><strong>{run.short_display_label}</strong></header>
              <dl>
                <div><dt>Final Portfolio Value</dt><dd>{formatCurrency(run.final_equity)}</dd></div>
                <div><dt>Total Capital Invested</dt><dd>{formatCurrency(run.total_capital_invested)}</dd></div>
                <div><dt>Investment Profit</dt><dd>{formatCurrency(run.investment_profit)}</dd></div>
                <div><dt>XIRR · Investor Experience</dt><dd>{formatXirr(run.metrics.xirr?.value)}</dd></div>
              </dl>
            </article>;
          })}
        </div>
        <p className="wealth-semantics">Results remain descriptive; no automatic strategy selection occurs. Wealth paths include actual external-capital timing; use canonical TWR for strategy performance and XIRR for investor experience.</p>
      </section>}
      <p className="comparison-provenance">{comparison.provenance_notice}</p>
    </section>
  </section>;
}
