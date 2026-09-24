import { useMemo, useState } from "react";

import {
  conditionSummary,
  ConditionTreeEditor,
} from "./ConditionTreeEditor";
import {
  createBlankRegimeEditor,
  createCondition,
  createDefaultRegimeEditor,
  validateRegimeEditor,
} from "./editor";
import type {
  EditorAllocation,
  EditorRegime,
  EditorRegimeTransition,
  EditorState,
  EditorValueZone,
  RegimeEditorState,
  ValidationIssue,
} from "./types";

type Selection = { kind: "state" | "transition" | "zone"; id: string };

function allocationTotal(allocations: EditorAllocation[]): number {
  return allocations.reduce((total, allocation) => total + Number(allocation.targetWeightPercent || 0), 0);
}

function stateLabel(state: EditorRegime): string {
  return state.displayName || state.stateId || "Untitled state";
}

function issueFor(issues: ValidationIssue[], path: string): ValidationIssue | undefined {
  return issues.find((issue) => issue.path.includes(path));
}

function AllocationRows({
  allocations,
  assets,
  onChange,
  onAdd,
  onRemove,
}: {
  allocations: EditorAllocation[];
  assets: string[];
  onChange: (allocation: EditorAllocation) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="regime-allocation-editor">
      {allocations.map((allocation) => (
        <div className="allocation-row" key={allocation.id}>
          <select aria-label="State allocation asset" value={allocation.symbol} onChange={(event) => onChange({ ...allocation, symbol: event.target.value })}>
            {assets.map((asset) => <option key={asset} value={asset}>{asset}</option>)}
          </select>
          <div className="weight-input"><input aria-label="State allocation weight percent" inputMode="decimal" value={allocation.targetWeightPercent} onChange={(event) => onChange({ ...allocation, targetWeightPercent: event.target.value })} /><span>%</span></div>
          <button className="button button-quiet" type="button" onClick={() => onRemove(allocation.id)}>Remove</button>
        </div>
      ))}
      <div className="allocation-actions"><button className="button button-quiet" type="button" onClick={onAdd}>+ Add asset allocation</button><span className={allocationTotal(allocations) > 100 ? "invalid-number" : "valid-number"}>Invested {allocationTotal(allocations).toFixed(2)}% · Cash {Math.max(0, 100 - allocationTotal(allocations)).toFixed(2)}% · Total 100%</span></div>
    </div>
  );
}

