import { useEffect, useMemo, useState } from "react";

import { strategyApi } from "../strategy/api";
import type { SemanticTargetCatalog, StrategyVersion } from "../strategy/types";
import { researchApi, ResearchApiError } from "./api";
import { buildParameters, parameterRow, percentageType, type ParameterRow } from "./experimentParameters";
import { semanticTargetDisplay } from "./semanticTargetDisplay";
import type { ExperimentWorkflowList, ExperimentWorkflowRecord, GridSearchPreflight, ResearchProtocolDetail } from "./types";

interface ExperimentWorkbenchProps {
  initialVersion: StrategyVersion | null;
  onBack: () => void;
  onOpenResults: (protocolId: string, experimentId: string) => void;
}

function errorText(error: unknown): string {
  return error instanceof ResearchApiError ? `${error.code}: ${error.message}` : error instanceof Error ? error.message : "Experiment workflow failed.";
}

function protocolStatus(detail: ResearchProtocolDetail): string {
  const value = detail.protocol.status;
  return typeof value === "string" ? value : "draft";
}

export function ExperimentWorkbench({ initialVersion, onBack, onOpenResults }: ExperimentWorkbenchProps) {
  const [catalog, setCatalog] = useState<SemanticTargetCatalog | null>(null);
  const [items, setItems] = useState<ExperimentWorkflowRecord[]>([]);
  const [protocolId, setProtocolId] = useState("");
  const [candidateSetId, setCandidateSetId] = useState("");
  const [name, setName] = useState("Regime 2x2 research");
  const [description, setDescription] = useState("Controlled IS semantic grid.");
  const [isStart, setIsStart] = useState("2007-01-01");
  const [isEnd, setIsEnd] = useState("2020-12-31");
  const [oosStart, setOosStart] = useState("2021-01-01");
  const [oosEnd, setOosEnd] = useState("2026-09-16");
  const [rows, setRows] = useState<ParameterRow[]>([]);
  const [limit, setLimit] = useState(100);
  const [preflight, setPreflight] = useState<GridSearchPreflight | null>(null);
  const [opened, setOpened] = useState<ExperimentWorkflowRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [libraryWarnings, setLibraryWarnings] = useState<string[]>([]);
  const [serviceUnavailable, setServiceUnavailable] = useState(false);

  useEffect(() => {
    void researchApi.listExperiments().then((response: ExperimentWorkflowList) => {
      setItems(response.items);
      setLibraryWarnings(response.warnings ?? []);
      setServiceUnavailable(false);
    }).catch((reason) => { setError(errorText(reason)); setServiceUnavailable(true); });
  }, []);

  useEffect(() => {
    if (!initialVersion) return;
    let active = true;
    void strategyApi.getSemanticTargets(initialVersion.strategy_id, initialVersion.version_id).then((response) => {
      if (!active) return;
      setCatalog(response);
      const supported = response.targets.filter((target) => target.supported);
      const threshold = supported.find((target) => target.value_type === "relative_threshold");
      const qqqAllocation = supported.find((target) => target.value_type === "allocation_weight" && target.descriptor.symbol === "QQQ" && target.descriptor.state_id === "FULL_RISK_ON");
      setRows([threshold, qqqAllocation, ...supported].filter((target, index, all): target is typeof supported[number] => target !== undefined && all.findIndex((item) => item?.parameter_name === target.parameter_name) === index).slice(0, 2).map(parameterRow));
    }).catch((reason) => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [initialVersion]);

  const built = useMemo(() => {
    try { return { value: buildParameters(rows, catalog?.targets ?? [], limit), error: "" }; }
    catch (reason) { return { value: null, error: errorText(reason) }; }
  }, [catalog, limit, rows]);

  const request = useMemo(() => ({
    protocol_id: protocolId,
    base_strategy_version_id: initialVersion?.version_id ?? "",
    name,
    description,
    is_start: isStart,
    is_end: isEnd,
    initial_capital: 100000,
    price_field: "adjusted_close",
    parameter_space: built.value?.parameter_space,
    grid_definition: built.value?.grid_definition,
    objective_specification: { primary_metric: "cagr", metric_directions: { cagr: "maximize" }, secondary_metrics: [], hard_constraints: {}, tie_break_rules: [] },
  }), [built.value, description, initialVersion, isEnd, isStart, name, protocolId]);

  function updateRow(id: string, patch: Partial<ParameterRow>) {
    setRows((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row));
    setPreflight(null);
  }

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await work(); } catch (reason) {
      setError(errorText(reason));
      if (reason instanceof ResearchApiError && reason.kind === "network") setServiceUnavailable(true);
    } finally { setBusy(false); }
  }

  async function freezeProtocol() {
    if (!initialVersion) throw new Error("An immutable strategy version is required.");
    let id = protocolId;
    let detail: ResearchProtocolDetail;
    if (!id) {
      detail = await researchApi.createProtocol({
        is_start_date: isStart, is_end_date: isEnd, oos_start_date: oosStart, oos_end_date: oosEnd,
        selection_rules: ["researcher selects one frozen IS candidate"], allowed_metrics: ["cagr", "max_drawdown"],
        evaluation_config: { price_field_used: "adjusted_close", initial_capital: 100000, commission: { rate: 0, per_order: 0 }, slippage: 0, execution_rule: "next_trading_day_open", fractional_shares: false, rebalance_policy: { frequency: initialVersion.configuration.rebalance_policy.frequency, threshold: initialVersion.configuration.rebalance_policy.threshold }, engine_version: "phase-3.0" },
      });
      id = String(detail.protocol.protocol_id ?? "");
      setProtocolId(id);
    } else {
      detail = await researchApi.getProtocol(id);
    }
    let candidateSet = detail.candidate_sets.find((item) => Array.isArray(item.strategy_version_ids) && item.strategy_version_ids.includes(initialVersion.version_id));
    if (!candidateSet) {
      detail = await researchApi.createCandidateSet(id, [initialVersion.version_id]);
      candidateSet = detail.candidate_sets.at(-1);
    }
    const setId = String(candidateSet?.candidate_set_id ?? "");
    if (!setId) throw new Error("Candidate set was not created.");
    setCandidateSetId(setId);
    if (candidateSet?.status !== "locked") await researchApi.lockCandidateSet(setId);
    if (protocolStatus(detail) !== "frozen") await researchApi.transitionProtocol(id, "frozen");
  }

  async function runPreflight() {
    if (!built.value || !initialVersion || !protocolId) return;
    setPreflight(await researchApi.preflightExperiment(request));
  }

  async function createExperiment() {
    if (!built.value || !initialVersion || !protocolId || !preflight?.can_execute) return;
    const saved = await researchApi.createExperiment(request);
    setOpened(saved);
    setItems((current) => [saved, ...current.filter((item) => item.experiment_id !== saved.experiment_id)]);
  }

  return <main className="app-shell research-shell">
    <header className="topbar"><div><span className="eyebrow">CONTROLLED IS RESEARCH</span><h1>Experiment Builder</h1><p>Create a frozen, deterministic semantic grid from one immutable Strategy Version.</p></div><button className="button button-secondary" type="button" onClick={onBack}>Back</button></header>
    {error && <p className="research-error" role="alert">{error}</p>}
    {libraryWarnings.map((warning) => <p key={warning} role="status">{warning}</p>)}
    {serviceUnavailable && <p role="status">Experiment service unavailable. Reload after connectivity is restored.</p>}
    {!initialVersion && <section className="panel"><h2>Select an immutable Strategy Version</h2><p className="muted">The builder never accepts a mutable draft as its base.</p></section>}
    {initialVersion && <section className="panel experiment-builder-panel">
      <h2>{initialVersion.configuration.name} · v{initialVersion.version_number}</h2><p className="research-hash">{initialVersion.version_id} · {initialVersion.content_hash}</p>
      <div className="inline-fields"><label>Experiment name<input aria-label="Experiment name" value={name} onChange={(event) => setName(event.target.value)} /></label><label>Description<input aria-label="Experiment description" value={description} onChange={(event) => setDescription(event.target.value)} /></label></div>
      <div className="inline-fields"><label>IS start<input type="date" value={isStart} onChange={(event) => setIsStart(event.target.value)} /></label><label>IS end<input type="date" value={isEnd} onChange={(event) => setIsEnd(event.target.value)} /></label><label>OOS start<input type="date" value={oosStart} onChange={(event) => setOosStart(event.target.value)} /></label><label>OOS end<input type="date" value={oosEnd} onChange={(event) => setOosEnd(event.target.value)} /></label></div>
      <div className="action-buttons"><button className="button button-secondary" type="button" disabled={busy || Boolean(protocolId)} onClick={() => void action(freezeProtocol)}>Freeze research protocol</button><span className="muted">{protocolId ? `Protocol ${protocolId} · candidate set ${candidateSetId}` : "Protocol not frozen"}</span></div>
      <h2>Semantic parameters</h2><label>Candidate limit<input aria-label="Candidate limit" type="number" min="1" max="1000" value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></label>
      {rows.map((row, index) => {
        const target = catalog?.targets.find((item) => item.parameter_name === row.targetName);
        const display = target ? semanticTargetDisplay(target, initialVersion.configuration) : null;
        const unit = display?.unit ?? (target && percentageType(target.value_type) ? "%" : "");
        const supported = catalog?.targets.filter((item) => item.supported) ?? [];
        const transitions = supported.filter((item) => item.target_kind.includes("transition") || item.target_kind.includes("condition"));
        const allocations = supported.filter((item) => item.target_kind === "state_allocation_weight");
        const zones = supported.filter((item) => item.target_kind.includes("value_zone"));
        const option = (item: typeof supported[number]) => <option key={item.parameter_name} value={item.parameter_name} disabled={rows.some((other) => other.id !== row.id && other.targetName === item.parameter_name)}>{semanticTargetDisplay(item, initialVersion.configuration).label}</option>;
        return <fieldset key={row.id}>
          <legend>Parameter {index + 1}</legend>
          <div className="inline-fields">
            <label>Target<select aria-label={`Semantic target ${index + 1}`} value={row.targetName} onChange={(event) => { const next = catalog?.targets.find((item) => item.parameter_name === event.target.value); if (next) updateRow(row.id, { ...parameterRow(next), id: row.id }); }}>
              <optgroup label="状态转换参数">{transitions.map(option)}</optgroup>
              <optgroup label="状态仓位参数">{allocations.map(option)}</optgroup>
              <optgroup label="价值区参数">{zones.map(option)}</optgroup>
            </select></label>
            <label>Values{unit && ` (${unit})`}<input aria-label={`Values ${index + 1}`} value={row.values} onChange={(event) => updateRow(row.id, { values: event.target.value })} /></label>
          </div>
          {display && <p className="muted">{display.help} Unit: {display.unit}</p>}
        </fieldset>;
      })}
      <p aria-label="Candidate count">Candidate Count: {built.value ? `${built.value.counts.join(" × ")} = ${built.value.count}` : "Unavailable"}</p>{built.error && <p className="field-error" role="alert">{built.error}</p>}
      <div className="action-buttons"><button className="button button-secondary" type="button" disabled={busy || !built.value || !protocolId} onClick={() => void action(runPreflight)}>Run preflight</button><button className="button button-primary" type="button" disabled={busy || !preflight?.can_execute} onClick={() => void action(createExperiment)}>Create immutable experiment</button></div>
      {preflight && <div aria-label="Builder preflight counts"><strong>{preflight.status.toUpperCase()}</strong><p>Theoretical {preflight.theoretical_count} · Valid {preflight.valid_count} · Executable {preflight.executable_count} · Duplicates {preflight.duplicate_count} · Pruned {preflight.pruned_count}</p>{preflight.issues.map((issue) => <p key={issue}>{issue}</p>)}</div>}
      {opened && <section className="panel" aria-label="Saved experiment"><h2>{opened.display_name}</h2><p>{opened.experiment_id}</p><button className="button button-primary" type="button" onClick={() => onOpenResults(opened.protocol_id, opened.experiment_id)}>Open Research</button></section>}
    </section>}
    <section className="panel"><h2>Experiment Library</h2>{items.length === 0 ? <p className="muted">No experiments saved yet.</p> : <div className="table-scroll"><table><thead><tr><th>Name</th><th>Base version</th><th>IS range</th><th>Status</th><th>Action</th></tr></thead><tbody>{items.map((item) => <tr key={item.experiment_id}><td>{item.display_name}</td><td>{item.base_strategy_version_id}</td><td>{item.is_start_date} → {item.is_end_date}</td><td>{item.status}</td><td><button className="button button-secondary" type="button" onClick={() => onOpenResults(item.protocol_id, item.experiment_id)}>Open Research</button></td></tr>)}</tbody></table></div>}</section>
  </main>;
}
