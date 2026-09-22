import { useMemo, useState } from "react";

import { researchApi, ResearchApiError } from "./api";
import { GridSearchPanel } from "./GridSearchPanel";
import { OptimizationResultsExplorer } from "./OptimizationResultsExplorer";
import type {
  CandidateResult,
  CompatibilityDiagnostic,
  ExperimentResults,
  HandoffResponse,
  ObjectiveEvaluationResponse,
  ObjectiveState,
  SelectionDecision,
  SelectionMethod,
  SelectionRequest,
} from "./types";

interface ExperimentResearchProps {
  onBack: () => void;
}

const selectionMethods: Array<{ value: SelectionMethod; label: string }> = [
  { value: "researcher_judgment", label: "Researcher judgment" },
  { value: "constraint_filtered_researcher_selection", label: "Constraint-filtered researcher selection" },
  { value: "manual_research_selection", label: "Manual research selection" },
];

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "Unavailable";
  }
}

function metricText(metric: { value: number | null; status: string; reason: string | null } | undefined): string {
  if (!metric || metric.status !== "available" || metric.value === null) return "NOT_EVALUABLE";
  return Number.isFinite(metric.value) ? metric.value.toFixed(4) : "NOT_EVALUABLE";
}

function statusLabel(status: string | null | undefined): string {
  return (status ?? "unknown").replaceAll("_", " ").toUpperCase();
}

function objectiveStateClass(state: ObjectiveState | undefined): string {
  return state === "pass" ? "is-valid" : state === "fail" ? "is-error" : "is-warning";
}

function candidateIsSelectable(candidate: CandidateResult, objective: ObjectiveState | undefined): boolean {
  return candidate.result_status === "completed" && candidate.execution_status === "completed" && objective === "pass";
}

function ApiErrorNotice({ error }: { error: ResearchApiError | string }) {
  const message = error instanceof ResearchApiError ? `${error.message} (${error.code})` : error;
  return <p className="research-error" role="alert">{message}</p>;
}

