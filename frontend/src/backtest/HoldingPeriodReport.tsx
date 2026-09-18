import { useEffect, useState } from "react";

import { backtestApi, BacktestApiError } from "./api";
import type {
  HoldingFilterStatus,
  HoldingReport,
  HoldingReportItem,
  HoldingSort,
} from "./types";

interface HoldingPeriodReportProps {
  backtestRunId: string;
}

const PAGE_SIZE = 25;

function money(value: number | null): string {
  return value === null || !Number.isFinite(value)
    ? "N/A"
    : value.toLocaleString(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      });
}

function price(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : value.toFixed(4);
}

function percent(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "N/A" : `${(value * 100).toFixed(2)}%`;
}

function signalReason(item: HoldingReportItem, side: "entry" | "exit"): string {
  const rule = side === "entry" ? item.entry_matched_rule_id : item.exit_matched_rule_id;
  const source = side === "entry" ? item.entry_allocation_source : item.exit_allocation_source;
  return rule ?? source ?? "N/A";
}

function executionCause(item: HoldingReportItem): string {
  const entry = item.entry_execution_cause.toUpperCase();
  return item.exit_execution_cause
    ? `${entry} → ${item.exit_execution_cause.toUpperCase()}`
    : entry;
}

export function HoldingPeriodReport({ backtestRunId }: HoldingPeriodReportProps) {
  const [report, setReport] = useState<HoldingReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<HoldingFilterStatus>("ALL");
  const [symbolInput, setSymbolInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const [sortBy, setSortBy] = useState<HoldingSort>("entry_date");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void backtestApi.getHoldingReport(backtestRunId, {
      status,
      symbol: symbol || undefined,
      limit: PAGE_SIZE,
      offset,
      sortBy,
      order,
    }).then((next) => {
      if (active) setReport(next);
    }).catch((reason: unknown) => {
      if (active) {
        setError(
          reason instanceof BacktestApiError
            ? reason.message
            : "Unable to load the holding period report.",
        );
      }
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [backtestRunId, offset, order, sortBy, status, symbol]);

  const applySymbol = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setOffset(0);
    setSymbol(symbolInput.trim().toUpperCase());
  };
  const changeStatus = (value: HoldingFilterStatus) => {
    setOffset(0);
    setStatus(value);
  };
  const changeSort = (value: HoldingSort) => {
    setOffset(0);
    setSortBy(value);
  };

  return <section className="panel holding-report" aria-label="Holding Period Report">
    <div className="section-header compact">
      <div><span className="eyebrow">FIFO LOT PROVENANCE</span><h2>Holding Period Report</h2></div>
      <span className="muted">{report?.status === "available" ? `${report.total} segments` : "Canonical lots"}</span>
    </div>
    <p className="muted holding-contract">Calendar-day duration from real entry execution to real exit execution or report end. Trade rows and holding lots remain separate.</p>
    <div className="holding-controls">
      <label>Status<select aria-label="Holding status" value={status} onChange={(event) => changeStatus(event.target.value as HoldingFilterStatus)}><option value="ALL">All</option><option value="OPEN">Open</option><option value="CLOSED">Closed</option></select></label>
      <form onSubmit={applySymbol}><label>Symbol<input aria-label="Holding symbol" value={symbolInput} onChange={(event) => setSymbolInput(event.target.value)} maxLength={32} /></label><button className="button button-secondary" type="submit">Apply</button></form>
      <label>Sort<select aria-label="Holding sort" value={sortBy} onChange={(event) => changeSort(event.target.value as HoldingSort)}><option value="entry_date">Entry date</option><option value="exit_date">Exit date</option><option value="symbol">Symbol</option><option value="holding_return">Return</option><option value="pnl">P&amp;L</option><option value="duration">Duration</option></select></label>
      <label>Order<select aria-label="Holding order" value={order} onChange={(event) => { setOffset(0); setOrder(event.target.value as "asc" | "desc"); }}><option value="asc">Ascending</option><option value="desc">Descending</option></select></label>
    </div>
    {loading && !report && <p className="muted">Loading canonical holding segments...</p>}
    {error && <p className="research-error">{error}</p>}
    {!error && report?.status === "not_available" && <p className="holding-unavailable">{report.reason}</p>}
    {!error && report?.status === "available" && <>
      <div className="holding-summary"><span>Open <strong>{report.summary.open_count}</strong></span><span>Closed <strong>{report.summary.closed_count}</strong></span><span>MFE / MAE / Holding Drawdown <strong>NOT AVAILABLE</strong></span></div>
      {report.items.length === 0 ? <p className="muted">No holding segments match the current filters.</p> : <div className="table-scroll"><table className="holding-table"><thead><tr><th>#</th><th>Symbol</th><th>Status</th><th>Quantity</th><th>Entry Signal</th><th>Entry Execution</th><th>Entry Price</th><th>Exit Signal</th><th>Exit Execution</th><th>Exit Price</th><th>Days</th><th>Return</th><th>P&amp;L</th><th>Execution / Reason</th></tr></thead><tbody>{report.items.map((item, index) => <tr key={item.holding_id} className={`holding-${item.status.toLowerCase()}`}><td>{report.offset + index + 1}</td><td>{item.symbol}</td><td><span className="holding-status">{item.status}</span></td><td>{item.quantity}</td><td data-testid={`${item.holding_id}-entry-signal`}>{item.entry_signal_date ?? "N/A"}</td><td>{item.entry_execution_date}</td><td>{price(item.entry_price)}</td><td>{item.exit_signal_date ?? "N/A"}</td><td data-testid={`${item.holding_id}-exit-execution`}>{item.exit_execution_date ?? "—"}</td><td>{price(item.exit_price)}</td><td>{item.holding_days}</td><td>{percent(item.holding_return)}</td><td title={item.pnl_type}>{money(item.pnl)}</td><td><strong>{executionCause(item)}</strong><small>{signalReason(item, "entry")}{item.status === "CLOSED" ? ` → ${signalReason(item, "exit")}` : ""}</small></td></tr>)}</tbody></table></div>}
      <div className="holding-pagination"><button className="button button-secondary" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{report.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, report.total)} of {report.total}</span><button className="button button-secondary" type="button" disabled={offset + PAGE_SIZE >= report.total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
    </>}
  </section>;
}
