import { useCallback, useEffect, useMemo, useState } from "react";

import { ChartSyncController } from "./chartSync";
import {
  buildComparisonSeries,
  comparisonFullRange,
  comparisonPresetRange,
  visibleComparisonSeries,
} from "./comparisonCharts";
import { MultiStrategyMetricsTable } from "./MultiStrategyMetricsTable";
import { MultiStrategyDrawdownChart, MultiStrategyTwrChart } from "./MultiStrategyTwrChart";
import type { ComparisonCompatibilityStatus, ResearchComparison } from "./types";

const presets = ["1Y", "3Y", "5Y", "MAX"] as const;
type RangeMode = typeof presets[number] | "CUSTOM";

function compatibilityTitle(status: ComparisonCompatibilityStatus): string {
  if (status === "COMPARABLE") return "Comparable TWR context";
  if (status === "WARNING") return "TWR comparison warning";
  if (status === "INCOMPATIBLE") return "TWR comparison blocked";
  return "TWR compatibility unknown";
}

export function MultiStrategyComparison({ comparison }: { comparison: ResearchComparison }) {
  const twrSeries = useMemo(() => buildComparisonSeries(comparison, "twr"), [comparison]);
  const drawdownSeries = useMemo(() => buildComparisonSeries(comparison, "drawdown"), [comparison]);
  const [hiddenRunIds, setHiddenRunIds] = useState<Set<string>>(() => new Set());
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const [hoverDate, setHoverDate] = useState<string | null>(null);
  const [selectedRange, setSelectedRange] = useState<RangeMode>("MAX");
  const [sync] = useState(() => new ChartSyncController());
  const compatibility = comparison.compatibility.twr;
  const blocked = compatibility.status === "INCOMPATIBLE";
  const visibleTwr = useMemo(
    () => visibleComparisonSeries(twrSeries, hiddenRunIds, focusRunId),
    [focusRunId, hiddenRunIds, twrSeries],
  );
  const visibleDrawdown = useMemo(
    () => visibleComparisonSeries(drawdownSeries, hiddenRunIds, focusRunId),
    [drawdownSeries, focusRunId, hiddenRunIds],
  );
  const fullRange = useMemo(() => comparisonFullRange([...twrSeries, ...drawdownSeries]), [drawdownSeries, twrSeries]);
  const registerTwrChart = useCallback(
    (chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("twr-comparison", chart),
    [sync],
  );
  const registerDrawdownChart = useCallback(
    (chart: Parameters<ChartSyncController["register"]>[1]) => sync.register("drawdown-comparison", chart),
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
    else sync.setRange(range);
  };
  const fitAll = () => {
    setSelectedRange("MAX");
    if (fullRange) sync.showFullHistory(fullRange);
  };
  const resetView = () => {
    setSelectedRange("MAX");
    setHoverDate(null);
    sync.resetView(fullRange ?? undefined);
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
          {presets.map((preset) => <button className={`button ${selectedRange === preset ? "button-primary" : "button-secondary"}`} type="button" key={preset} disabled={blocked} onClick={() => selectRange(preset)}>{preset}</button>)}
          <button className="button button-secondary" type="button" disabled={blocked} onClick={fitAll}>Fit All</button>
          <button className="button button-secondary" type="button" disabled={blocked} onClick={resetView}>Reset View</button>
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
      <p className="comparison-provenance">{comparison.provenance_notice}</p>
    </section>
  </section>;
}
