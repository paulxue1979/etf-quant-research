import { useEffect, useRef, useState } from "react";

import { strategyApi, StrategyApiError } from "./api";
import type { ControlledVersionRequest, StrategyPayload } from "./types";

interface Props {
  strategyId: string;
  draftRevision: number;
  isDirty: boolean;
  onCreated: () => void;
}

const gateReason: Record<string, string> = {
  DRAFT_STALE: "Draft revision or hash changed. Ask Codex to prepare a new request.",
  VERSION_BASE_STALE: "A newer immutable version exists. Ask Codex to prepare a new request.",
  STRATEGY_INACTIVE: "This strategy is not active.",
  DRAFT_INVALID: "The saved Draft no longer passes validation.",
  REJECTED: "This request was rejected.",
  CREATED: "This request already created a version.",
  STALE: "This request is stale.",
  FAILED: "This request failed; prepare a new request.",
};

function snapshotDetails(snapshot: StrategyPayload) {
  const regimes = Array.isArray(snapshot.regimes) ? snapshot.regimes : [];
  const transitions = Array.isArray(snapshot.transitions) ? snapshot.transitions : [];
  const named = (value: unknown, fallback: string): string => {
    if (typeof value !== "object" || value === null) return fallback;
    const item = value as Record<string, unknown>;
    const allocation = item.target_allocation as { allocations?: Array<{ symbol: string; target_weight: number }> } | undefined;
    const weights = allocation?.allocations?.map((entry) => `${entry.symbol} ${Math.round(entry.target_weight * 100)}%`).join(", ");
    const transition = item.from_state && item.to_state ? `${item.from_state} → ${item.to_state}` : null;
    const label = String(item.display_name ?? item.name ?? item.state_id ?? item.transition_id ?? fallback);
    return `${transition ?? label}${weights ? ` (${weights})` : ""}`;
  };
  return (
    <>
      <dl className="prepared-backtest-summary">
        <div><dt>Mode</dt><dd>{snapshot.strategy_mode ?? "rule_based"}</dd></div>
        <div><dt>Assets</dt><dd>{snapshot.assets.map((asset) => asset.symbol).join(", ")}</dd></div>
        <div><dt>Price field</dt><dd>{snapshot.price_field}</dd></div>
        <div><dt>Rebalance</dt><dd>{snapshot.rebalance_policy.frequency} · threshold {String(snapshot.rebalance_policy.threshold ?? "none")}</dd></div>
        <div><dt>Rules</dt><dd>{snapshot.rules.map((rule) => rule.name).join(", ") || "None"}</dd></div>
        <div><dt>Regimes</dt><dd>{regimes.map((item, index) => named(item, `State ${index + 1}`)).join(", ") || "None"}</dd></div>
        <div><dt>Transitions</dt><dd>{transitions.map((item, index) => named(item, `Transition ${index + 1}`)).join(", ") || "None"}</dd></div>
        <div><dt>Fallback</dt><dd>{snapshot.fallback?.allocations.map((item) => `${item.symbol} ${item.target_weight}`).join(", ") ?? "Not active"}</dd></div>
      </dl>
      <details><summary>Exact saved Draft snapshot</summary><pre>{JSON.stringify(snapshot, null, 2)}</pre></details>
    </>
  );
}

export function ControlledVersionRequests({ strategyId, draftRevision, isDirty, onCreated }: Props) {
  const [requests, setRequests] = useState<ControlledVersionRequest[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadSequence = useRef(0);

  async function refresh() {
    const sequence = ++loadSequence.current;
    try {
      const items = await strategyApi.listVersionRequests(strategyId);
      if (sequence !== loadSequence.current) return;
      setRequests(items);
      setError(null);
    } catch {
      if (sequence !== loadSequence.current) return;
      setRequests([]);
      setError("Version requests could not be loaded. Approval is unavailable.");
    }
  }

  useEffect(() => {
    void refresh();
    return () => { loadSequence.current += 1; };
  }, [strategyId, draftRevision]);

  async function decide(requestId: string, approve: boolean) {
    setBusy(requestId);
    setError(null);
    let decisionError: string | null = null;
    try {
      const result = approve
        ? await strategyApi.approveVersionRequest(strategyId, requestId)
        : await strategyApi.rejectVersionRequest(strategyId, requestId);
      if (result.status === "CREATED") onCreated();
    } catch (reason) {
      decisionError = reason instanceof StrategyApiError && reason.code
        ? gateReason[reason.code] ?? reason.code
        : "Decision failed. No version was confirmed; refresh the request.";
    } finally {
      await refresh();
      if (decisionError) setError(decisionError);
      setBusy(null);
    }
  }

  return (
    <section className="operator-proposals" aria-label="Codex prepared version requests">
      <div className="section-header">
        <div><span className="eyebrow">HUMAN APPROVAL</span><h2>Prepared version requests</h2></div>
        <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void refresh()}>Refresh requests</button>
      </div>
      {error && <p className="operator-error" role="alert">{error}</p>}
      {requests.length === 0 && !error && <p>No prepared requests for this strategy.</p>}
      <div className="operator-proposal-list">
        {requests.map((request) => {
          const canApprove = request.status === "PROPOSED" && request.approval_gate.can_approve
            && request.expected_draft_revision === draftRevision && !isDirty && busy === null;
          const reason = isDirty ? "Save or discard unsaved Draft edits first."
            : request.expected_draft_revision !== draftRevision ? gateReason.DRAFT_STALE
              : gateReason[request.approval_gate.reason ?? ""];
          return (
            <article className="operator-proposal" key={request.request_id}>
              <div className="operator-proposal-heading"><div><strong>{request.strategy_name}</strong><small>{request.request_id}</small></div><span className="status-pill">{request.status}</span></div>
              <p className="operator-meta">Strategy {request.strategy_id} · Draft r{request.expected_draft_revision} · Validation PASS at preparation</p>
              {snapshotDetails(request.draft_snapshot)}
              <dl className="prepared-backtest-summary">
                <div><dt>Draft hash</dt><dd>{request.expected_draft_hash}</dd></div>
                <div><dt>Latest version at preparation</dt><dd>{request.base_latest_version_id ?? "No prior version"}</dd></div>
                <div><dt>Baseline version hash</dt><dd>{request.base_latest_version_hash ?? "N/A"}</dd></div>
                <div><dt>Created version ID</dt><dd>{request.created_version_id ?? "Not yet created"}</dd></div>
                <div><dt>Created version hash</dt><dd>{request.created_version_hash ?? "Not yet created"}</dd></div>
              </dl>
              {request.status === "PROPOSED" && <>
                <p className="prepared-approval-reason">Creating a version is irreversible. Review the saved Draft snapshot before approval; this does not run a backtest.</p>
                <div className="operator-actions">
                  <button className="button button-primary prepared-approve-button" type="button" disabled={!canApprove} onClick={() => void decide(request.request_id, true)}>Approve &amp; Create Version</button>
                  <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void decide(request.request_id, false)}>Reject</button>
                </div>
                {!canApprove && reason && <p role="status">{reason}</p>}
              </>}
              {request.error_code && <p role="status">{gateReason[request.error_code] ?? request.error_code}</p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}
