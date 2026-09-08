import { useEffect, useMemo, useState } from "react";

import { BacktestApiError, backtestApi } from "./api";
import type {
  ResearchProtocolCreateRequest,
  ResearchProtocolDetail,
  ResearchProtocolStatus,
} from "./types";

interface ResearchProtocolPanelProps {
  availableVersionIds: string[];
  availableRunIds: string[];
  preferredVersionId: string;
}

const EMPTY_PROTOCOL: ResearchProtocolCreateRequest = {
  is_start_date: "2020-01-01",
  is_end_date: "2023-12-29",
  oos_start_date: "2024-01-02",
  oos_end_date: "2025-12-31",
  gap_days: 0,
  embargo_days: 0,
  selection_rules: ["Human review of IS evidence only"],
  allowed_metrics: ["cagr", "max_drawdown"],
  evaluation_config: {
    price_field_used: "adjusted_close",
    initial_capital: 100000,
    commission: { rate: 0, per_order: 0 },
    slippage: 0,
    execution_rule: "next_trading_day_open",
    fractional_shares: false,
    rebalance_policy: { frequency: "daily", threshold: null },
    engine_version: "phase-3.0",
  },
};

function idList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function errorMessage(reason: unknown): string {
  return reason instanceof BacktestApiError
    ? reason.message
    : "Unable to update the research protocol.";
}

function statusLabel(status: ResearchProtocolStatus): string {
  return status.replaceAll("_", " ").toUpperCase();
}

