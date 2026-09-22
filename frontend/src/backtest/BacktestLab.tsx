import { useEffect, useMemo, useState } from "react";

import { BacktestApiError, backtestApi } from "./api";
import { BacktestReportCharts } from "./BacktestReportCharts";
import { CapitalResearchPanel } from "./CapitalResearchPanel";
import { HoldingPeriodReport } from "./HoldingPeriodReport";
import { MultiStrategyComparison } from "./MultiStrategyComparison";
import { ResearchProtocolPanel } from "./ResearchProtocolPanel";
import type {
  BacktestRequest,
  BacktestReport,
  BacktestReportSeries,
  BacktestRun,
  ContributionFrequency,
  DrawdownPoint,
  EquityPoint,
  MetricValue,
  ResearchBacktestSummary,
  ResearchComparison,
  ResearchMetrics,
  ResearchSortBy,
  StrategyCatalogItem,
} from "./types";
import type { PriceField, StrategyVersionSummary } from "../strategy/types";

interface BacktestLabProps {
  onBack: () => void;
}

const EMPTY_FORM = {
  startDate: "2020-01-01",
  endDate: "2025-12-31",
  initialCapital: "100000",
  commissionRatePercent: "0",
  commissionPerOrder: "0",
  slippagePercent: "0",
  priceField: "adjusted_close" as PriceField,
  benchmarkSymbol: "",
  contributionsEnabled: false,
  contributionFrequency: "monthly" as ContributionFrequency,
  contributionAmount: "",
  contributionDate: "",
};

const RESEARCH_PAGE_SIZE = 50;

