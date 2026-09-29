import { useEffect, useMemo, useState } from "react";

import { strategyApi } from "../strategy/api";
import { researchApi, ResearchApiError } from "./api";
import { parameterValueDisplay, semanticTargetDisplay, type ParameterLabels } from "./semanticTargetDisplay";
import { GridSearchPanel } from "./GridSearchPanel";
import { FreezeSelectionPanel, freezeGateReason } from "./FreezeSelectionPanel";
import { OptimizationResultsExplorer } from "./OptimizationResultsExplorer";
import type {
  CandidateResult,
  CompatibilityDiagnostic,
  ExperimentResults,
  HandoffResponse,
  ObjectiveEvaluationResponse,
  OfficialOosExecutionResponse,
  ResearchProtocolDetail,
  ObjectiveState,
  SelectionDecision,
  SelectionMethod,
  SelectionRequest,
} from "./types";

const selectionMethods: Array<{ value: SelectionMethod; label: string }> = [
  { value: "researcher_judgment", label: "Researcher judgment" },
  { value: "constraint_filtered_researcher_selection", label: "Constraint-filtered researcher selection" },
  { value: "manual_research_selection", label: "Manual research selection" },
];

const resultMetrics = [
  ["cagr", "CAGR"], ["total_return", "Total Return"], ["max_drawdown", "Max Drawdown"],
  ["sharpe_ratio", "Sharpe Ratio"], ["calmar_ratio", "Calmar Ratio"],
] as const;

function ParameterDisplay({ candidate, labels }: { candidate: CandidateResult; labels: ParameterLabels }) {
  const nested = candidate.parameter_set.values;
  const values = typeof nested === "object" && nested !== null && !Array.isArray(nested) ? nested : candidate.parameter_set;
  return <>
    {Object.entries(values).map(([name, value]) => {
      const display = labels[name];
      const text = parameterValueDisplay(value, display?.unit);
      return <div key={name}>{display?.label ?? name}：{text}</div>;
    })}
    <details><summary>Canonical parameters</summary><pre className="parameter-json">{safeJson(candidate.parameter_set)}</pre></details>
  </>;
}

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

function ApiErrorNotice({ error }: { error: ResearchApiError | string }) {
  const message = error instanceof ResearchApiError ? `${error.message} (${error.code})` : error;
  return <p className="research-error" role="alert">{message}</p>;
}