function StateEditor({
  state,
  model,
  assets,
  issues,
  onChange,
}: {
  state: EditorRegime;
  model: RegimeEditorState;
  assets: string[];
  issues: ValidationIssue[];
  onChange: (next: RegimeEditorState) => void;
}) {
  const updateState = (patch: Partial<EditorRegime>) => {
    const oldId = state.stateId;
    const nextState = { ...state, ...patch };
    const next = { ...model, regimes: model.regimes.map((item) => item.id === state.id ? nextState : item) };
    if (patch.stateId !== undefined && patch.stateId !== oldId) {
      next.initialRegime = model.initialRegime === oldId ? patch.stateId : model.initialRegime;
      next.transitions = model.transitions.map((transition) => ({
        ...transition,
        fromState: transition.fromState === oldId ? patch.stateId ?? oldId : transition.fromState,
        toState: transition.toState === oldId ? patch.stateId ?? oldId : transition.toState,
      }));
    }
    onChange(next);
  };
  const referenced = model.transitions.some((transition) => transition.fromState === state.stateId || transition.toState === state.stateId) || model.initialRegime === state.stateId;
  return (
    <section className="regime-editor-panel">
      <div className="section-header"><div><span className="eyebrow">SELECTED STATE</span><h3>{stateLabel(state)}</h3></div><span className="regime-selection-badge">{state.stateId}</span></div>
      <div className="inline-fields">
        <label>State ID<input aria-label="State ID" value={state.stateId} onChange={(event) => updateState({ stateId: event.target.value })} /><small>Machine-readable and unique.</small></label>
        <label>Display Name<input aria-label="State display name" value={state.displayName} onChange={(event) => updateState({ displayName: event.target.value })} /></label>
      </div>
      <label>Description / Help text<textarea aria-label="State description" rows={2} value={state.description} onChange={(event) => updateState({ description: event.target.value })} /></label>
      <label className="initial-state-select">Initial State<select aria-label="Initial state" value={model.initialRegime} onChange={(event) => onChange({ ...model, initialRegime: event.target.value })}>{model.regimes.map((item) => <option key={item.id} value={item.stateId}>{stateLabel(item)}</option>)}</select></label>
      <div className="subsection-heading"><div><span className="eyebrow">TARGET ALLOCATION</span><h4>What to hold after entering this state</h4></div><strong>{allocationTotal(state.allocations).toFixed(2)}% invested</strong></div>
      <AllocationRows allocations={state.allocations} assets={assets} onChange={(allocation) => updateState({ allocations: state.allocations.map((item) => item.id === allocation.id ? allocation : item) })} onAdd={() => updateState({ allocations: [...state.allocations, { id: `regime-allocation-${Date.now()}`, symbol: assets[0] ?? "QQQ", targetWeightPercent: "0" }] })} onRemove={(id) => updateState({ allocations: state.allocations.filter((item) => item.id !== id) })} />
      {issueFor(issues, state.stateId) && <div className="field-error">{issueFor(issues, state.stateId)?.message}</div>}
      <button className="button button-quiet" type="button" disabled={referenced} title={referenced ? "Remove incoming/outgoing transitions and initial references first." : undefined} onClick={() => onChange({ ...model, regimes: model.regimes.filter((item) => item.id !== state.id), initialRegime: model.initialRegime === state.stateId ? "" : model.initialRegime })}>Delete state</button>
      {referenced && <span className="muted">State is referenced by a transition or is initial; deletion is blocked.</span>}
    </section>
  );
}

function TransitionEditor({
  transition,
  model,
  state,
  issues,
  onChange,
}: {
  transition: EditorRegimeTransition;
  model: RegimeEditorState;
  state: EditorState;
  issues: ValidationIssue[];
  onChange: (next: RegimeEditorState) => void;
}) {
  const update = (patch: Partial<EditorRegimeTransition>) => onChange({ ...model, transitions: model.transitions.map((item) => item.id === transition.id ? { ...item, ...patch } : item) });
  return (
    <section className="regime-editor-panel">
      <div className="section-header"><div><span className="eyebrow">SELECTED TRANSITION</span><h3>{transition.fromState} → {transition.toState}</h3></div><span className="regime-selection-badge">Priority {transition.priority}</span></div>
      <div className="inline-fields">
        <label>Transition ID<input aria-label="Transition ID" value={transition.transitionId} onChange={(event) => update({ transitionId: event.target.value })} /></label>
        <label>Priority<input aria-label="Transition priority" type="number" step="1" value={transition.priority} onChange={(event) => update({ priority: event.target.value })} /></label>
      </div>
      <div className="inline-fields">
        <label>From State<select aria-label="Transition from state" value={transition.fromState} onChange={(event) => update({ fromState: event.target.value })}>{model.regimes.map((item) => <option key={item.id} value={item.stateId}>{stateLabel(item)}</option>)}</select></label>
        <label>To State<select aria-label="Transition to state" value={transition.toState} onChange={(event) => update({ toState: event.target.value })}>{model.regimes.map((item) => <option key={item.id} value={item.stateId}>{stateLabel(item)}</option>)}</select></label>
      </div>
      <label>Description<input aria-label="Transition description" value={transition.description} onChange={(event) => update({ description: event.target.value })} /></label>
      <div className="subsection-heading"><div><span className="eyebrow">TRANSITION CONDITION</span><h4>Reuse 12D Condition / Timeframe / Threshold</h4></div></div>
      <ConditionTreeEditor group={transition.condition} assets={state.assets} priceField={state.priceField} issues={issues} onChange={(condition) => update({ condition })} />
      <div className="inline-fields">
        <label>Value Zone reference<select aria-label="Transition value zone" value={transition.valueZoneId} onChange={(event) => update({ valueZoneId: event.target.value })}><option value="">No Value Zone trigger</option>{model.valueZones.map((zone) => <option key={zone.id} value={zone.zoneId}>{zone.displayName || zone.zoneId}</option>)}</select></label>
        <label>Value Zone trigger<select aria-label="Value Zone trigger" value={transition.valueZoneTrigger} disabled={!transition.valueZoneId} onChange={(event) => update({ valueZoneTrigger: event.target.value as EditorRegimeTransition["valueZoneTrigger"] })}><option value="match">Match</option><option value="enter">Enter</option><option value="exit">Exit</option></select></label>
      </div>
      <button className="button button-quiet" type="button" onClick={() => onChange({ ...model, transitions: model.transitions.filter((item) => item.id !== transition.id) })}>Delete transition</button>
    </section>
  );
}

