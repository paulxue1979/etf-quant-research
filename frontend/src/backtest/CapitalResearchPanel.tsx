import { useEffect, useState } from "react";

import type {
  BacktestReport,
  ContributionReportEvent,
  ContributionScheduleReport,
  MetricValue,
} from "./types";

interface CapitalResearchPanelProps {
  report: BacktestReport | null;
  loading?: boolean;
  error?: string | null;
}

const PAGE_SIZE = 25;

function money(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : "Not available";
}

function contributionMoney(value: string | null): string {
  const numeric = Number(value);
  return value !== null && Number.isFinite(numeric) ? money(numeric) : "Not available";
}

function percentage(metric: MetricValue | undefined): { value: string; reason?: string } {
  if (metric?.status === "available" && Number.isFinite(metric.value)) {
    return { value: `${(Number(metric.value) * 100).toFixed(2)}%` };
  }
  return {
    value: metric?.status === "not_evaluable" ? "Not evaluable" : "Not available",
    reason: metric?.reason ?? undefined,
  };
}

function scheduleTitle(schedule: ContributionScheduleReport): string {
  return schedule.frequency === "monthly" ? "Monthly Contribution" : "One-Time Contribution";
}

function scheduleSemantics(schedule: ContributionScheduleReport): string {
  return schedule.requested_date_semantics === "month_start"
    ? "Month Start"
    : schedule.requested_date ?? "Explicit Date";
}

function deploymentText(event: ContributionReportEvent): string {
  return event.deployment.status === "rebalance_executed"
    ? "Portfolio rebalance triggered"
    : "No contribution-triggered rebalance execution";
}

export function CapitalResearchPanel({
  report,
  loading = false,
  error = null,
}: CapitalResearchPanelProps) {
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [report?.identity.backtest_run_id]);

  if (loading) {
    return <section className="panel capital-research" aria-label="Capital and contribution research"><p className="muted">Loading capital research...</p></section>;
  }
  if (error) {
    return <section className="panel capital-research" aria-label="Capital and contribution research"><p className="research-error">{error}</p></section>;
  }
  if (!report) {
    return <section className="panel capital-research" aria-label="Capital and contribution research"><p className="muted">Capital research is not available for this run.</p></section>;
  }

  const contributionReport = report.contribution_report;
  const twr = percentage(report.performance.twr_total_return);
  const xirr = percentage(report.investor_experience.xirr);
  const events = contributionReport.events.slice(offset, offset + PAGE_SIZE);
  const schedule = contributionReport.schedule;
  const benchmark = report.summary.benchmark;
  const sameCashFlows = benchmark?.status === "available"
    && benchmark.provenance?.same_contribution_schedule === true;

  return <section className="capital-research" aria-label="Capital and contribution research">
    <section className="panel capital-summary">
      <div className="section-header compact"><div><span className="eyebrow">CAPITAL BASIS</span><h2>Capital Summary</h2></div><span className="muted">Canonical backend values</span></div>
      <div className="capital-summary-grid">
        {[
          ["Initial Capital", report.capital.initial_capital],
          ["Cumulative Contributions", report.capital.cumulative_contributions],
          ["Total Capital Invested", report.capital.total_capital_invested],
          ["Ending Portfolio Value", report.summary.account?.ending_value],
          ["Investment Profit", report.profit.investment_profit],
        ].map(([label, value]) => <div className="capital-summary-item" key={String(label)}><span>{label}</span><strong>{money(value as number | null | undefined)}</strong></div>)}
      </div>
      <p className="capital-semantics">Account value is current portfolio value. Capital invested is external principal. Investment profit is their dollar difference and is not TWR or XIRR.</p>
    </section>

    <section className="panel contribution-panel">
      <div className="section-header compact"><div><span className="eyebrow">BACKTEST CONFIGURATION</span><h2>Contribution Schedule</h2></div><span className="muted">Not a strategy rule</span></div>
      {contributionReport.status === "not_available" ? <p className="holding-unavailable">{contributionReport.reason ?? "Contribution data not available"}</p> : <>
        {schedule?.enabled ? <dl className="contribution-schedule-summary"><div><dt>{scheduleTitle(schedule)}</dt><dd>{contributionMoney(schedule.amount)}</dd></div><div><dt>Schedule</dt><dd>{scheduleSemantics(schedule)}</dd></div><div><dt>Contribution Count</dt><dd>{contributionReport.event_count}</dd></div></dl> : <p className="muted">Contributions disabled for this backtest configuration.</p>}
        {contributionReport.integrity.status === "inconsistent" && <p className="research-error">Contribution provenance failed its backend consistency check.</p>}
        <div className="subsection-heading"><h3>Contribution Timeline</h3><span className="muted">Effective date ascending</span></div>
        {contributionReport.events.length === 0 ? <p className="muted">No external contributions</p> : <>
          <div className="table-scroll contribution-table-scroll"><table className="contribution-table"><thead><tr><th>#</th><th>Requested Date</th><th>Effective Date</th><th>Amount</th><th>Schedule</th><th>Strategy Signal</th><th>Deployment</th></tr></thead><tbody>{events.map((event) => <tr key={`${event.sequence}-${event.requested_date}-${event.effective_date}`}><td>{event.sequence}</td><td>{event.requested_date}</td><td>{event.effective_date}</td><td>{contributionMoney(event.amount)}</td><td>{event.frequency === "monthly" ? "Monthly" : "One-Time"}</td><td>NONE</td><td><strong>{deploymentText(event)}</strong>{event.deployment.order_count > 0 && <small>{event.deployment.order_count} orders · {event.deployment.fill_count} fills · {event.deployment.symbols.join(", ") || "No security"}</small>}</td></tr>)}</tbody></table></div>
          <div className="holding-pagination"><button className="button button-secondary" type="button" aria-label="Previous contribution page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{offset + 1}–{Math.min(offset + PAGE_SIZE, contributionReport.event_count)} of {contributionReport.event_count}</span><button className="button button-secondary" type="button" aria-label="Next contribution page" disabled={offset + PAGE_SIZE >= contributionReport.event_count} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
        </>}
      </>}
    </section>

    <section className="capital-return-grid" aria-label="Strategy and investor returns">
      <article className="panel return-panel"><span className="eyebrow">STRATEGY</span><h2>Strategy Performance (TWR)</h2><strong>{twr.value}</strong><p>Measures strategy performance after removing the timing effect of external contributions.</p>{twr.reason && <small>{twr.reason}</small>}</article>
      <article className="panel return-panel"><span className="eyebrow">INVESTOR</span><h2>Investor Experience (XIRR)</h2><strong>{xirr.value}</strong><p>Annualized investor return using actual contribution amounts and dates.</p>{xirr.reason && <small>{xirr.reason}</small>}</article>
    </section>

    {benchmark?.status === "available" && <section className="panel benchmark-capital-note"><div><span className="eyebrow">BENCHMARK</span><h2>Capital-Fair Comparison</h2></div><div>{sameCashFlows && <span className="holding-status">Same cash-flow schedule</span>}<strong>{money(benchmark.ending_value?.value)}</strong><small>Benchmark ending value</small></div></section>}
  </section>;
}