function number(value: string, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function metricText(metric: MetricValue, percentage = false): string {
  if (metric.status !== "available" || metric.value === null) return "N/A";
  const value = percentage ? metric.value * 100 : metric.value;
  return `${value.toFixed(2)}${percentage ? "%" : ""}`;
}

function MetricCard({ label, metric, percentage = false }: { label: string; metric: MetricValue; percentage?: boolean }) {
  return (
    <div className="backtest-metric">
      <span>{label}</span>
      <strong>{metricText(metric, percentage)}</strong>
      {metric.status !== "available" && metric.reason && <small>{metric.reason}</small>}
    </div>
  );
}

function linePoints<T>(items: T[], readValue: (item: T) => number): string {
  if (items.length === 0) return "";
  const values = items.map(readValue);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return items
    .map((item, index) => {
      const x = items.length === 1 ? 350 : (index / (items.length - 1)) * 700;
      const y = 190 - ((readValue(item) - min) / span) * 160;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function SeriesChart<T>({ label, items, readValue, color }: { label: string; items: T[]; readValue: (item: T) => number; color: string }) {
  return (
    <div className="series-chart">
      <div className="subsection-heading">
        <h3>{label}</h3>
        <span className="muted">Backend series</span>
      </div>
      {items.length === 0 ? (
        <p className="muted">No series data returned.</p>
      ) : (
        <svg className="series-svg" viewBox="0 0 700 220" role="img" aria-label={label}>
          <line x1="0" y1="190" x2="700" y2="190" stroke="#2b3942" />
          <polyline points={linePoints(items, readValue)} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      )}
    </div>
  );
}

const metricDefinitions: Array<{ label: string; key: keyof ResearchMetrics; percentage?: boolean }> = [
  { label: "Total return", key: "total_return", percentage: true },
  { label: "CAGR", key: "cagr", percentage: true },
  { label: "Annualized volatility", key: "annualized_volatility", percentage: true },
  { label: "Sharpe", key: "sharpe_ratio" },
  { label: "Sortino", key: "sortino_ratio" },
  { label: "Max drawdown", key: "max_drawdown", percentage: true },
  { label: "Calmar", key: "calmar_ratio" },
  { label: "Win rate", key: "win_rate", percentage: true },
  { label: "Profit factor", key: "profit_factor" },
  { label: "Average trade return", key: "average_trade_return", percentage: true },
  { label: "Best trade", key: "best_trade", percentage: true },
  { label: "Worst trade", key: "worst_trade", percentage: true },
  { label: "Average holding period", key: "average_holding_period" },
  { label: "Turnover", key: "turnover", percentage: true },
];

const researchSortOptions: Array<{ value: ResearchSortBy; label: string }> = [
  { value: "created_at", label: "Created" },
  ...metricDefinitions.map((metric) => ({ value: metric.key, label: metric.label })),
];

function ResearchHistory({
  runs,
  selectedRunIds,
  sortBy,
  order,
  loading,
  total,
  limit,
  offset,
  onSortBy,
  onOrder,
  onPrevious,
  onNext,
  onToggle,
  onCompare,
}: {
  runs: ResearchBacktestSummary[];
  selectedRunIds: string[];
  sortBy: ResearchSortBy;
  order: "asc" | "desc";
  loading: boolean;
  total: number;
  limit: number;
  offset: number;
  onSortBy: (value: ResearchSortBy) => void;
  onOrder: (value: "asc" | "desc") => void;
  onPrevious: () => void;
  onNext: () => void;
  onToggle: (runId: string) => void;
  onCompare: () => void;
}) {
  return (
    <section className="panel research-history">
      <div className="section-header compact"><div><span className="eyebrow">RESEARCH HISTORY</span><h2>Saved backtest runs</h2></div><span className="draft-label">{selectedRunIds.length} / 10 selected</span></div>
      <div className="research-controls">
        <label>Sort by<select aria-label="Research sort metric" value={sortBy} onChange={(event) => onSortBy(event.target.value as ResearchSortBy)}>{researchSortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label>Order<select aria-label="Research sort order" value={order} onChange={(event) => onOrder(event.target.value as "asc" | "desc")}><option value="desc">Descending</option><option value="asc">Ascending</option></select></label>
        <button className="button button-primary" type="button" onClick={onCompare} disabled={loading || selectedRunIds.length < 2}>Compare selected</button>
      </div>
      {selectedRunIds.length === 10 && <p className="selection-limit" role="status">Maximum 10 runs selected. Remove one run before selecting another.</p>}
      {runs.length === 0 ? <p className="muted">No saved backtest runs yet.</p> : <div className="table-scroll"><table className="research-table"><thead><tr><th>Select</th><th>Strategy</th><th>Version</th><th>Hash</th><th>Run</th><th>Date</th><th>Initial capital</th><th>Price field</th><th>Engine</th><th>Analysis</th></tr></thead><tbody>{runs.map((item) => {
        const selected = selectedRunIds.includes(item.backtest_run_id);
        return <tr key={item.backtest_run_id}><td><input aria-label={`Select research run ${item.backtest_run_id}`} type="checkbox" checked={selected} disabled={!selected && selectedRunIds.length >= 10} onChange={() => onToggle(item.backtest_run_id)} /></td><td>{item.strategy_id}</td><td>{item.strategy_version_id}</td><td className="research-hash" title={item.strategy_version_content_hash}>{item.strategy_version_content_hash}</td><td>{item.backtest_run_id}</td><td>{item.start_date} to {item.end_date}</td><td>{item.initial_capital.toLocaleString(undefined, { style: "currency", currency: "USD" })}</td><td>{item.price_field_used.toUpperCase()}</td><td>{item.engine_version}</td><td>{item.analysis_version}</td></tr>;
      })}</tbody></table></div>}
      <div className="research-controls" aria-label="Research history pagination">
        <button className="button button-secondary" type="button" onClick={onPrevious} disabled={loading || offset === 0}>Previous</button>
        <span className="muted">{total === 0 ? "0 runs" : `${offset + 1}-${Math.min(offset + runs.length, total)} of ${total}`}</span>
        <button className="button button-secondary" type="button" onClick={onNext} disabled={loading || offset + limit >= total}>Next</button>
      </div>
    </section>
  );
}

function Results({ run, report, reportSeries, reportLoading, reportError, reportSeriesError }: {
  run: BacktestRun;
  report: BacktestReport | null;
  reportSeries: BacktestReportSeries | null;
  reportLoading: boolean;
  reportError: string | null;
  reportSeriesError: string | null;
}) {
  const analysis = run.performance_analysis;
  const result = run.backtest_result;
  const metrics = useMemo(
    () => [
      ["Total return", analysis.total_return, true],
      ["CAGR", analysis.cagr, true],
      ["Volatility", analysis.annualized_volatility, true],
      ["Sharpe", analysis.sharpe_ratio, false],
      ["Sortino", analysis.sortino_ratio, false],
      ["Max drawdown", analysis.max_drawdown, true],
      ["Calmar", analysis.calmar_ratio, false],
      ["Win rate", analysis.trade_metrics.win_rate, true],
    ] as const,
    [analysis],
  );
  return (
    <section className="backtest-results">
      <div className="panel result-header">
        <div>
          <span className="eyebrow">COMPLETED BACKTEST RUN</span>
          <h2>{run.backtest_run_id}</h2>
          <p className="muted">Version {run.strategy_version_id} · {analysis.start_date} to {analysis.end_date} · {analysis.price_field_used.toUpperCase()}</p>
        </div>
        <div className="result-equity">
          <span>Final equity</span>
          <strong>{result.final_equity.toLocaleString(undefined, { style: "currency", currency: "USD" })}</strong>
        </div>
      </div>

      <section className="panel">
        <div className="section-header compact"><div><span className="eyebrow">PERFORMANCE</span><h2>Backend analytics</h2></div></div>
        <div className="backtest-metrics">{metrics.map(([label, metric, percentage]) => <MetricCard key={label} label={label} metric={metric} percentage={percentage} />)}</div>
      </section>

      <CapitalResearchPanel report={report} loading={reportLoading} error={reportError} />

      <BacktestReportCharts report={report} series={reportSeries} loading={reportLoading} error={reportError ?? reportSeriesError} />

      <HoldingPeriodReport backtestRunId={run.backtest_run_id} />

      <section className="panel chart-grid">
        <SeriesChart label="Equity curve" items={result.equity_curve} readValue={(item: EquityPoint) => item.total_equity} color="#6bd7d0" />
        <SeriesChart label="Drawdown curve" items={analysis.drawdown_curve} readValue={(item: DrawdownPoint) => item.value} color="#ff8b8b" />
      </section>

      <section className="panel result-tables">
        <div className="table-block">
          <div className="subsection-heading"><h3>Trades</h3><span className="muted">{result.trades.length} closed</span></div>
          {result.trades.length === 0 ? <p className="muted">No closed trades.</p> : <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Hold</th></tr></thead><tbody>{result.trades.slice(-12).map((trade) => <tr key={`${trade.symbol}-${trade.entry_date}-${trade.exit_date}`}><td>{trade.symbol}</td><td>{trade.entry_date}</td><td>{trade.exit_date}</td><td>{(trade.pnl_pct * 100).toFixed(2)}%</td><td>{trade.holding_period}d</td></tr>)}</tbody></table></div>}
        </div>
        <div className="table-block">
          <div className="subsection-heading"><h3>Latest allocations</h3><span className="muted">Backend output</span></div>
          {result.allocation_history.length === 0 ? <p className="muted">No allocation history.</p> : <div className="table-scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Target</th><th>Actual</th></tr></thead><tbody>{result.allocation_history.slice(-12).map((item, index) => <tr key={`${item.date}-${item.symbol}-${index}`}><td>{item.date}</td><td>{item.symbol}</td><td>{(item.target_weight * 100).toFixed(1)}%</td><td>{(item.actual_weight * 100).toFixed(1)}%</td></tr>)}</tbody></table></div>}
        </div>
        <div className="table-block">
          <div className="subsection-heading"><h3>Latest positions</h3><span className="muted">Backend output</span></div>
          {result.positions.length === 0 ? <p className="muted">No position snapshots.</p> : <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Value</th></tr></thead><tbody>{(result.positions.at(-1)?.positions ?? []).map((position) => <tr key={position.symbol}><td>{position.symbol}</td><td>{position.quantity}</td><td>{position.market_price.toFixed(2)}</td><td>{position.market_value.toLocaleString(undefined, { style: "currency", currency: "USD" })}</td></tr>)}</tbody></table></div>}
        </div>
      </section>
    </section>
  );
}

export function BacktestLab({ onBack }: BacktestLabProps) {
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [versions, setVersions] = useState<StrategyVersionSummary[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [reportSeries, setReportSeries] = useState<BacktestReportSeries | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportSeriesError, setReportSeriesError] = useState<string | null>(null);
  const [researchRuns, setResearchRuns] = useState<ResearchBacktestSummary[]>([]);
  const [researchTotal, setResearchTotal] = useState(0);
  const [researchOffset, setResearchOffset] = useState(0);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [researchSortBy, setResearchSortBy] = useState<ResearchSortBy>("created_at");
  const [researchOrder, setResearchOrder] = useState<"asc" | "desc">("desc");
  const [comparison, setComparison] = useState<ResearchComparison | null>(null);
  const [researchBusy, setResearchBusy] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"catalog" | "versions" | "run" | null>("catalog");
  const [error, setError] = useState<string | null>(null);

  async function loadResearchRuns() {
    setResearchBusy(true);
    setResearchError(null);
    try {
      const response = await backtestApi.listResearchRuns(researchSortBy, researchOrder, {
        limit: RESEARCH_PAGE_SIZE,
        offset: researchOffset,
      });
      setResearchRuns(response.items);
      setResearchTotal(response.total);
    } catch (reason) {
      setResearchError(reason instanceof BacktestApiError ? reason.message : "Unable to load saved research runs.");
    } finally {
      setResearchBusy(false);
    }
  }

  useEffect(() => {
    void backtestApi.listStrategies().then((items) => {
      setCatalog(items);
      setStrategyId(items[0]?.strategy_id ?? "");
      setBusy(null);
    }).catch((reason: unknown) => {
      setBusy(null);
      setError(reason instanceof BacktestApiError ? reason.message : "Unable to load strategies.");
    });
  }, []);

  useEffect(() => {
    void loadResearchRuns();
  }, [researchSortBy, researchOrder, researchOffset]);

  useEffect(() => {
    if (!strategyId) {
      setVersions([]);
      setVersionId("");
      return;
    }
    setBusy("versions");
    setError(null);
    void backtestApi.listVersions(strategyId).then((items) => {
      setVersions(items);
      setVersionId(items.at(-1)?.version_id ?? "");
      setBusy(null);
    }).catch((reason: unknown) => {
      setVersions([]);
      setVersionId("");
      setBusy(null);
      setError(reason instanceof BacktestApiError ? reason.message : "Unable to load strategy versions.");
    });
  }, [strategyId]);

  const updateForm = <K extends keyof typeof EMPTY_FORM>(key: K, value: (typeof EMPTY_FORM)[K]) => setForm((current) => ({ ...current, [key]: value }));
  const selectedStrategy = catalog.find((item) => item.strategy_id === strategyId);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setRun(null);
    setReport(null);
    setReportSeries(null);
    setReportError(null);
    setReportSeriesError(null);
    if (!strategyId || !versionId) {
      setError("Select a strategy and an immutable version before running.");
      return;
    }
    const contributionAmount = Number(form.contributionAmount);
    if (
      form.contributionsEnabled
      && (!form.contributionAmount.trim() || !Number.isFinite(contributionAmount) || contributionAmount <= 0)
    ) {
      setError("Contribution amount must be a positive finite USD value.");
      return;
    }
    if (form.contributionsEnabled && form.contributionFrequency === "one_time" && !form.contributionDate) {
      setError("Select a requested date for the one-time contribution.");
      return;
    }
    const request: BacktestRequest = {
      strategy_id: strategyId,
      strategy_version_id: versionId,
      start_date: form.startDate,
      end_date: form.endDate,
      initial_capital: number(form.initialCapital),
      commission: { rate: number(form.commissionRatePercent) / 100, per_order: number(form.commissionPerOrder) },
      slippage: number(form.slippagePercent) / 100,
      price_field_used: form.priceField,
      execution_rule: "next_trading_day_open",
      fractional_shares: false,
      ...(form.benchmarkSymbol.trim() ? { benchmark_symbol: form.benchmarkSymbol.trim().toUpperCase() } : {}),
      ...(form.contributionsEnabled ? {
        contribution_schedule: {
          frequency: form.contributionFrequency,
          amount: form.contributionAmount,
          ...(form.contributionFrequency === "one_time" ? { requested_date: form.contributionDate } : {}),
        },
      } : {}),
    };
    setBusy("run");
    try {
      const created = await backtestApi.create(request);
      setRun(created);
      setReportLoading(true);
      const [reportResult, seriesResult] = await Promise.allSettled([
        backtestApi.getReport(created.backtest_run_id),
        backtestApi.getReportSeries(created.backtest_run_id, ["equity", "capital", "twr", "drawdown", "benchmark_twr", "benchmark_drawdown"]),
      ]);
      if (reportResult.status === "fulfilled") setReport(reportResult.value);
      else setReportError(reportResult.reason instanceof BacktestApiError ? reportResult.reason.message : "Unable to load the versioned report summary.");
      if (seriesResult.status === "fulfilled") setReportSeries(seriesResult.value);
      else setReportSeriesError(seriesResult.reason instanceof BacktestApiError ? seriesResult.reason.message : "Unable to load versioned report series.");
      setReportLoading(false);
      if (researchOffset === 0) void loadResearchRuns();
      else setResearchOffset(0);
    } catch (reason) {
      setError(reason instanceof BacktestApiError ? reason.message : "Backtest request failed.");
    } finally {
      setBusy(null);
    }
  }

  function toggleResearchRun(runId: string) {
    setResearchError(null);
    setSelectedRunIds((current) => {
      if (current.includes(runId)) return current.filter((item) => item !== runId);
      if (current.length >= 10) {
        setResearchError("Select at most 10 saved backtest runs for one comparison.");
        return current;
      }
      return [...current, runId];
    });
  }

  async function compareSelectedRuns() {
    if (selectedRunIds.length < 2 || selectedRunIds.length > 10) {
      setResearchError("Select between 2 and 10 saved backtest runs.");
      return;
    }
    setResearchBusy(true);
    setResearchError(null);
    try {
      setComparison(await backtestApi.compare(selectedRunIds));
    } catch (reason) {
      setResearchError(reason instanceof BacktestApiError ? reason.message : "Unable to compare selected research runs.");
    } finally {
      setResearchBusy(false);
    }
  }

  return (
    <main className="backtest-shell">
      <header className="backtest-topbar">
        <div><span className="eyebrow">ETF QUANT RESEARCH SYSTEM · BACKTEST LAB</span><h1>Backtest Lab</h1><p>Run an immutable strategy version and inspect backend analytics.</p></div>
        <button className="button button-secondary" type="button" onClick={onBack}>Back to Strategy Lab</button>
      </header>
      <div className="backtest-layout">
        <form className="panel backtest-form" onSubmit={(event) => void submit(event)}>
          <div className="section-header"><div><span className="eyebrow">RUN CONFIGURATION</span><h2>Research inputs</h2></div><span className="draft-label">New run every submit</span></div>
          {error && <div className="inline-error backtest-error" role="alert">{error}</div>}
          <label>Strategy<select aria-label="Backtest strategy" value={strategyId} onChange={(event) => setStrategyId(event.target.value)} disabled={busy === "catalog"}><option value="">Select strategy</option>{catalog.map((item) => <option key={item.strategy_id} value={item.strategy_id}>{item.name} · {item.strategy_id}</option>)}</select></label>
          <label>Strategy version<select aria-label="Backtest strategy version" value={versionId} onChange={(event) => setVersionId(event.target.value)} disabled={!strategyId || busy === "versions"}><option value="">Select immutable version</option>{versions.map((version) => <option key={version.version_id} value={version.version_id}>v{version.version_number} · {new Date(version.created_at).toLocaleString()}</option>)}</select></label>
          {selectedStrategy && <p className="muted">{selectedStrategy.version_count} saved version{selectedStrategy.version_count === 1 ? "" : "s"}; latest v{selectedStrategy.latest_version ?? "-"}.</p>}
          <div className="basic-grid"><label>Start date<input type="date" value={form.startDate} onChange={(event) => updateForm("startDate", event.target.value)} /></label><label>End date<input type="date" value={form.endDate} onChange={(event) => updateForm("endDate", event.target.value)} /></label><label>Initial capital<input type="number" min="0.01" step="0.01" value={form.initialCapital} onChange={(event) => updateForm("initialCapital", event.target.value)} /></label><label>Price field<select value={form.priceField} onChange={(event) => updateForm("priceField", event.target.value as PriceField)}><option value="adjusted_close">ADJUSTED_CLOSE</option><option value="raw_close">RAW_CLOSE</option></select></label><label>Benchmark symbol<input aria-label="Benchmark symbol" maxLength={16} placeholder="Optional, e.g. SPY" value={form.benchmarkSymbol} onChange={(event) => updateForm("benchmarkSymbol", event.target.value.toUpperCase())} /></label><label>Commission rate %<input type="number" min="0" step="0.01" value={form.commissionRatePercent} onChange={(event) => updateForm("commissionRatePercent", event.target.value)} /></label><label>Commission per order<input type="number" min="0" step="0.01" value={form.commissionPerOrder} onChange={(event) => updateForm("commissionPerOrder", event.target.value)} /></label><label>Slippage %<input type="number" min="0" max="99.99" step="0.01" value={form.slippagePercent} onChange={(event) => updateForm("slippagePercent", event.target.value)} /></label></div>
          <fieldset className={`contribution-config ${form.contributionsEnabled ? "is-enabled" : "is-disabled"}`}>
            <legend className="sr-only">Capital Contributions</legend>
            <div className="contribution-header">
              <div><span className="eyebrow">EXTERNAL CASH FLOW</span><h3>Capital Contributions</h3></div>
              <span className={`contribution-status ${form.contributionsEnabled ? "is-on" : "is-off"}`}>Contributions = {form.contributionsEnabled ? "On" : "Off"}</span>
            </div>
            <label className="contribution-toggle">
              <input aria-label="Enable contributions" aria-describedby="contribution-toggle-help" type="checkbox" checked={form.contributionsEnabled} onChange={(event) => updateForm("contributionsEnabled", event.target.checked)} />
              <span className="contribution-toggle-track" aria-hidden="true"><span /></span>
              <span><strong>Enable contributions</strong><small id="contribution-toggle-help">Add external cash flows during this backtest.</small></span>
            </label>
            {!form.contributionsEnabled && <p className="contribution-off-copy">No external cash flows will be added. Backtest configuration remains unchanged.</p>}
            {form.contributionsEnabled && <div className="contribution-details">
              <div className="contribution-principle" role="note">
                <strong>Contribution is external cash flow, not a strategy signal.</strong>
                <span>It enters Cash, then the current strategy target determines rebalance and execution. It does not automatically buy QQQ. Under a Cash 100% target, it remains cash until the strategy and rebalance policy deploys it.</span>
              </div>
              <div className="basic-grid contribution-fields">
                <label>Frequency<select aria-label="Contribution frequency" value={form.contributionFrequency} onChange={(event) => updateForm("contributionFrequency", event.target.value as ContributionFrequency)}><option value="one_time">One Time</option><option value="monthly">Monthly</option></select></label>
                <label>Amount USD<input aria-label="Contribution amount USD" type="number" min="0.01" step="0.01" value={form.contributionAmount} onChange={(event) => updateForm("contributionAmount", event.target.value)} /></label>
                {form.contributionFrequency === "one_time" && <label>Requested Date<input aria-label="Contribution requested date" aria-describedby="one-time-date-help" type="date" value={form.contributionDate} onChange={(event) => updateForm("contributionDate", event.target.value)} /></label>}
              </div>
              <p className="contribution-date-help" id={form.contributionFrequency === "one_time" ? "one-time-date-help" : undefined}>
                {form.contributionFrequency === "monthly" ? "Monthly requests use month start and map to the next common trading date when necessary." : "A Requested Date that is not a common trading date maps to the next common trading date."}{" "}
                Effective Date is produced by the backtest and is not an editable input.
              </p>
            </div>}
          </fieldset>
          <button className="button button-primary run-button" type="submit" disabled={busy !== null || !strategyId || !versionId}>{busy === "run" ? "Running backtest..." : "Run backtest"}</button>
        </form>
        <section className="backtest-context panel"><span className="eyebrow">EXECUTION CONTRACT</span><h2>Immutable research run</h2><dl><dt>Signal execution</dt><dd>Next trading day open</dd><dt>Statistics window</dt><dd>Requested dates only</dd><dt>Warm-up</dt><dd>Resolved from strategy indicators</dd><dt>Analytics</dt><dd>Backend calculated</dd></dl></section>
      </div>
      {run && <Results run={run} report={report} reportSeries={reportSeries} reportLoading={reportLoading} reportError={reportError} reportSeriesError={reportSeriesError} />}
      <section className="research-workspace">
        {researchError && <div className="inline-error backtest-error" role="alert">{researchError}</div>}
        <ResearchProtocolPanel
          availableVersionIds={versions.map((version) => version.version_id)}
          availableRunIds={researchRuns.map((item) => item.backtest_run_id)}
          preferredVersionId={versionId}
        />
        <ResearchHistory
          runs={researchRuns}
          selectedRunIds={selectedRunIds}
          sortBy={researchSortBy}
          order={researchOrder}
          loading={researchBusy}
          total={researchTotal}
          limit={RESEARCH_PAGE_SIZE}
          offset={researchOffset}
          onSortBy={(value) => {
            setResearchOffset(0);
            setResearchSortBy(value);
          }}
          onOrder={(value) => {
            setResearchOffset(0);
            setResearchOrder(value);
          }}
          onPrevious={() => setResearchOffset((current) => Math.max(0, current - RESEARCH_PAGE_SIZE))}
          onNext={() => setResearchOffset((current) => current + RESEARCH_PAGE_SIZE)}
          onToggle={toggleResearchRun}
          onCompare={() => void compareSelectedRuns()}
        />
        {comparison && <MultiStrategyComparison comparison={comparison} />}
      </section>
    </main>
  );
}
