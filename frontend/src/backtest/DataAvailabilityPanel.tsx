import type { DataAvailabilityIssue, DataAvailabilityProjection } from "./types";

function statusClass(status: DataAvailabilityProjection["status"]): string {
  return status === "READY" ? "is-valid" : status === "BLOCKED" ? "is-error" : "is-warning";
}

export function DataAvailabilityPanel({ projection, loading, error }: {
  projection: DataAvailabilityProjection | null;
  loading: boolean;
  error: string | null;
}) {
  const issueText = (issue: DataAvailabilityIssue) => `${issue.code}: ${issue.message}`;
  return <section className="panel data-availability-panel" aria-label="Data and execution preflight">
    <div className="section-header compact">
      <div><span className="eyebrow">DATA &amp; EXECUTION PREFLIGHT</span><h2>Availability before execution</h2></div>
      {projection && <span className={`status-pill ${statusClass(projection.status)}`}>{projection.status.replaceAll("_", " ")}</span>}
    </div>
    {loading && <p className="muted">Checking the selected immutable version and market-data range...</p>}
    {error && <p className="research-error" role="alert">{error}</p>}
    {!loading && !error && !projection && <p className="muted">Select an immutable StrategyVersion to preview requested, effective, and warm-up ranges.</p>}
    {projection && <>
      <dl className="research-detail-list">
        <dt>Requested research range</dt><dd>{projection.requested_start} → {projection.requested_end}</dd>
        <dt>Expected effective trading range</dt><dd>{projection.effective_start_expected ?? "Unavailable"} → {projection.effective_end_expected ?? "Unavailable"}</dd>
        <dt>Warm-up request start</dt><dd>{projection.warmup_start}</dd>
        <dt>Price field</dt><dd>{projection.price_field === "adjusted_close" ? "Adjusted Close" : "Raw Close"}</dd>
      </dl>
      {projection.indicator_requirements.length > 0 && <div className="data-availability-requirements"><strong>Indicator requirements</strong>{projection.indicator_requirements.map((item) => <span key={`${item.asset}-${item.timeframe}-${item.period}`}>{item.asset} · {item.timeframe.toUpperCase()} · {item.period} completed bars</span>)}</div>}
      <div className="data-availability-assets"><strong>Asset availability</strong>{projection.assets.map((asset) => <div key={asset.symbol}><span>{asset.symbol}</span><span>{asset.first_available_date ?? "Unavailable"} → {asset.last_available_date ?? "Unavailable"}</span>{asset.warnings?.map((warning) => <small key={`${asset.symbol}-${warning.code}`}>{issueText(warning)}</small>)}</div>)}</div>
      {projection.info.map((info) => <p className="muted" key={`${info.code}-${info.message}`}>Info: {issueText(info)}</p>)}
      {projection.warnings.map((warning) => <p className="muted" key={`${warning.code}-${warning.message}`}>Warning: {issueText(warning)}</p>)}
      {projection.blocking_issues.map((issue) => <p className="field-error" role="alert" key={`${issue.code}-${issue.message}`}>Blocking: {issueText(issue)}</p>)}
    </>}
  </section>;
}