function CandidateTable({
  candidates,
  objective,
  selectedCandidateId,
  persistedSelection,
  compatibility,
  onSelect,
}: {
  candidates: CandidateResult[];
  objective: Map<string, ObjectiveState>;
  selectedCandidateId: string;
  persistedSelection: SelectionDecision | null;
  compatibility: CompatibilityDiagnostic | null;
  onSelect: (candidate: CandidateResult) => void;
}) {
  return (
    <div className="table-scroll">
      <table className="experiment-candidate-table">
        <thead>
          <tr>
            <th>Index</th><th>Candidate ID</th><th>Parameter set</th><th>Result</th><th>Constraints</th><th>Performance</th><th>Action</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => {
            const state = objective.get(candidate.candidate_id);
            const selectable = candidateIsSelectable(candidate, state) && compatibility?.status === "compatible";
            const isPersisted = persistedSelection?.selected_candidate_id === candidate.candidate_id;
            return (
              <tr key={candidate.candidate_id} className={selectedCandidateId === candidate.candidate_id ? "is-selected" : undefined}>
                <td>{candidate.candidate_index}</td>
                <td className="research-hash" title={candidate.candidate_id}>{candidate.candidate_id}</td>
                <td>
                  <details>
                    <summary>View parameters</summary>
                    <pre className="parameter-json">{safeJson(candidate.parameter_set)}</pre>
                  </details>
                  <small className="research-hash">{candidate.parameter_set_hash}</small>
                </td>
                <td>
                  <span className={`status-pill ${candidate.result_status === "completed" ? "is-valid" : "is-warning"}`}>
                    {candidate.result_status === "not_evaluable" ? "NOT_EVALUABLE" : statusLabel(candidate.result_status)}
                  </span>
                  {candidate.failure_summary && <small className="cell-note">{candidate.failure_summary}</small>}
                </td>
                <td>
                  <span className={`status-pill ${objectiveStateClass(state)}`}>{statusLabel(state)}</span>
                </td>
                <td>
                  {candidate.performance_summary && Object.entries(candidate.performance_summary).slice(0, 4).map(([key, metric]) => (
                    <div className="metric-line" key={key}><span>{key.replaceAll("_", " ")}</span><strong>{metricText(metric)}</strong></div>
                  ))}
                  {!candidate.performance_summary && <span className="muted">NOT_EVALUABLE</span>}
                </td>
                <td>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!selectable || persistedSelection !== null}
                    onClick={() => onSelect(candidate)}
                  >
                    {isPersisted ? "Selected" : "Select candidate"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CompatibilityPanel({ diagnostic }: { diagnostic: CompatibilityDiagnostic | null }) {
  if (!diagnostic) return <section className="panel"><span className="eyebrow">COMPATIBILITY DIAGNOSTICS</span><h2>Unavailable</h2><p className="muted">Compatibility evidence is not available yet. Candidate selection remains disabled.</p></section>;
  return (
    <section className={`panel compatibility ${diagnostic.status === "incompatible" ? "incompatible" : ""}`}>
      <span className="eyebrow">COMPATIBILITY DIAGNOSTICS</span>
      <h2>{statusLabel(diagnostic.status)}</h2>
      <p>{diagnostic.status === "compatible" ? "Persisted candidate results share the frozen research contract." : "Review the backend diagnostic details before using the evidence."}</p>
      {diagnostic.checked_dimensions.length > 0 && <ul className="compatibility-checks">{diagnostic.checked_dimensions.map((dimension) => <li key={dimension}><strong>{dimension}</strong><span>PASS</span></li>)}</ul>}
      {diagnostic.mismatches.length > 0 && (
        <ul>
          {diagnostic.mismatches.map((item) => <li key={`${item.candidate_id}-${item.dimension}-${item.reason_code}`}><strong>{item.reason_code}</strong><span>{item.message}</span></li>)}
        </ul>
      )}
    </section>
  );
}

function ObjectivePanel({ response }: { response: ObjectiveEvaluationResponse | null }) {
  if (!response) return <section className="panel"><span className="eyebrow">FROZEN OBJECTIVE</span><h2>Unavailable</h2><p className="muted">Frozen objective and constraint evidence is not available yet. Candidate selection remains disabled.</p></section>;
  return (
    <section className="panel">
      <div className="section-header compact"><div><span className="eyebrow">FROZEN OBJECTIVE</span><h2>Hard constraints</h2></div><span className="research-hash">{response.objective_spec_hash}</span></div>
      <details>
        <summary>View frozen objective specification</summary>
        <pre className="parameter-json">{safeJson(response.objective_specification)}</pre>
      </details>
      <div className="objective-grid">
        {response.evaluations.map((item) => (
          <div className="objective-card" key={item.candidate_id}>
            <div className="subsection-heading"><strong>Candidate {item.candidate_index}</strong><span className={`status-pill ${objectiveStateClass(item.evaluation.overall_constraint_state)}`}>{statusLabel(item.evaluation.overall_constraint_state)}</span></div>
            {item.evaluation.constraint_results.map((constraint) => <div className="constraint-line" key={`${item.candidate_id}-${constraint.metric}`}><span>{constraint.metric} {constraint.operator} {constraint.threshold} · observed {constraint.metric_value ?? "NOT_EVALUABLE"}<small>{constraint.reason}</small></span><strong>{statusLabel(constraint.state)}</strong></div>)}
          </div>
        ))}
      </div>
    </section>
  );
}

function SelectionPanel({
  candidate,
  objectiveState,
  onCancel,
  onSubmit,
  busy,
}: {
  candidate: CandidateResult;
  objectiveState: ObjectiveState | undefined;
  onCancel: () => void;
  onSubmit: (request: SelectionRequest) => void;
  busy: boolean;
}) {
  const [method, setMethod] = useState<SelectionMethod>("researcher_judgment");
  const [rationale, setRationale] = useState("");
  return (
    <section className="panel selection-panel">
      <span className="eyebrow">CONFIRM RESEARCH SELECTION</span>
      <h2>Review before persisting</h2>
      <dl className="research-detail-list">
        <dt>Candidate</dt><dd>{candidate.candidate_id} · index {candidate.candidate_index}</dd>
        <dt>Parameter set</dt><dd><code>{safeJson(candidate.parameter_set)}</code></dd>
        <dt>Derived strategy</dt><dd>{candidate.derived_strategy_version_id ?? "Unavailable"}</dd>
        <dt>Constraint state</dt><dd>{statusLabel(objectiveState)}</dd>
        <dt>Result state</dt><dd>{statusLabel(candidate.result_status)}</dd>
      </dl>
      <div className="selection-form">
        <label>Selection method<select value={method} onChange={(event) => setMethod(event.target.value as SelectionMethod)}>{selectionMethods.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label>Researcher rationale<textarea aria-label="Researcher rationale" value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="Explain the evidence used for this selection." /></label>
      </div>
      <p className="muted">Only the candidate choice, method and rationale are sent. Frozen identity and evidence remain server-controlled.</p>
      <div className="action-buttons"><button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button><button className="button button-primary" type="button" disabled={busy || !rationale.trim()} onClick={() => onSubmit({ selected_candidate_id: candidate.candidate_id, selection_method: method, researcher_rationale: rationale.trim() })}>{busy ? "Saving..." : "Confirm selection"}</button></div>
    </section>
  );
}

function PersistedSelection({ selection }: { selection: SelectionDecision }) {
  return (
    <section className="panel persisted-panel">
      <div className="section-header compact"><div><span className="eyebrow">PERSISTED SELECTION</span><h2>Read-only decision</h2></div><span className="status-pill is-valid">IMMUTABLE</span></div>
      <dl className="research-detail-list">
        <dt>Selection ID</dt><dd>{selection.selection_id}</dd>
        <dt>Candidate</dt><dd>{selection.selected_candidate_id} · index {selection.selected_candidate_index}</dd>
        <dt>Method</dt><dd>{selection.selection_method}</dd>
        <dt>Rationale</dt><dd>{selection.researcher_rationale}</dd>
        <dt>Result hash</dt><dd className="research-hash">{selection.selected_result_hash ?? "Unavailable"}</dd>
        <dt>Derived strategy</dt><dd>{selection.selected_derived_strategy_version_id}</dd>
      </dl>
    </section>
  );
}

function handoffField(value: Record<string, unknown>, key: string): string {
  const field = value[key];
  return typeof field === "string" || typeof field === "number" ? String(field) : "Unavailable";
}

function HandoffPanel({ handoff, onHandoff, busy }: { handoff: HandoffResponse | null; onHandoff: () => void; busy: boolean }) {
  const [confirming, setConfirming] = useState(false);
  if (handoff) {
    return <section className="panel handoff-panel"><div className="section-header compact"><div><span className="eyebrow">PHASE 7 HANDOFF</span><h2>Freeze recorded</h2></div><span className="status-pill is-valid">CONFIRMED</span></div><dl className="research-detail-list"><dt>Protocol</dt><dd>{handoff.protocol_id}</dd><dt>Selection decision</dt><dd>{handoffField(handoff.selection_decision, "decision_id")}</dd><dt>Strategy freeze</dt><dd>{handoffField(handoff.strategy_freeze, "freeze_id")}</dd><dt>Strategy version</dt><dd>{handoff.identity.strategy_version_id}</dd><dt>Content hash</dt><dd className="research-hash">{handoff.identity.strategy_version_content_hash}</dd><dt>Identity</dt><dd>Backend confirmed the returned strategy identity.</dd></dl><p className="muted">This handoff reuses the persisted selection and does not execute another research run.</p></section>;
  }
  return <section className="panel handoff-panel"><span className="eyebrow">PHASE 7 HANDOFF</span><h2>Submit frozen selection</h2><p className="muted">This action only hands the persisted selection to the research protocol. It does not run another backtest or change the selected candidate.</p>{!confirming ? <button className="button button-primary" type="button" disabled={busy} onClick={() => setConfirming(true)}>Proceed to PHASE 7 Handoff</button> : <div className="handoff-confirmation" role="dialog" aria-label="Confirm PHASE 7 handoff"><p>This does not execute OOS, run a backtest, reselect a candidate, or regenerate a strategy version. It only hands the persisted selection to PHASE 7.</p><div className="action-buttons"><button className="button button-quiet" type="button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button><button className="button button-primary" type="button" disabled={busy} onClick={onHandoff}>{busy ? "Submitting..." : "Confirm handoff"}</button></div></div>}</section>;
}

export function ExperimentResearch({ onBack }: ExperimentResearchProps) {
  const [protocolId, setProtocolId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [results, setResults] = useState<ExperimentResults | null>(null);
  const [compatibility, setCompatibility] = useState<CompatibilityDiagnostic | null>(null);
  const [objective, setObjective] = useState<ObjectiveEvaluationResponse | null>(null);
  const [selection, setSelection] = useState<SelectionDecision | null>(null);
  const [handoff, setHandoff] = useState<HandoffResponse | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateResult | null>(null);
  const [busy, setBusy] = useState<"load" | "selection" | "handoff" | null>(null);
  const [error, setError] = useState<ResearchApiError | string | null>(null);

  const objectiveByCandidate = useMemo(() => new Map((objective?.evaluations ?? []).map((item) => [item.candidate_id, item.evaluation.overall_constraint_state])), [objective]);

  async function loadResearch() {
    const protocol = protocolId.trim();
    const experiment = experimentId.trim();
    if (!protocol || !experiment) {
      setError("Enter both a protocol ID and an experiment ID.");
      return;
    }
    setBusy("load");
    setError(null);
    setSelection(null);
    setHandoff(null);
    setSelectedCandidate(null);
    setCompatibility(null);
    setObjective(null);
    const [resultsResponse, compatibilityResponse, objectiveResponse, selectionResponse, handoffResponse] = await Promise.allSettled([
      researchApi.getExperimentResults(protocol, experiment),
      researchApi.getExperimentCompatibility(protocol, experiment),
      researchApi.getExperimentObjectiveEvaluation(protocol, experiment),
      researchApi.getExperimentSelection(protocol, experiment),
      researchApi.getExperimentHandoff(protocol, experiment),
    ]);
    if (resultsResponse.status === "rejected") {
      setError(resultsResponse.reason instanceof ResearchApiError ? resultsResponse.reason : "Unable to load experiment results.");
      setResults(null);
    } else {
      setResults(resultsResponse.value);
    }
    if (compatibilityResponse.status === "fulfilled") setCompatibility(compatibilityResponse.value);
    if (objectiveResponse.status === "fulfilled") setObjective(objectiveResponse.value);
    if (selectionResponse.status === "fulfilled") setSelection(selectionResponse.value);
    if (handoffResponse.status === "fulfilled") setHandoff(handoffResponse.value);
    setBusy(null);
  }

  async function submitSelection(request: SelectionRequest) {
    setBusy("selection");
    setError(null);
    try {
      const saved = await researchApi.createExperimentSelection(protocolId.trim(), experimentId.trim(), request);
      setSelection(saved);
      setSelectedCandidate(null);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? reason : "Selection could not be persisted.");
    } finally {
      setBusy(null);
    }
  }

  async function submitHandoff() {
    setBusy("handoff");
    setError(null);
    try {
      const saved = await researchApi.handoffExperimentSelection(protocolId.trim(), experimentId.trim());
      setHandoff(saved);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? reason : "Selection handoff failed.");
    } finally {
      setBusy(null);
    }
  }

  const candidates = results ? [...results.candidates].sort((a, b) => a.candidate_index - b.candidate_index) : [];

  return (
    <main className="research-shell">
      <header className="backtest-topbar">
        <div><span className="eyebrow">ETF QUANT RESEARCH SYSTEM · EXPERIMENT RESEARCH</span><h1>Experiment Research</h1><p>Inspect persisted experiment evidence and record a researcher decision.</p></div>
        <div className="topbar-status"><button className="button button-secondary" type="button" onClick={onBack}>Back to Strategy Lab</button></div>
      </header>
      <section className="panel research-loader">
        <div className="research-controls"><label>Protocol ID<input aria-label="Protocol ID" value={protocolId} onChange={(event) => setProtocolId(event.target.value)} placeholder="protocol-id" /></label><label>Experiment ID<input aria-label="Experiment ID" value={experimentId} onChange={(event) => setExperimentId(event.target.value)} placeholder="experiment-id" /></label><button className="button button-primary" type="button" disabled={busy === "load"} onClick={() => void loadResearch()}>{busy === "load" ? "Loading..." : "Load experiment"}</button></div>
        {error && <ApiErrorNotice error={error} />}
      </section>
      <GridSearchPanel experimentId={experimentId} />
      {results && <>
        <section className="panel"><div className="section-header compact"><div><span className="eyebrow">EXPERIMENT SUMMARY</span><h2>{results.experiment_id}</h2></div><span className="status-pill">{statusLabel(results.experiment_status)}</span></div><dl className="research-detail-list summary-list"><dt>Protocol</dt><dd>{results.protocol_id}</dd><dt>IS range</dt><dd>{results.is_start} to {results.is_end}</dd><dt>Candidates</dt><dd>{results.summary.candidate_count}</dd><dt>Completed</dt><dd>{results.summary.completed_count}</dd><dt>Failed</dt><dd>{results.summary.failed_count}</dd><dt>Not evaluable</dt><dd>{results.summary.not_evaluable_count}</dd><dt>Pending / running</dt><dd>{results.summary.pending_count} / {results.summary.running_count}</dd><dt>Completeness</dt><dd>{results.summary.completeness_status}</dd><dt>Candidate set hash</dt><dd className="research-hash">{results.candidate_set?.candidate_set_hash ?? "Unavailable"}</dd><dt>Parameter space hash</dt><dd className="research-hash">{results.parameter_space_hash}</dd><dt>Objective hash</dt><dd className="research-hash">{results.objective_spec_hash}</dd><dt>Engine / analytics</dt><dd>{results.engine_version} / {results.analysis_version}</dd></dl></section>
        <CompatibilityPanel diagnostic={compatibility} />
        <ObjectivePanel response={objective} />
        <OptimizationResultsExplorer protocolId={results.protocol_id} experimentId={results.experiment_id} />
        <section className="panel"><div className="section-header compact"><div><span className="eyebrow">CANDIDATE RESULTS</span><h2>Persisted IS evidence</h2></div><span className="muted">Candidate index order</span></div><CandidateTable candidates={candidates} objective={objectiveByCandidate} selectedCandidateId={selectedCandidate?.candidate_id ?? ""} persistedSelection={selection} compatibility={compatibility} onSelect={setSelectedCandidate} /></section>
        {selectedCandidate && !selection && <SelectionPanel candidate={selectedCandidate} objectiveState={objectiveByCandidate.get(selectedCandidate.candidate_id)} onCancel={() => setSelectedCandidate(null)} onSubmit={(request) => void submitSelection(request)} busy={busy === "selection"} />}
        {selection && <><PersistedSelection selection={selection} /><HandoffPanel handoff={handoff} onHandoff={() => void submitHandoff()} busy={busy === "handoff"} /></>}
      </>}
    </main>
  );
}
