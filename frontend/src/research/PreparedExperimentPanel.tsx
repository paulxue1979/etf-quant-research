import { useEffect, useState } from "react";

import { researchApi } from "./api";
import type { PendingExperimentConfiguration } from "./types";

interface Props {
  versionId: string | null;
  onOpenResults: (protocolId: string, experimentId: string) => void;
}

export function canApprovePreparedExperiment(plan: PendingExperimentConfiguration): boolean {
  return plan.approval_status === "PROPOSED"
    && plan.execution_status === "NOT_STARTED"
    && plan.validation_status === "READY"
    && plan.data_availability.status === "READY"
    && plan.invalid_candidates.length === 0
    && plan.grid_preflight.pruned_count === 0
    && plan.grid_preflight.duplicate_count === 0
    && plan.grid_preflight.executable_count === plan.candidate_count;
}

export function canResumePreparedExperiment(plan: PendingExperimentConfiguration): boolean {
  return plan.approval_status === "APPROVED"
    && (plan.execution_status === "RUNNING" || plan.execution_status === "FAILED");
}

export function PreparedExperimentPanel({ versionId, onOpenResults }: Props) {
  const [plans, setPlans] = useState<PendingExperimentConfiguration[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    const loaded = await researchApi.listPreparedExperiments();
    setPlans(loaded);
    setSelected((current) => current || loaded.find((item) => item.strategy_version_id === versionId)?.config_id || "");
  }

  useEffect(() => {
    let active = true;
    void researchApi.listPreparedExperiments().then((loaded) => {
      if (!active) return;
      setPlans(loaded);
      setSelected(loaded.find((item) => item.strategy_version_id === versionId)?.config_id ?? "");
    }).catch(() => { if (active) setError("Unable to load prepared experiments."); });
    return () => { active = false; };
  }, [versionId]);

  const plan = plans.find((item) => item.config_id === selected);

  async function decide(decision: "reject" | "approve") {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      await (decision === "reject"
        ? researchApi.rejectPreparedExperiment(plan.config_id)
        : researchApi.approvePreparedExperiment(plan.config_id));
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Experiment decision failed.");
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel" aria-label="Codex prepared experiment">
    <div className="section-header compact"><div><span className="eyebrow">CODEX PREPARED RESEARCH</span><h2>Pending IS grid</h2></div><button className="button button-secondary" type="button" disabled={busy} onClick={() => void reload()}>Refresh</button></div>
    {error && <p className="field-error" role="alert">{error}</p>}
    {plans.length === 0 ? <p className="muted">No prepared experiment configurations.</p> : <label>Configuration
      <select aria-label="Prepared experiment configuration" value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a configuration</option>
        {plans.map((item) => <option key={item.config_id} value={item.config_id}>{item.config_id} · {item.strategy_id} · {item.approval_status}</option>)}
      </select>
    </label>}
    {plan && <>
      <dl className="research-detail-list">
        <dt>Configuration</dt><dd>{plan.config_id}</dd>
        <dt>Source</dt><dd>{plan.source}</dd>
        <dt>Strategy</dt><dd>{plan.strategy_name} · {plan.strategy_id}</dd>
        <dt>Immutable version</dt><dd>{plan.strategy_version_id}</dd>
        <dt>Version content hash</dt><dd className="research-hash">{plan.strategy_version_hash}</dd>
        <dt>Research protocol</dt><dd>{plan.protocol_id}</dd>
        <dt>IS window</dt><dd>{plan.requested_is_start} to {plan.requested_is_end}</dd>
        <dt>Frozen OOS window</dt><dd>{plan.oos_start} to {plan.oos_end} (not executed here)</dd>
        <dt>Expected effective IS</dt><dd>{plan.data_availability.effective_start_expected ?? "Unavailable"} to {plan.data_availability.effective_end_expected ?? "Unavailable"}</dd>
        <dt>Initial capital</dt><dd>USD {plan.initial_capital.toLocaleString()}</dd>
        <dt>Price / execution</dt><dd>{plan.experiment_snapshot.backtest_configuration.price_field_used} · {plan.experiment_snapshot.backtest_configuration.execution_rule}</dd>
        <dt>Rebalance</dt><dd>{plan.experiment_snapshot.backtest_configuration.rebalance_policy.frequency} · threshold {plan.experiment_snapshot.backtest_configuration.rebalance_policy.threshold ?? "none"}</dd>
        <dt>Costs / shares</dt><dd>Commission {plan.experiment_snapshot.backtest_configuration.commission.rate} / {plan.experiment_snapshot.backtest_configuration.commission.per_order} · slippage {plan.experiment_snapshot.backtest_configuration.slippage} · {plan.experiment_snapshot.backtest_configuration.fractional_shares ? "fractional" : "integer"} shares</dd>
        <dt>Data source</dt><dd>Cache-only preflight; canonical grid execution</dd>
        <dt>Candidate budget</dt><dd>{plan.candidate_count} of 16</dd>
        <dt>Preflight</dt><dd>{plan.validation_status} · data {plan.data_availability.status} · grid {plan.grid_preflight.status}</dd>
        <dt>Candidate counts</dt><dd>Theoretical {plan.grid_preflight.theoretical_count} · executable {plan.grid_preflight.executable_count} · pruned {plan.grid_preflight.pruned_count} · duplicates {plan.grid_preflight.duplicate_count}</dd>
        <dt>Approval / execution</dt><dd>{plan.approval_status} / {plan.execution_status}</dd>
        <dt>Configuration hash</dt><dd className="research-hash">{plan.configuration_hash}</dd>
      </dl>
      <h3>Semantic parameter axes</h3>
      <ul>{plan.axes.map((axis) => <li key={axis.semantic_target_id}>{axis.semantic_target_id}: {axis.values.join(", ")} {axis.unit}</li>)}</ul>
      {plan.invalid_candidates.length > 0 && <div role="alert"><strong>Invalid combinations</strong><ul>{plan.invalid_candidates.map((item) => <li key={item.source_index}>#{item.source_index}: {Object.entries(item.values).map(([key, value]) => `${key}=${value}`).join(", ")} · {item.reason}</li>)}</ul></div>}
      {plan.validation_issues.map((issue) => <p className="field-error" key={issue}>{issue}</p>)}
      {plan.data_availability.blocking_issues.map((issue, index) => <p className="field-error" key={index}>{typeof issue === "string" ? issue : JSON.stringify(issue)}</p>)}
      {plan.error_code && <p className="field-error">{plan.error_code}</p>}
      <div className="action-buttons"><button className="button button-secondary" type="button" disabled={busy || plan.approval_status !== "PROPOSED"} onClick={() => void decide("reject")}>Reject</button><button className="button button-primary" type="button" disabled={busy || !(canApprovePreparedExperiment(plan) || canResumePreparedExperiment(plan))} onClick={() => void decide("approve")}>{canResumePreparedExperiment(plan) ? "Resume IS Grid" : "Approve & Run IS Grid"}</button></div>
      {plan.execution_status === "COMPLETED" && <button className="button button-secondary" type="button" onClick={() => onOpenResults(plan.protocol_id, plan.experiment_id)}>Open candidate results</button>}
    </>}
  </section>;
}