export function ResearchProtocolPanel({
  availableVersionIds,
  availableRunIds,
  preferredVersionId,
}: ResearchProtocolPanelProps) {
  const [protocols, setProtocols] = useState<ResearchProtocolDetail["protocol"][]>([]);
  const [detail, setDetail] = useState<ResearchProtocolDetail | null>(null);
  const [createForm, setCreateForm] = useState(EMPTY_PROTOCOL);
  const [candidateInput, setCandidateInput] = useState(preferredVersionId);
  const [selectionVersionId, setSelectionVersionId] = useState("");
  const [isRunInput, setIsRunInput] = useState("");
  const [selectionRationale, setSelectionRationale] = useState("");
  const [freezeReason, setFreezeReason] = useState("Selected version is frozen before OOS observation.");
  const [oosRunId, setOosRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidateSet = detail?.candidate_sets[0];
  const selection = detail?.selections[0];
  const freeze = detail?.freezes[0];
  const observedOos = detail?.oos_evaluations[0];
  const candidateVersionIds = candidateSet?.strategy_version_ids ?? [];
  const protocol = detail?.protocol;

  const candidateHint = useMemo(
    () => availableVersionIds.join(", "),
    [availableVersionIds],
  );
  const runHint = useMemo(() => availableRunIds.join(", "), [availableRunIds]);

  async function refreshProtocols(selectedProtocolId?: string) {
    const items = await backtestApi.listResearchProtocols();
    setProtocols(items);
    const protocolId = selectedProtocolId ?? detail?.protocol.protocol_id;
    if (protocolId) setDetail(await backtestApi.getResearchProtocol(protocolId));
  }

  function accept(next: ResearchProtocolDetail) {
    setDetail(next);
    void refreshProtocols(next.protocol.protocol_id).catch(() => undefined);
  }

  useEffect(() => {
    void refreshProtocols().catch((reason) => setError(errorMessage(reason)));
  }, []);

  useEffect(() => {
    if (!candidateInput && preferredVersionId) setCandidateInput(preferredVersionId);
  }, [candidateInput, preferredVersionId]);

  async function perform(action: () => Promise<ResearchProtocolDetail>) {
    setBusy(true);
    setError(null);
    try {
      accept(await action());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function createProtocol(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = await backtestApi.createResearchProtocol(createForm);
      setDetail(next);
      await refreshProtocols(next.protocol.protocol_id);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="research-protocol panel">
      <div className="section-header compact">
        <div>
          <span className="eyebrow">OOS RESEARCH PROTOCOL</span>
          <h2>Holdout governance</h2>
        </div>
        {protocol && <span className="draft-label">{statusLabel(protocol.status)}</span>}
      </div>
      {error && <div className="inline-error backtest-error" role="alert">{error}</div>}

      <div className="protocol-picker">
        <label>
          Existing protocol
          <select
            aria-label="Existing research protocol"
            value={protocol?.protocol_id ?? ""}
            disabled={busy}
            onChange={(event) => {
              const protocolId = event.target.value;
              if (!protocolId) {
                setDetail(null);
                return;
              }
              setBusy(true);
              setError(null);
              void backtestApi.getResearchProtocol(protocolId).then(setDetail).catch((reason) => {
                setError(errorMessage(reason));
              }).finally(() => setBusy(false));
            }}
          >
            <option value="">Create a new protocol</option>
            {protocols.map((item) => <option key={item.protocol_id} value={item.protocol_id}>
              {item.protocol_id} · {statusLabel(item.status)}
            </option>)}
          </select>
        </label>
      </div>

      {!detail && (
        <form className="protocol-form" onSubmit={(event) => void createProtocol(event)}>
          <div className="basic-grid">
            <label>IS start<input aria-label="IS start date" type="date" value={createForm.is_start_date} onChange={(event) => setCreateForm((current) => ({ ...current, is_start_date: event.target.value }))} /></label>
            <label>IS end<input aria-label="IS end date" type="date" value={createForm.is_end_date} onChange={(event) => setCreateForm((current) => ({ ...current, is_end_date: event.target.value }))} /></label>
            <label>OOS start<input aria-label="OOS start date" type="date" value={createForm.oos_start_date} onChange={(event) => setCreateForm((current) => ({ ...current, oos_start_date: event.target.value }))} /></label>
            <label>OOS end<input aria-label="OOS end date" type="date" value={createForm.oos_end_date} onChange={(event) => setCreateForm((current) => ({ ...current, oos_end_date: event.target.value }))} /></label>
            <label>Gap days<input aria-label="Gap days" type="number" min="0" value={createForm.gap_days} onChange={(event) => setCreateForm((current) => ({ ...current, gap_days: Number(event.target.value) || 0 }))} /></label>
            <label>Embargo days<input aria-label="Embargo days" type="number" min="0" value={createForm.embargo_days} onChange={(event) => setCreateForm((current) => ({ ...current, embargo_days: Number(event.target.value) || 0 }))} /></label>
          </div>
          <button className="button button-primary" type="submit" disabled={busy}>Create protocol</button>
        </form>
      )}

      {detail && protocol && (
        <div className="protocol-flow">
          <dl className="protocol-summary">
            <dt>IS period</dt><dd>{protocol.is_start_date} to {protocol.is_end_date}</dd>
            <dt>OOS period</dt><dd>{protocol.oos_start_date} to {protocol.oos_end_date}</dd>
            <dt>Execution</dt><dd>Signal(T) to T+1 trading day open</dd>
            <dt>Data</dt><dd>Immutable run provenance only</dd>
            {protocol.evaluation_config && <>
              <dt>Evaluation contract</dt><dd>{protocol.evaluation_config.price_field_used} · ${protocol.evaluation_config.initial_capital.toLocaleString()} · {protocol.evaluation_config.execution_rule}</dd>
              <dt>Frozen costs</dt><dd>Commission {protocol.evaluation_config.commission.rate} / {protocol.evaluation_config.commission.per_order} · Slippage {protocol.evaluation_config.slippage}</dd>
            </>}
          </dl>

          {protocol.status === "draft" && !candidateSet && (
            <div className="protocol-action">
              <label>Candidate strategy version IDs<input aria-label="Candidate strategy version IDs" value={candidateInput} onChange={(event) => setCandidateInput(event.target.value)} placeholder="version-id-1, version-id-2" /></label>
              {candidateHint && <p className="muted">Available current-version IDs: {candidateHint}</p>}
              <button className="button button-secondary" type="button" disabled={busy || idList(candidateInput).length === 0} onClick={() => void perform(() => backtestApi.createCandidateSet(protocol.protocol_id, idList(candidateInput)))}>Record candidate set</button>
            </div>
          )}

          {protocol.status === "draft" && candidateSet?.status === "open" && (
            <div className="protocol-action">
              <p>Candidate set is recorded but still editable only by creating a new protocol. Lock it before IS evaluation.</p>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => void perform(() => backtestApi.lockCandidateSet(candidateSet.candidate_set_id))}>Lock candidate set</button>
            </div>
          )}

          {protocol.status === "draft" && candidateSet?.status === "locked" && (
            <div className="protocol-action">
              <p>Candidate set is immutable. Freeze the research protocol before reviewing IS evidence.</p>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => void perform(() => backtestApi.transitionResearchProtocol(protocol.protocol_id, "frozen"))}>Freeze research protocol</button>
            </div>
          )}

          {protocol.status === "frozen" && (
            <div className="protocol-action">
              <p>Confirm that IS evidence collection is complete before recording a human selection decision.</p>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => void perform(() => backtestApi.transitionResearchProtocol(protocol.protocol_id, "is_evaluated"))}>Mark IS evaluation complete</button>
            </div>
          )}

          {protocol.status === "is_evaluated" && !selection && candidateSet && (
            <div className="protocol-action protocol-selection">
              <label>Selected candidate<select aria-label="Selected candidate" value={selectionVersionId} onChange={(event) => setSelectionVersionId(event.target.value)}><option value="">Select frozen candidate</option>{candidateVersionIds.map((versionId) => <option key={versionId} value={versionId}>{versionId}</option>)}</select></label>
              <label>IS backtest run IDs<input aria-label="IS backtest run IDs" value={isRunInput} onChange={(event) => setIsRunInput(event.target.value)} placeholder="run-id-1, run-id-2" /></label>
              {runHint && <p className="muted">Saved run IDs: {runHint}</p>}
              <label>Human selection rationale<textarea aria-label="Human selection rationale" value={selectionRationale} onChange={(event) => setSelectionRationale(event.target.value)} /></label>
              <button className="button button-secondary" type="button" disabled={busy || !selectionVersionId || !selectionRationale.trim() || idList(isRunInput).length === 0} onClick={() => void perform(() => backtestApi.createSelectionDecision(protocol.protocol_id, { candidate_set_id: candidateSet.candidate_set_id, selected_strategy_version_id: selectionVersionId, is_backtest_run_ids: idList(isRunInput), rationale: selectionRationale.trim() }))}>Record IS selection</button>
            </div>
          )}

          {protocol.status === "is_evaluated" && selection && (
            <div className="protocol-action">
              <p>Selection is immutable and based on IS references only. Commit it before freezing the selected strategy version.</p>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => void perform(() => backtestApi.transitionResearchProtocol(protocol.protocol_id, "selection_recorded"))}>Commit selection decision</button>
            </div>
          )}

          {protocol.status === "selection_recorded" && !freeze && selection && (
            <div className="protocol-action">
              <label>Freeze reason<textarea aria-label="Freeze reason" value={freezeReason} onChange={(event) => setFreezeReason(event.target.value)} /></label>
              <button className="button button-secondary" type="button" disabled={busy || !freezeReason.trim()} onClick={() => void perform(() => backtestApi.freezeResearchStrategy(protocol.protocol_id, selection.decision_id, freezeReason.trim()))}>Freeze selected strategy version</button>
            </div>
          )}

          {protocol.status === "selection_recorded" && freeze && !observedOos && (
            <div className="protocol-action">
              <label>OOS backtest run ID<input aria-label="OOS backtest run ID" value={oosRunId} onChange={(event) => setOosRunId(event.target.value)} placeholder="OOS run with the exact protocol dates" /></label>
              <button className="button button-primary" type="button" disabled={busy || !oosRunId.trim()} onClick={() => void perform(() => backtestApi.observeOos(protocol.protocol_id, freeze.freeze_id, oosRunId.trim()))}>Record OOS observation</button>
            </div>
          )}

          {protocol.status === "selection_recorded" && observedOos && (
            <div className="protocol-action">
              <p>OOS has been observed and cannot return to untouched status.</p>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => void perform(() => backtestApi.transitionResearchProtocol(protocol.protocol_id, "oos_evaluated"))}>Finalize OOS evaluation</button>
            </div>
          )}

          <div className="protocol-records">
            <p><strong>Candidate set:</strong> {candidateSet ? `${candidateSet.strategy_version_ids.length} version(s) · ${candidateSet.status}` : "not recorded"}</p>
            <p><strong>Selection:</strong> {selection ? `${selection.selected_strategy_version_id} · IS only` : "not recorded"}</p>
            <p><strong>Strategy freeze:</strong> {freeze ? `${freeze.strategy_version_id} · ${freeze.strategy_version_content_hash}` : "not recorded"}</p>
            <p><strong>OOS:</strong> {observedOos ? `${observedOos.status} · untouched: ${String(observedOos.untouched_oos)}` : "not observed"}</p>
            <p className="muted">{detail.data_provenance_notice}</p>
          </div>
        </div>
      )}
    </section>
  );
}
