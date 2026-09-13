import { useState } from "react";

import { researchApi, ResearchApiError } from "./api";
import type { MetricValue, OosResearchView as OosResearchViewModel } from "./types";

interface OosResearchViewProps {
  onBack: () => void;
}

function text(value: unknown, fallback = "Unavailable"): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : fallback;
}

function metricText(metric: MetricValue | undefined, percentage = false): string {
  if (!metric || metric.status !== "available" || metric.value === null) return "Not Evaluable";
  return `${(percentage ? metric.value * 100 : metric.value).toFixed(4)}${percentage ? "%" : ""}`;
}

function statusLabel(value: unknown): string {
  return text(value, "unknown").replaceAll("_", " ").toUpperCase();
}

function linePoints(points: Array<{ date: string; value: number }>): string {
  if (!points.length) return "";
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  return points.map((point, index) => {
    const x = points.length === 1 ? 350 : (index / (points.length - 1)) * 700;
    const y = 190 - ((point.value - min) / span) * 160;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function ReadOnlySeries({ label, points, color }: { label: string; points: Array<{ date: string; value: number }>; color: string }) {
  return <div className="series-chart"><div className="subsection-heading"><h3>{label}</h3><span className="muted">Official backend series</span></div>{points.length === 0 ? <p className="muted">No series data returned.</p> : <><svg className="series-svg" viewBox="0 0 700 220" role="img" aria-label={label}><line x1="0" y1="190" x2="700" y2="190" stroke="#2b3942" /><polyline points={linePoints(points)} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" /></svg><div className="chart-timeline"><span>{points[0].date}</span><span>{points.at(-1)?.date}</span></div></>}</div>;
}

function MetricGrid({ metrics }: { metrics: Record<string, MetricValue> }) {
  const definitions: Array<[string, string, boolean]> = [
    ["Total return", "total_return", true], ["CAGR", "cagr", true], ["Volatility", "annualized_volatility", true],
    ["Sharpe", "sharpe_ratio", false], ["Sortino", "sortino_ratio", false], ["Max drawdown", "max_drawdown", true],
    ["Drawdown duration", "max_drawdown_duration", false], ["Recovery", "recovery_duration", false], ["Calmar", "calmar_ratio", false],
    ["Win rate", "win_rate", true], ["Profit factor", "profit_factor", false], ["Average trade return", "average_trade_return", true],
    ["Best trade", "best_trade", true], ["Worst trade", "worst_trade", true], ["Average holding period", "average_holding_period", false],
  ];
  return <div className="backtest-metrics">{definitions.map(([label, key, percentage]) => <div className="backtest-metric" key={key}><span>{label}</span><strong>{metricText(metrics[key], percentage)}</strong>{metrics[key]?.reason && <small>{metrics[key].reason}</small>}</div>)}</div>;
}

function Provenance({ view }: { view: OosResearchViewModel }) {
  const p = view.provenance;
  const rows: Array<[string, unknown]> = [
    ["Protocol ID", p.protocol_id], ["OOS Result ID", p.oos_result_id], ["Backtest Run ID", p.backtest_run_id], ["Execution ID", p.execution_id],
    ["Strategy Version ID", p.strategy_version_id], ["Strategy Content Hash", p.strategy_content_hash], ["Selection Decision ID", p.selection_decision_id], ["Strategy Freeze ID", p.strategy_freeze_id],
    ["IS range", `${text(p.is_start)} to ${text(p.is_end)}`], ["Warm-up start", p.warmup_start], ["OOS range", `${text(p.oos_start)} to ${text(p.oos_end)}`],
    ["Price field", p.price_field_used], ["Initial capital", p.initial_capital], ["Commission", p.commission], ["Slippage", p.slippage], ["Execution rule", p.execution_rule],
    ["Fractional shares", p.fractional_shares], ["Rebalance policy", JSON.stringify(p.rebalance_policy)], ["Engine version", p.engine_version], ["Analytics version", p.analytics_version],
    ["Asset universe", Array.isArray(p.asset_universe) ? p.asset_universe.join(", ") : undefined], ["Data provenance", JSON.stringify(p.data_provenance)],
  ];
  return <dl className="research-detail-list oos-provenance-list">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className={label.includes("Hash") || label.includes("ID") ? "research-hash" : undefined}>{text(value)}</dd></div>)}</dl>;
}

export function OosResearchView({ onBack }: OosResearchViewProps) {
  const [protocolId, setProtocolId] = useState("");
  const [view, setView] = useState<OosResearchViewModel | null>(null);
  const [error, setError] = useState<ResearchApiError | string | null>(null);
  const [loading, setLoading] = useState(false);

  async function loadView() {
    const protocol = protocolId.trim();
    if (!protocol) { setError("Enter a protocol ID."); return; }
    setLoading(true); setError(null);
    try { setView(await researchApi.getOfficialOosResearchView(protocol)); }
    catch (reason) { setView(null); setError(reason instanceof ResearchApiError ? `${reason.message} (${reason.code})` : "Official OOS result could not be loaded."); }
    finally { setLoading(false); }
  }

  const result = view?.oos_result;
  const run = view?.backtest_run;
  const backtestResult = run?.backtest_result;
  return <main className="research-shell"><header className="backtest-topbar"><div><span className="eyebrow">ETF QUANT RESEARCH SYSTEM · OFFICIAL OOS</span><h1>OOS Research View</h1><p>A read-only view of one finalized out-of-sample evaluation.</p></div><div className="topbar-status"><button className="button button-secondary" type="button" onClick={onBack}>Back to Strategy Lab</button></div></header>
    <section className="panel research-loader"><div className="research-controls"><label>Protocol ID<input aria-label="OOS Protocol ID" value={protocolId} onChange={(event) => setProtocolId(event.target.value)} placeholder="protocol-id" /></label><button className="button button-primary" type="button" disabled={loading} onClick={() => void loadView()}>{loading ? "Loading..." : view ? "Refresh view" : "Load official OOS"}</button></div>{error && <p className="research-error" role="alert">{error instanceof ResearchApiError ? `${error.message} (${error.code})` : error}</p>}</section>
    {view && result && run && backtestResult && <div className="research-workspace oos-view">
      <section className="panel"><div className="section-header compact"><div><span className="eyebrow">OFFICIAL OOS EVALUATION</span><h2>{result.oos_result_id}</h2></div><span className="status-pill is-valid">FINALIZED · READ ONLY</span></div><p className="oos-statement">This result comes from the pre-frozen strategy evaluated once over the frozen OOS range. It is an observation, not an optimization, selection, recommendation, forecast, or live-trading result.</p><dl className="research-detail-list summary-list"><dt>Protocol state</dt><dd>{statusLabel(view.protocol.status)}</dd><dt>Strategy version</dt><dd>{result.strategy_version_id}</dd><dt>OOS range</dt><dd>{result.oos_start} to {result.oos_end}</dd><dt>Backtest run</dt><dd>{result.backtest_run_id}</dd><dt>Final equity</dt><dd>{backtestResult.final_equity.toLocaleString(undefined, { style: "currency", currency: "USD" })}</dd></dl></section>
      <section className="panel"><div className="section-header compact"><div><span className="eyebrow">PERFORMANCE</span><h2>Persisted analytics</h2></div><span className="muted">No metrics recalculated in this view</span></div><MetricGrid metrics={result.performance_summary.metrics} /></section>
      <section className="panel chart-grid"><ReadOnlySeries label="OOS equity curve" points={backtestResult.equity_curve.map((point) => ({ date: point.date, value: point.total_equity }))} color="#6bd7d0" /><ReadOnlySeries label="OOS drawdown curve" points={run.performance_analysis.drawdown_curve} color="#ff8b8b" /></section>
      <section className="panel result-tables"><div className="table-block"><div className="subsection-heading"><h3>Trades</h3><span className="muted">Persisted closed trades</span></div>{backtestResult.trades.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>Qty</th><th>P&amp;L</th><th>Return</th><th>Hold</th></tr></thead><tbody>{backtestResult.trades.map((trade) => <tr key={`${trade.symbol}-${trade.entry_date}-${trade.exit_date}`}><td>{trade.symbol}</td><td>{trade.entry_date}</td><td>{trade.exit_date}</td><td>{trade.quantity}</td><td>{trade.pnl.toFixed(2)}</td><td>{(trade.pnl_pct * 100).toFixed(2)}%</td><td>{trade.holding_period}d</td></tr>)}</tbody></table></div> : <p className="muted">No closed trades.</p>}</div><div className="table-block"><div className="subsection-heading"><h3>Allocation history</h3><span className="muted">Persisted backend output</span></div>{backtestResult.allocation_history.length ? <div className="table-scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Target</th><th>Actual</th></tr></thead><tbody>{backtestResult.allocation_history.map((item, index) => <tr key={`${item.date}-${item.symbol}-${index}`}><td>{item.date}</td><td>{item.symbol}</td><td>{(item.target_weight * 100).toFixed(1)}%</td><td>{(item.actual_weight * 100).toFixed(1)}%</td></tr>)}</tbody></table></div> : <p className="muted">No allocation history.</p>}</div><div className="table-block"><div className="subsection-heading"><h3>Latest positions</h3><span className="muted">Persisted snapshot</span></div>{backtestResult.positions.at(-1)?.positions.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Value</th></tr></thead><tbody>{backtestResult.positions.at(-1)?.positions.map((position) => <tr key={position.symbol}><td>{position.symbol}</td><td>{position.quantity}</td><td>{position.market_price.toFixed(2)}</td><td>{position.market_value.toFixed(2)}</td></tr>)}</tbody></table></div> : <p className="muted">No position snapshot.</p>}</div></section>
      <section className="panel result-tables"><div className="table-block"><div className="subsection-heading"><h3>Orders</h3><span className="muted">Signal(T Close) to T+1 open</span></div><div className="table-scroll"><table><thead><tr><th>Signal date</th><th>Execution date</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th></tr></thead><tbody>{backtestResult.orders.map((order) => <tr key={order.order_id}><td>{order.signal_date}</td><td>{order.date}</td><td>{order.symbol}</td><td>{order.side}</td><td>{order.quantity}</td><td>{order.status}</td></tr>)}</tbody></table></div></div><div className="table-block"><div className="subsection-heading"><h3>Fills</h3><span className="muted">Persisted executions</span></div><div className="table-scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead><tbody>{backtestResult.fills.map((fill) => <tr key={`${fill.order_id}-${fill.date}`}><td>{fill.date}</td><td>{fill.symbol}</td><td>{fill.side}</td><td>{fill.quantity}</td><td>{fill.price.toFixed(2)}</td></tr>)}</tbody></table></div></div></section>
      <section className="panel"><div className="section-header compact"><div><span className="eyebrow">PROVENANCE</span><h2>Frozen identity and boundaries</h2></div><span className="status-pill is-valid">IMMUTABLE</span></div><p className="oos-statement">Warm-up only initializes indicators and is excluded from OOS performance. OOS Signals Only. Portfolio initialization: Fresh Capital. Execution: Signal(T Close) -&gt; T+1 Trading Day Open.</p><Provenance view={view} /></section>
    </div>}
  </main>;
}