function ValueZoneEditor({ zone, model, state, onChange }: { zone: EditorValueZone; model: RegimeEditorState; state: EditorState; onChange: (next: RegimeEditorState) => void }) {
  const update = (patch: Partial<EditorValueZone>) => onChange({ ...model, valueZones: model.valueZones.map((item) => item.id === zone.id ? { ...item, ...patch } : item) });
  return (
    <section className="regime-editor-panel">
      <div className="section-header"><div><span className="eyebrow">VALUE ZONE</span><h3>{zone.displayName || zone.zoneId}</h3></div><span className="regime-selection-badge">Priority {zone.priority}</span></div>
      <div className="inline-fields"><label>Zone ID<input aria-label="Value Zone ID" value={zone.zoneId} onChange={(event) => update({ zoneId: event.target.value })} /></label><label>Display Name<input aria-label="Value Zone display name" value={zone.displayName} onChange={(event) => update({ displayName: event.target.value })} /></label></div>
      <div className="inline-fields"><label>Asset<select aria-label="Value Zone asset" value={zone.asset} onChange={(event) => update({ asset: event.target.value })}>{state.assets.map((asset) => <option key={asset} value={asset}>{asset}</option>)}</select></label><label>Timeframe<select aria-label="Value Zone timeframe" value={zone.timeframe} onChange={(event) => update({ timeframe: event.target.value as EditorValueZone["timeframe"] })}><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label><label>Indicator<select aria-label="Value Zone indicator" value={zone.indicatorKind} onChange={(event) => update({ indicatorKind: event.target.value as EditorValueZone["indicatorKind"] })}><option value="ma">MA</option><option value="ema">EMA</option></select></label><label>Period<input aria-label="Value Zone period" type="number" min="1" step="1" value={zone.period} onChange={(event) => update({ period: event.target.value })} /></label></div>
      <div className="inline-fields"><label>Entry operator<select aria-label="Value Zone entry operator" value={zone.entryOperator} onChange={(event) => update({ entryOperator: event.target.value as EditorValueZone["entryOperator"] })}><option value="less_or_equal">≤</option><option value="less_than">&lt;</option><option value="greater_or_equal">≥</option><option value="greater_than">&gt;</option></select></label><label>Entry threshold (%)<input aria-label="Value Zone entry threshold" inputMode="decimal" value={zone.entryThresholdPercent} onChange={(event) => update({ entryThresholdPercent: event.target.value })} /></label><label>Exit operator<select aria-label="Value Zone exit operator" value={zone.exitOperator} onChange={(event) => update({ exitOperator: event.target.value as EditorValueZone["exitOperator"] })}><option value="greater_or_equal">≥</option><option value="greater_than">&gt;</option><option value="less_or_equal">≤</option><option value="less_than">&lt;</option></select></label><label>Exit threshold (%)<input aria-label="Value Zone exit threshold" inputMode="decimal" value={zone.exitThresholdPercent} onChange={(event) => update({ exitThresholdPercent: event.target.value })} placeholder="optional" /></label><label>Priority<input aria-label="Value Zone priority" type="number" step="1" value={zone.priority} onChange={(event) => update({ priority: event.target.value })} /></label></div>
      <p className="strategy-semantics-note">Entry and Exit are separate hysteresis thresholds. Value Zone runtime semantics remain backend-owned.</p>
      <button className="button button-quiet" type="button" onClick={() => onChange({ ...model, valueZones: model.valueZones.filter((item) => item.id !== zone.id) })}>Delete Value Zone</button>
    </section>
  );
}

