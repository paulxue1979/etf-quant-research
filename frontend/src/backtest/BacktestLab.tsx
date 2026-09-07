import { useEffect, useMemo, useState } from "react";

import { BacktestApiError, backtestApi } from "./api";
import type {
  BacktestRequest,
  BacktestRun,
  DrawdownPoint,
  EquityPoint,
  MetricValue,
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
};

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

function Results({ run }: { run: BacktestRun }) {
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
  const [busy, setBusy] = useState<"catalog" | "versions" | "run" | null>("catalog");
  const [error, setError] = useState<string | null>(null);

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
    if (!strategyId || !versionId) {
      setError("Select a strategy and an immutable version before running.");
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
    };
    setBusy("run");
    try {
      setRun(await backtestApi.create(request));
    } catch (reason) {
      setError(reason instanceof BacktestApiError ? reason.message : "Backtest request failed.");
    } finally {
      setBusy(null);
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
          <div className="basic-grid"><label>Start date<input type="date" value={form.startDate} onChange={(event) => updateForm("startDate", event.target.value)} /></label><label>End date<input type="date" value={form.endDate} onChange={(event) => updateForm("endDate", event.target.value)} /></label><label>Initial capital<input type="number" min="0.01" step="0.01" value={form.initialCapital} onChange={(event) => updateForm("initialCapital", event.target.value)} /></label><label>Price field<select value={form.priceField} onChange={(event) => updateForm("priceField", event.target.value as PriceField)}><option value="adjusted_close">ADJUSTED_CLOSE</option><option value="raw_close">RAW_CLOSE</option></select></label><label>Commission rate %<input type="number" min="0" step="0.01" value={form.commissionRatePercent} onChange={(event) => updateForm("commissionRatePercent", event.target.value)} /></label><label>Commission per order<input type="number" min="0" step="0.01" value={form.commissionPerOrder} onChange={(event) => updateForm("commissionPerOrder", event.target.value)} /></label><label>Slippage %<input type="number" min="0" max="99.99" step="0.01" value={form.slippagePercent} onChange={(event) => updateForm("slippagePercent", event.target.value)} /></label></div>
          <button className="button button-primary run-button" type="submit" disabled={busy !== null || !strategyId || !versionId}>{busy === "run" ? "Running backtest..." : "Run backtest"}</button>
        </form>
        <section className="backtest-context panel"><span className="eyebrow">EXECUTION CONTRACT</span><h2>Immutable research run</h2><dl><dt>Signal execution</dt><dd>Next trading day open</dd><dt>Statistics window</dt><dd>Requested dates only</dd><dt>Warm-up</dt><dd>Resolved from strategy indicators</dd><dt>Analytics</dt><dd>Backend calculated</dd></dl></section>
      </div>
      {run && <Results run={run} />}
    </main>
  );
}
