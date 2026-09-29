import { useState } from "react";

import type { CandidateResult, ExperimentResults, HandoffResponse, ResearchProtocolDetail, SelectionDecision } from "./types";
import { parameterValueDisplay, type ParameterLabels } from "./semanticTargetDisplay";

function validDate(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

export function freezeGateReason(
  results: ExperimentResults,
  selection: SelectionDecision | null,
  protocol: ResearchProtocolDetail | null,
  readError: string | null,
): string | null {
  if (readError) return readError;
  if (!selection) return "SELECTION_NOT_FOUND: A persisted selection is required.";
  if (selection.experiment_id !== results.experiment_id || selection.protocol_id !== results.protocol_id) return "SELECTION_MISMATCH: Reload the persisted selection.";
  const candidate = results.candidates.find((item) => item.candidate_id === selection.selected_candidate_id);
  if (!candidate) return "CANDIDATE_NOT_FOUND: The selected candidate is missing.";
  if (!candidate.selection_eligibility?.eligible) return `${candidate.selection_eligibility?.failure_code ?? "ELIGIBILITY_UNAVAILABLE"}: ${candidate.selection_eligibility?.eligibility_reason ?? "Selection eligibility could not be verified."}`;
  // Canonical eligibility includes the persisted derived-version existence/hash check.
  if (!candidate.derived_strategy_version_id || !/^[0-9a-f]{64}$/.test(candidate.derived_strategy_version_hash ?? "")
    || candidate.derived_strategy_version_id !== selection.selected_derived_strategy_version_id
    || candidate.derived_strategy_version_hash !== selection.selected_derived_strategy_content_hash) return "DERIVED_STRATEGY_INTEGRITY_ERROR: Selected StrategyVersion identity/hash does not match persisted evidence.";
  if (candidate.candidate_index !== selection.selected_candidate_index || candidate.parameter_set_hash !== selection.selected_parameter_set_hash
    || candidate.experiment_result_id !== selection.selected_experiment_result_id || candidate.result_hash !== selection.selected_result_hash) return "SELECTION_INTEGRITY_ERROR: Selected candidate evidence does not match.";
  if (!results.summary.complete || results.summary.pending_count !== 0 || results.summary.running_count !== 0) return "EXPERIMENT_NOT_COMPLETED: IS execution is incomplete.";
  const record = protocol?.protocol;
  if (!record || record.protocol_id !== results.protocol_id) return "PROTOCOL_UNAVAILABLE: Frozen protocol could not be verified.";
  if (record.status !== "is_evaluated" && record.status !== "selection_recorded") return "PROTOCOL_INVALID_STATE: Protocol is not ready to freeze.";
  if (protocol.freezes.length > 0) return "STRATEGY_FREEZE_CONFLICT: A protocol freeze already exists; reload the frozen selection.";
  if (record.is_start_date !== results.is_start || record.is_end_date !== results.is_end) return "IS_BOUNDARY_ERROR: Experiment and protocol IS ranges differ.";
  if (!validDate(record.oos_start_date) || !validDate(record.oos_end_date)
    || record.oos_start_date <= results.is_end || record.oos_end_date < record.oos_start_date) return "OOS_BOUNDARY_ERROR: Frozen protocol OOS range is invalid.";
  return null;
}

export function FreezeSelectionPanel({ selection, candidate, results, protocol, labels, frozen, blockReason, busy, onFreeze }: {
  selection: SelectionDecision;
  candidate: CandidateResult | undefined;
  results: ExperimentResults;
  protocol: ResearchProtocolDetail | null;
  labels: ParameterLabels;
  frozen: HandoffResponse | null;
  blockReason: string | null;
  busy: boolean;
  onFreeze: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const nested = candidate?.parameter_set.values;
  const parameters = typeof nested === "object" && nested !== null && !Array.isArray(nested) ? nested : candidate?.parameter_set;
  const versionId = frozen?.identity.strategy_version_id ?? selection.selected_derived_strategy_version_id;
  const versionHash = frozen?.identity.strategy_version_content_hash ?? selection.selected_derived_strategy_content_hash;
  const record = protocol?.protocol;
  const oosStart = record?.oos_start_date;
  const oosEnd = record?.oos_end_date;
  return <section className="panel handoff-panel" aria-label="Freeze selection for Official OOS">
    <div className="section-header compact"><div><span className="eyebrow">OFFICIAL OOS FREEZE</span><h2>{frozen ? "FROZEN FOR OFFICIAL OOS" : "Freeze selected candidate"}</h2></div></div>
    <dl className="research-detail-list">
      <dt>Selected Candidate</dt><dd>#{selection.selected_candidate_index}{frozen ? " · Selected / Frozen" : ""}</dd>
      <dt>Parameters</dt><dd>{parameters ? Object.entries(parameters).map(([name, value]) => <div key={name}>{labels[name]?.label ?? name}：{parameterValueDisplay(value, labels[name]?.unit)}</div>) : "Unavailable"}</dd>
      <dt>Selection ID</dt><dd className="research-hash">{selection.selection_id}</dd>
      <dt>Exact Derived StrategyVersion ID</dt><dd className="research-hash">{versionId}</dd>
      <dt>Derived StrategyVersion content hash</dt><dd className="research-hash">{versionHash}</dd>
      <dt>IS Range</dt><dd>{results.is_start} → {results.is_end}</dd>
      <dt>OOS Range</dt><dd>{validDate(oosStart) && validDate(oosEnd) ? `${oosStart} → ${oosEnd}` : "Unavailable"}</dd>
      {frozen && <><dt>Freeze ID</dt><dd className="research-hash">{String(frozen.strategy_freeze.freeze_id ?? "Unavailable")}</dd></>}
    </dl>
    {!frozen && <>
      <div role="note" aria-label="Freeze consequences"><p>冻结后：</p><ul>
        <li>Selected Candidate 不可再修改。</li><li>Parameter Space 不可修改。</li><li>Derived StrategyVersion 不可替换。</li>
        <li>Official OOS 将使用上述 exact immutable StrategyVersion。</li><li>如需重新选择参数，应创建新的 Experiment。</li>
      </ul></div>
      {blockReason && <p className="research-error" role="alert">{blockReason}</p>}
      {!confirming ? <button className="button button-primary" type="button" disabled={busy || blockReason !== null} onClick={() => setConfirming(true)}>Freeze for Official OOS</button>
        : <div role="group" aria-label="Confirm Official OOS freeze"><p>确认冻结 Candidate #{selection.selected_candidate_index} 及上述 StrategyVersion ID/content hash？此操作不会执行 OOS 回测。</p><div className="action-buttons">
          <button className="button button-quiet" type="button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
          <button className="button button-primary" type="button" disabled={busy || blockReason !== null} onClick={onFreeze}>{busy ? "Freezing..." : "Confirm freeze"}</button>
        </div></div>}
    </>}
    {frozen && !protocol && <p className="research-error" role="alert">PROTOCOL_UNAVAILABLE: Reload to verify the persisted OOS range.</p>}
  </section>;
}