function CandidateTable({
  candidates,
  objective,
  selectedCandidateId,
  persistedSelection,
  onSelect,
  parameterLabels,
  allowChange,
  frozen,
}: {
  candidates: CandidateResult[];
  objective: Map<string, ObjectiveState>;
  selectedCandidateId: string;
  persistedSelection: SelectionDecision | null;
  onSelect: (candidate: CandidateResult) => void;
  parameterLabels: ParameterLabels;
  allowChange: boolean;
  frozen: boolean;
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
            const selectable = candidate.selection_eligibility?.eligible === true;
            const isPersisted = persistedSelection?.selected_candidate_id === candidate.candidate_id;
            return (
              <tr key={candidate.candidate_id} className={selectedCandidateId === candidate.candidate_id ? "is-selected" : undefined}>
                <td>{candidate.candidate_index}</td>
                <td className="research-hash" title={candidate.candidate_id}>{candidate.candidate_id}</td>
                <td>
                  <ParameterDisplay candidate={candidate} labels={parameterLabels} />
                  <small className="research-hash">{candidate.parameter_set_hash}</small>
                </td>
                <td>
                  <span className={`status-pill ${candidate.result_status === "completed" ? "is-valid" : "is-warning"}`}>
                    {candidate.result_status === "not_evaluable" ? "NOT_EVALUABLE" : statusLabel(candidate.result_status)}
                  </span>
                  {candidate.failure_code && <small className="cell-note">{candidate.failure_code}</small>}
                  {candidate.failure_summary && <small className="cell-note">{candidate.failure_summary}</small>}
                  {candidate.failure_stage && <small className="cell-note">Stage: {candidate.failure_stage}</small>}
                  {candidate.failure_exception_type && <small className="cell-note">Type: {candidate.failure_exception_type}</small>}
                </td>
                <td>
                  <span className={`status-pill ${objectiveStateClass(state)}`}>{statusLabel(state)}</span>
                </td>
                <td>
                  {resultMetrics.map(([key, label]) => (
                    <div className="metric-line" key={key}><span>{label}</span><strong>{metricText(candidate.performance_summary?.[key])}</strong></div>
                  ))}
                  {!candidate.performance_summary && <span className="muted">NOT_EVALUABLE</span>}
                </td>
                <td>
                  <button
                    className="button button-secondary"
                    type="button"
                    disabled={!selectable || !allowChange || isPersisted}
                    onClick={() => onSelect(candidate)}
                  >
                    {isPersisted ? frozen ? "Selected / Frozen" : "Selected" : persistedSelection ? "Change selection" : "Select candidate"}
                  </button>
                  {!selectable && candidate.selection_eligibility?.eligibility_reason && <small className="cell-note">{candidate.selection_eligibility.eligibility_reason}</small>}
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
      <div className="action-buttons"><button className="button button-quiet" type="button" onClick={onCancel}>Cancel</button><button className="button button-primary" type="button" disabled={busy} onClick={() => onSubmit({ selected_candidate_id: candidate.candidate_id, selection_method: method, researcher_rationale: rationale.trim() })}>{busy ? "Saving..." : "Confirm selection"}</button></div>
    </section>
  );
}

function PersistedSelection({ selection, frozen }: { selection: SelectionDecision; frozen: boolean }) {
  return (
    <section className="panel persisted-panel">
      <div className="section-header compact"><div><span className="eyebrow">PERSISTED SELECTION</span><h2>{frozen ? "Frozen decision" : "Active decision"}</h2></div><span className="status-pill is-valid">{frozen ? "FROZEN" : "CHANGEABLE BEFORE FREEZE"}</span></div>
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

function OfficialOosPanel({
  handoff,
  execution,
  busy,
  onPrepare,
  onExecute,
  onOpen,
}: {
  handoff: HandoffResponse;
  execution: OfficialOosExecutionResponse["execution"] | null;
  busy: boolean;
  onPrepare: () => void;
  onExecute: () => void;
  onOpen: () => void;
}) {
  const status = execution?.status ?? "not_prepared";
  return <section className="panel handoff-panel">
    <div className="section-header compact"><div><span className="eyebrow">OFFICIAL OOS</span><h2>Frozen strategy validation</h2></div><span className={`status-pill ${status === "completed" ? "is-valid" : "is-warning"}`}>{statusLabel(status)}</span></div>
    <p className="muted">One official OOS execution is allowed for this exact frozen StrategyVersion. Parameters and dates are server-controlled.</p>
    <dl className="research-detail-list"><dt>Frozen StrategyVersion</dt><dd>{handoff.identity.strategy_version_id}</dd><dt>Content hash</dt><dd className="research-hash">{handoff.identity.strategy_version_content_hash}</dd><dt>Execution ID</dt><dd>{execution?.execution_id ?? "Created on prepare"}</dd></dl>
    <div className="action-buttons">
      {!execution && <button className="button button-secondary" type="button" disabled={busy} onClick={onPrepare}>{busy ? "Preparing..." : "Prepare Official OOS"}</button>}
      {execution && status !== "completed" && <button className="button button-primary" type="button" disabled={busy || status === "running"} onClick={onExecute}>{busy ? "Executing..." : status === "failed" ? "Retry Official OOS" : "Run Official OOS"}</button>}
      {status === "completed" && <button className="button button-primary" type="button" onClick={onOpen}>Open Official OOS Research View</button>}
    </div>
  </section>;
}

export interface ExperimentResearchProps {
  onBack: () => void;
  initialProtocolId?: string;
  initialExperimentId?: string;
  onOpenOos?: (protocolId: string) => void;
}

export function ExperimentResearch({ onBack, initialProtocolId = "", initialExperimentId = "", onOpenOos }: ExperimentResearchProps) {
  const [protocolId, setProtocolId] = useState(initialProtocolId);
  const [experimentId, setExperimentId] = useState(initialExperimentId);
  const [results, setResults] = useState<ExperimentResults | null>(null);
  const [parameterLabels, setParameterLabels] = useState<ParameterLabels>({});
  const [compatibility, setCompatibility] = useState<CompatibilityDiagnostic | null>(null);
  const [objective, setObjective] = useState<ObjectiveEvaluationResponse | null>(null);
  const [selection, setSelection] = useState<SelectionDecision | null>(null);
  const [handoff, setHandoff] = useState<HandoffResponse | null>(null);
  const [protocolDetail, setProtocolDetail] = useState<ResearchProtocolDetail | null>(null);
  const [freezeReadError, setFreezeReadError] = useState<string | null>("FREEZE_STATE_UNAVAILABLE: Load persisted freeze state.");
  const [oosExecution, setOosExecution] = useState<OfficialOosExecutionResponse["execution"] | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateResult | null>(null);
  const [busy, setBusy] = useState<"load" | "selection" | "handoff" | null>(null);
  const [error, setError] = useState<ResearchApiError | string | null>(null);

  const objectiveByCandidate = useMemo(() => new Map((objective?.evaluations ?? []).map((item) => [item.candidate_id, item.evaluation.overall_constraint_state])), [objective]);

  useEffect(() => {
    let active = true;
    setParameterLabels({});
    if (!results) return;
    void (async () => {
      try {
        const experiment = await researchApi.getExperiment(results.experiment_id);
        if (experiment.base_strategy_version_id !== results.base_strategy_version_id || experiment.base_strategy_version_hash !== results.base_strategy_version_hash) return;
        const [version, catalog] = await Promise.all([
          strategyApi.getVersion(experiment.strategy_definition_id, results.base_strategy_version_id),
          strategyApi.getSemanticTargets(experiment.strategy_definition_id, results.base_strategy_version_id),
        ]);
        if (!active || version.content_hash !== results.base_strategy_version_hash || catalog.strategy_version_hash !== results.base_strategy_version_hash) return;
        setParameterLabels(Object.fromEntries(catalog.targets.filter((target) => target.supported).map((target) => [target.parameter_name, semanticTargetDisplay(target, version.configuration)])));
      } catch {
        // Canonical parameters remain readable when display metadata is unavailable.
      }
    })();
    return () => { active = false; };
  }, [results]);

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
    setProtocolDetail(null);
    setFreezeReadError("FREEZE_STATE_UNAVAILABLE: Verifying persisted freeze state.");
    setOosExecution(null);
    setSelectedCandidate(null);
    setCompatibility(null);
    setObjective(null);
    const [resultsResponse, compatibilityResponse, objectiveResponse, selectionResponse, handoffResponse, oosResponse, protocolResponse] = await Promise.allSettled([
      researchApi.getExperimentResults(protocol, experiment),
      researchApi.getExperimentCompatibility(protocol, experiment),
      researchApi.getExperimentObjectiveEvaluation(protocol, experiment),
      researchApi.getExperimentSelection(protocol, experiment),
      researchApi.getExperimentHandoff(protocol, experiment),
      researchApi.getOfficialOosExecution(protocol),
      researchApi.getProtocol(protocol),
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
    if (handoffResponse.status === "fulfilled") {
      setHandoff(handoffResponse.value);
      setFreezeReadError(null);
    } else if (handoffResponse.reason instanceof ResearchApiError && ["HANDOFF_NOT_FOUND", "SELECTION_NOT_FOUND"].includes(handoffResponse.reason.code)) {
      setFreezeReadError(null);
    } else {
      setFreezeReadError("FREEZE_STATE_UNAVAILABLE: Persisted freeze state could not be verified; reload research.");
    }
    if (protocolResponse.status === "fulfilled") setProtocolDetail(protocolResponse.value);
    if (oosResponse.status === "fulfilled") setOosExecution(oosResponse.value.execution);
    setBusy(null);
  }

  async function prepareOos() {
    setBusy("handoff");
    setError(null);
    try {
      const prepared = await researchApi.prepareOfficialOos(protocolId.trim());
      setOosExecution(prepared.execution);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? reason : "Official OOS could not be prepared.");
    } finally {
      setBusy(null);
    }
  }

  async function executeOos() {
    setBusy("handoff");
    setError(null);
    try {
      const executed = await researchApi.executeOfficialOos(protocolId.trim());
      setOosExecution(executed.execution);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? reason : "Official OOS execution failed.");
    } finally {
      setBusy(null);
    }
  }

  async function submitSelection(request: SelectionRequest) {
    setBusy("selection");
    setError(null);
    try {
      if (!results || !allowSelectionChange) return;
      const saved = await researchApi.createExperimentSelection(results.protocol_id, results.experiment_id, request);
      setSelection(saved);
      setSelectedCandidate(null);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? reason : "Selection could not be persisted.");
    } finally {
      setBusy(null);
    }
  }

  async function submitHandoff() {
    if (!results || freezeBlockReason || handoff) return;
    setBusy("handoff");
    setError(null);
    try {
      const saved = await researchApi.handoffExperimentSelection(results.protocol_id, results.experiment_id);
      setHandoff(saved);
    } catch (reason) {
      setError(reason instanceof ResearchApiError ? `Freeze could not be recorded (${reason.code}). Reload to verify the persisted selection.` : "Freeze could not be recorded.");
    } finally {
      setBusy(null);
    }
  }

  const candidates = results ? [...results.candidates].sort((a, b) => a.candidate_index - b.candidate_index) : [];
  const idsMatch = results?.protocol_id === protocolId.trim() && results?.experiment_id === experimentId.trim();
  const freezeBlockReason = results ? freezeGateReason(results, selection, protocolDetail, idsMatch ? freezeReadError : "EXPERIMENT_BINDING_MISMATCH: Reload the requested experiment.") : "RESULT_READ_UNAVAILABLE";
  const allowSelectionChange = idsMatch && handoff === null && freezeReadError === null && protocolDetail !== null && protocolDetail.freezes.length === 0;

  useEffect(() => {
    if (initialProtocolId && initialExperimentId) void loadResearch();
  }, [initialExperimentId, initialProtocolId]);

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
        <OptimizationResultsExplorer protocolId={results.protocol_id} experimentId={results.experiment_id} parameterLabels={parameterLabels} selectedCandidateId={selection?.selected_candidate_id} />
        <section className="panel"><div className="section-header compact"><div><span className="eyebrow">CANDIDATE RESULTS</span><h2>Persisted IS evidence</h2></div><span className="muted">Candidate index order</span></div><CandidateTable candidates={candidates} parameterLabels={parameterLabels} objective={objectiveByCandidate} selectedCandidateId={selectedCandidate?.candidate_id ?? ""} persistedSelection={selection} onSelect={setSelectedCandidate} allowChange={allowSelectionChange && busy === null} frozen={handoff !== null} /></section>
        {selectedCandidate && allowSelectionChange && (!selection || selectedCandidate.candidate_id !== selection.selected_candidate_id) && <SelectionPanel candidate={selectedCandidate} objectiveState={objectiveByCandidate.get(selectedCandidate.candidate_id)} onCancel={() => setSelectedCandidate(null)} onSubmit={(request) => void submitSelection(request)} busy={busy === "selection"} />}
        {selection && <><PersistedSelection selection={selection} frozen={handoff !== null} /><FreezeSelectionPanel key={selection.selection_id} selection={selection} candidate={candidates.find((item) => item.candidate_id === selection.selected_candidate_id)} results={results} protocol={protocolDetail} labels={parameterLabels} frozen={handoff} blockReason={freezeBlockReason} onFreeze={() => void submitHandoff()} busy={busy !== null} />{handoff && <OfficialOosPanel handoff={handoff} execution={oosExecution} busy={busy === "handoff"} onPrepare={() => void prepareOos()} onExecute={() => void executeOos()} onOpen={() => onOpenOos?.(results.protocol_id)} />}</>}
      </>}
    </main>
  );
}