export function RegimeStateMachineEditor({ state, model, issues, onChange }: { state: EditorState; model: RegimeEditorState; issues: ValidationIssue[]; onChange: (model: RegimeEditorState) => void }) {
  const [selection, setSelection] = useState<Selection>({ kind: "state", id: model.regimes[0]?.id ?? "" });
  const [view, setView] = useState<"flow" | "states" | "transitions" | "zones">("flow");
  const [template, setTemplate] = useState<RegimeEditorState["template"]>(model.template);
  const selectedState = model.regimes.find((item) => item.id === selection.id);
  const selectedTransition = model.transitions.find((item) => item.id === selection.id);
  const selectedZone = model.valueZones.find((item) => item.id === selection.id);
  const localIssues = useMemo(() => validateRegimeEditor(model, state.assets), [model, state.assets]);
  const allIssues = issues.length > 0 ? issues : localIssues;
  const chooseTemplate = () => {
    const next = template === "long_term_value_trend" ? createDefaultRegimeEditor(state.assets) : createBlankRegimeEditor(state.assets);
    onChange(next);
    setSelection({ kind: "state", id: next.regimes[0]?.id ?? "" });
  };
  const addState = () => {
    const nextState: EditorRegime = { id: `regime-${Date.now()}`, stateId: `STATE_${model.regimes.length + 1}`, displayName: `State ${model.regimes.length + 1}`, description: "", allocations: [{ id: `regime-allocation-${Date.now()}`, symbol: state.assets[0] ?? "QQQ", targetWeightPercent: "0" }], metadata: {} };
    const next = { ...model, regimes: [...model.regimes, nextState] };
    onChange(next); setSelection({ kind: "state", id: nextState.id });
  };
  const addTransition = () => {
    const from = model.regimes[0]?.stateId ?? "";
    const to = model.regimes[1]?.stateId ?? "";
    const nextTransition: EditorRegimeTransition = { id: `transition-${Date.now()}`, transitionId: `transition-${model.transitions.length + 1}`, fromState: from, toState: to, priority: "1", description: "", condition: { id: `transition-group-${Date.now()}`, type: "group", operator: "and", children: [createCondition(state.assets)] }, valueZoneId: "", valueZoneTrigger: "match" };
    const next = { ...model, transitions: [...model.transitions, nextTransition] };
    onChange(next); setSelection({ kind: "transition", id: nextTransition.id });
  };
  const addZone = () => {
    const nextZone: EditorValueZone = { id: `value-zone-${Date.now()}`, zoneId: `ZONE_${model.valueZones.length + 1}`, displayName: `Zone ${model.valueZones.length + 1}`, asset: state.assets[0] ?? "QQQ", timeframe: "weekly", indicatorKind: "ma", period: "200", priceField: state.priceField, entryThresholdPercent: "0", exitThresholdPercent: "3", entryOperator: "less_or_equal", exitOperator: "greater_or_equal", priority: String(model.valueZones.length + 1), metadata: {} };
    const next = { ...model, valueZones: [...model.valueZones, nextZone] };
    onChange(next); setSelection({ kind: "zone", id: nextZone.id });
  };
  return (
    <section className="panel regime-machine-panel">
      <div className="section-header"><div><span className="eyebrow">REGIME STATE MACHINE</span><h2>Visual State Machine Editor</h2><p className="muted">Domain payload remains canonical. Flow layout and selection are presentation only.</p></div><div className="regime-template-controls"><label>Template<select aria-label="Regime template" value={template} onChange={(event) => setTemplate(event.target.value as RegimeEditorState["template"])}><option value="blank">Blank State Machine</option><option value="long_term_value_trend">Long-Term Value / Trend Template</option></select></label><button className="button button-secondary" type="button" onClick={chooseTemplate}>Apply template</button></div></div>
      <div className="strategy-semantics-note"><strong>Protective vs Recovery:</strong> DEEP_VALUE → RECOVERY_HOLD is recovery holding; FULL_RISK_ON → PROTECTIVE is risk protection. The default template does not create a FULL_RISK_ON → RECOVERY_HOLD path.</div>
      <div className="regime-validation-panel"><div className="section-header compact"><div><span className="eyebrow">VALIDATION</span><h3>{allIssues.filter((issue) => !issue.code.startsWith("Unreachable")).length === 0 ? "No blocking errors" : "Review configuration"}</h3></div><span className={allIssues.some((issue) => !issue.code.startsWith("Unreachable")) ? "invalid-number" : "valid-number"}>{allIssues.some((issue) => !issue.code.startsWith("Unreachable")) ? `${allIssues.filter((issue) => !issue.code.startsWith("Unreachable")).length} error(s)` : "PASS"}</span></div>{allIssues.length === 0 ? <p className="muted">Initial state, references, allocations, conditions, priorities and Value Zones are valid.</p> : <ul className="validation-list">{allIssues.map((issue, index) => <li className={issue.code.startsWith("Unreachable") ? "validation-warning" : "validation-error"} key={`${issue.code}-${issue.path}-${index}`}><strong>{issue.code}</strong><span>{issue.message}</span></li>)}</ul>}</div>
      <div className="regime-tabs"><button className={view === "flow" ? "is-active" : ""} type="button" onClick={() => setView("flow")}>Flow View</button><button className={view === "states" ? "is-active" : ""} type="button" onClick={() => setView("states")}>State Table</button><button className={view === "transitions" ? "is-active" : ""} type="button" onClick={() => setView("transitions")}>Transition Table</button><button className={view === "zones" ? "is-active" : ""} type="button" onClick={() => setView("zones")}>Value Zones</button></div>
      {view === "flow" && <div className="regime-flow-layout"><div className="regime-flow-view"><div className="section-header compact"><div><span className="eyebrow">FLOW PROJECTION</span><h3>State connections</h3></div><button className="button button-quiet" type="button" onClick={addState}>+ Add state</button></div><div className="regime-flow-nodes">{model.regimes.map((item) => { const incoming = model.transitions.filter((transition) => transition.toState === item.stateId).length; const outgoing = model.transitions.filter((transition) => transition.fromState === item.stateId).length; return <button className={`regime-node ${selection.id === item.id ? "is-selected" : ""}`} type="button" key={item.id} onClick={() => setSelection({ kind: "state", id: item.id })}><span className="regime-node-top">{model.initialRegime === item.stateId && <b>INITIAL</b>}<small>{item.stateId}</small></span><strong>{stateLabel(item)}</strong><span>Invested {allocationTotal(item.allocations).toFixed(0)}% · Cash {Math.max(0, 100 - allocationTotal(item.allocations)).toFixed(0)}%</span><small>Incoming {incoming} · Outgoing {outgoing}</small></button>; })}</div><div className="regime-edge-list"><div className="subsection-heading"><h4>Transition edges</h4><button className="button button-quiet" type="button" disabled={model.regimes.length < 2} onClick={addTransition}>+ Add transition</button></div>{model.transitions.map((transition) => <button className={`regime-edge ${selection.id === transition.id ? "is-selected" : ""}`} type="button" key={transition.id} onClick={() => setSelection({ kind: "transition", id: transition.id })}><strong>{transition.fromState} → {transition.toState}</strong><span>Priority {transition.priority} · {conditionSummary(transition.condition)}</span>{transition.valueZoneId && <small>Value Zone: {transition.valueZoneId} ({transition.valueZoneTrigger})</small>}</button>)}</div></div><div className="regime-detail-pane">{selectedState && <StateEditor state={selectedState} model={model} assets={state.assets} issues={allIssues} onChange={onChange} />}{selectedTransition && <TransitionEditor transition={selectedTransition} model={model} state={state} issues={allIssues} onChange={onChange} />}{selectedZone && <ValueZoneEditor zone={selectedZone} model={model} state={state} onChange={onChange} />}</div></div>}
      {view === "states" && <div className="regime-table-wrap"><div className="section-header compact"><div><span className="eyebrow">STATE TABLE</span><h3>Declared states</h3></div><button className="button button-secondary" type="button" onClick={addState}>+ Add state</button></div><table className="regime-table"><thead><tr><th>State ID</th><th>Display Name</th><th>Initial</th><th>Invested</th><th>Cash</th><th>Incoming</th><th>Outgoing</th><th>Validation</th></tr></thead><tbody>{model.regimes.map((item) => <tr key={item.id} onClick={() => { setSelection({ kind: "state", id: item.id }); setView("flow"); }}><td>{item.stateId}</td><td>{stateLabel(item)}</td><td>{model.initialRegime === item.stateId ? "INITIAL" : ""}</td><td>{allocationTotal(item.allocations).toFixed(2)}%</td><td>{Math.max(0, 100 - allocationTotal(item.allocations)).toFixed(2)}%</td><td>{model.transitions.filter((transition) => transition.toState === item.stateId).length}</td><td>{model.transitions.filter((transition) => transition.fromState === item.stateId).length}</td><td>{issueFor(allIssues, item.stateId) ? "ERROR" : "OK"}</td></tr>)}</tbody></table></div>}
      {view === "transitions" && <div className="regime-table-wrap"><div className="section-header compact"><div><span className="eyebrow">TRANSITION TABLE</span><h3>Prioritized edges</h3></div><button className="button button-secondary" type="button" disabled={model.regimes.length < 2} onClick={addTransition}>+ Add transition</button></div><table className="regime-table"><thead><tr><th>ID</th><th>From</th><th>To</th><th>Priority</th><th>Condition</th><th>Value Zone</th><th>Validation</th></tr></thead><tbody>{model.transitions.map((item) => <tr key={item.id} onClick={() => { setSelection({ kind: "transition", id: item.id }); setView("flow"); }}><td>{item.transitionId}</td><td>{item.fromState}</td><td>{item.toState}</td><td>{item.priority}</td><td>{conditionSummary(item.condition)}</td><td>{item.valueZoneId || "-"}</td><td>{issueFor(allIssues, item.transitionId) ? "ERROR" : "OK"}</td></tr>)}</tbody></table></div>}
      {view === "zones" && <div className="regime-table-wrap"><div className="section-header compact"><div><span className="eyebrow">VALUE ZONES</span><h3>Entry / Exit hysteresis bands</h3></div><button className="button button-secondary" type="button" onClick={addZone}>+ Add Value Zone</button></div><table className="regime-table"><thead><tr><th>Zone</th><th>Reference</th><th>Entry</th><th>Exit</th><th>Priority</th><th>Validation</th></tr></thead><tbody>{model.valueZones.map((item) => <tr key={item.id} onClick={() => { setSelection({ kind: "zone", id: item.id }); setView("flow"); }}><td>{item.displayName} ({item.zoneId})</td><td>{item.asset} {item.timeframe.toUpperCase()} {item.indicatorKind.toUpperCase()}{item.period}</td><td>{item.entryThresholdPercent}%</td><td>{item.exitThresholdPercent || "-"}%</td><td>{item.priority}</td><td>{issueFor(allIssues, item.zoneId) ? "ERROR" : "OK"}</td></tr>)}</tbody></table></div>}
    </section>
  );
}
