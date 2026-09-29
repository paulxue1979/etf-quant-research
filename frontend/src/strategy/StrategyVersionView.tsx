import { useEffect, useState } from "react";

import { strategyApi, StrategyApiError } from "./api";
import type { SemanticTargetCatalog, StrategyVersion, StrategyVersionSummary, VersionDiff } from "./types";

interface StrategyVersionViewProps {
  version: StrategyVersion;
  versions: StrategyVersionSummary[];
  diff: VersionDiff | null;
  onBack: () => void;
  onCreateDraft: () => void;
  onClone: (request: { new_strategy_id: string; name: string; description: string }) => void;
  onCompare: (versionId: string) => void;
  onRunBacktest: () => void;
  onCreateExperiment?: () => void;
}

function value(value: unknown): string {
  if (value === undefined) return "Not present";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function StrategyVersionView({ version, versions, diff, onBack, onCreateDraft, onClone, onCompare, onRunBacktest, onCreateExperiment }: StrategyVersionViewProps) {
  const [compareId, setCompareId] = useState("");
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneId, setCloneId] = useState(`${version.strategy_id}-clone`);
  const [cloneName, setCloneName] = useState(`${version.configuration.name} Copy`);
  const [cloneDescription, setCloneDescription] = useState(version.configuration.description);
  const [semanticTargets, setSemanticTargets] = useState<SemanticTargetCatalog | null>(null);
  const [semanticError, setSemanticError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setSemanticTargets(null);
    setSemanticError(null);
    void strategyApi.getSemanticTargets(version.strategy_id, version.version_id).then((catalog) => {
      if (active) setSemanticTargets(catalog);
    }).catch((error: unknown) => {
      if (active) setSemanticError(error instanceof StrategyApiError ? error.message : "Semantic target discovery failed.");
    });
    return () => { active = false; };
  }, [version.strategy_id, version.version_id]);
  const configuration = version.configuration;
  const assets = configuration.assets.map((asset) => asset.symbol).join(" · ");
  const mode = version.active_mode ?? configuration.strategy_mode ?? "rule_based";
  return (
    <main className="app-shell strategy-version-shell">
      <header className="topbar"><div><span className="eyebrow">IMMUTABLE STRATEGY VERSION</span><h1>{configuration.name} · v{version.version_number}</h1><p>{version.version_id}</p></div><div className="topbar-status"><button className="button button-secondary" type="button" onClick={onBack}>Back to library</button><button className="button button-primary" type="button" onClick={onRunBacktest}>Run Backtest</button>{onCreateExperiment && <button className="button button-primary" type="button" onClick={onCreateExperiment}>Create Experiment</button>}</div></header>
      <section className="panel version-readonly-banner"><strong>READ ONLY</strong><span>This snapshot cannot be edited. Create a draft to continue editing.</span><div className="action-buttons"><button className="button button-secondary" type="button" onClick={onCreateDraft}>Create Draft From Version</button><button className="button button-quiet" type="button" onClick={() => setCloneOpen((open) => !open)}>Clone / Save As New Strategy</button></div></section>
      {cloneOpen && <section className="panel clone-panel"><div className="inline-fields"><label>New strategy ID<input value={cloneId} onChange={(event) => setCloneId(event.target.value)} /></label><label>Name<input value={cloneName} onChange={(event) => setCloneName(event.target.value)} /></label><label>Description<input value={cloneDescription} onChange={(event) => setCloneDescription(event.target.value)} /></label></div><button className="button button-primary" type="button" disabled={!cloneId.trim() || !cloneName.trim()} onClick={() => onClone({ new_strategy_id: cloneId.trim(), name: cloneName.trim(), description: cloneDescription })}>Create new strategy draft</button></section>}
      <section className="panel"><div className="section-header"><div><span className="eyebrow">SEMANTIC SNAPSHOT</span><h2>Strategy configuration</h2></div><span className="preview-tag">{mode}</span></div><div className="preview-grid"><div><span>Assets</span><strong>{assets || "None"}</strong></div><div><span>Rules</span><strong>{configuration.rules.length}</strong></div><div><span>Source draft</span><strong>{version.source_draft_revision ? `r${version.source_draft_revision}` : "Legacy / external"}</strong></div><div><span>Content hash</span><strong>{version.content_hash.slice(0, 12)}</strong></div></div><details className="version-details" open><summary>Read-only configuration JSON</summary><pre>{JSON.stringify(configuration, null, 2)}</pre></details></section>
      {mode === "regime_state_machine" && <section className="panel"><div className="section-header compact"><div><span className="eyebrow">GRID TARGET DISCOVERY</span><h2>Tunable semantic targets</h2></div><span className="preview-tag">{semanticTargets?.targets.length ?? "..."}</span></div>{semanticError ? <p className="field-error">{semanticError}</p> : semanticTargets ? <><p className="muted">Targets are bound by transition, state, zone and operand identity. Internal array paths are intentionally hidden.</p><div className="table-scroll"><table><thead><tr><th>Target</th><th>Type</th><th>Binding ID</th></tr></thead><tbody>{semanticTargets.targets.map((target) => <tr key={target.parameter_name}><td>{target.label}</td><td>{target.value_type}</td><td>{target.parameter_name}</td></tr>)}</tbody></table></div>{semanticTargets.unsupported.length > 0 && <p className="muted">Unsupported structural targets are reported and never silently bound: {semanticTargets.unsupported.map((item) => item.reason).join(" · ")}</p>}</> : <p className="muted">Discovering tunable targets...</p>}</section>}
      <section className="panel"><div className="section-header compact"><div><span className="eyebrow">VERSION DIFF</span><h2>Compare immutable versions</h2></div></div><div className="inline-fields"><label>Compare with<select aria-label="Compare version" value={compareId} onChange={(event) => { setCompareId(event.target.value); if (event.target.value) onCompare(event.target.value); }}><option value="">Select another version</option>{versions.filter((item) => item.version_id !== version.version_id).map((item) => <option key={item.version_id} value={item.version_id}>v{item.version_number}</option>)}</select></label></div>{diff && <div className="table-scroll"><table><thead><tr><th>Semantic path</th><th>Change</th><th>Before</th><th>After</th></tr></thead><tbody>{diff.items.length === 0 ? <tr><td colSpan={4}>No semantic differences.</td></tr> : diff.items.map((item) => <tr key={`${item.path}-${item.change}`}><td>{item.path}</td><td>{item.change}</td><td>{value(item.before)}</td><td>{value(item.after)}</td></tr>)}</tbody></table></div>}</section>
    </main>
  );
}
