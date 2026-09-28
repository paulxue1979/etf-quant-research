import { useMemo, useState } from "react";

import type { StrategyDiagnostics, StrategyMode, StrategyRecord } from "./types";

interface StrategyLibraryProps {
  strategies: StrategyRecord[];
  loading: boolean;
  onNew: (request: { strategy_id: string; name: string; description: string }) => void;
  onOpenDraft: (strategyId: string) => void;
  onOpenLatest: (strategyId: string, versionId: string) => void;
  onOpenHistory: (strategyId: string, versionId: string) => void;
  onClone: (strategyId: string, versionId: string) => void;
  onTrash: (strategyId: string) => void;
  onRestore: (strategyId: string) => void;
  onProtect: (strategyId: string, protectedValue: boolean) => void;
  onPermanentDelete: (strategyId: string) => void;
  onBulkTrash: (strategyIds: string[]) => void;
  diagnostics?: StrategyDiagnostics | null;
  onBack: () => void;
}

export function StrategyLibrary({ strategies, loading, onNew, onOpenDraft, onOpenLatest, onOpenHistory, onClone, onTrash, onRestore, onProtect, onPermanentDelete, onBulkTrash, diagnostics, onBack }: StrategyLibraryProps) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"all" | StrategyMode>("all");
  const [newOpen, setNewOpen] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [tab, setTab] = useState<"ACTIVE" | "TRASHED">("ACTIVE");
  const [selected, setSelected] = useState<string[]>([]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return strategies.filter((item) => {
      if ((item.lifecycle_status ?? "ACTIVE") !== tab) return false;
      const matchesText = !normalized || [item.name, item.strategy_id, item.description].some((value) => value.toLowerCase().includes(normalized));
      const matchesMode = mode === "all" || item.draft_active_mode === mode;
      return matchesText && matchesMode;
    });
  }, [mode, query, strategies, tab]);

  const toggleSelected = (strategyId: string) => {
    setSelected((current) => current.includes(strategyId) ? current.filter((item) => item !== strategyId) : [...current, strategyId]);
  };

  const switchTab = (next: "ACTIVE" | "TRASHED") => {
    setTab(next);
    setSelected([]);
  };

  return (
    <main className="app-shell strategy-library-shell">
      <header className="topbar">
        <div><span className="eyebrow">ETF QUANT RESEARCH SYSTEM · STRATEGY LIBRARY</span><h1>Strategy Library</h1><p>Browse identities, drafts, and immutable strategy versions.</p></div>
        <div className="topbar-status"><button className="button button-secondary" type="button" onClick={onBack}>Back to editor</button>{tab === "ACTIVE" && <button className="button button-primary" type="button" onClick={() => setNewOpen((open) => !open)}>+ New Strategy</button>}</div>
      </header>
      {newOpen && <section className="panel clone-panel"><div className="section-header compact"><div><span className="eyebrow">NEW IDENTITY</span><h2>Start a clean draft</h2></div></div><div className="inline-fields"><label>Strategy ID<input aria-label="New strategy ID" value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="my-strategy" /></label><label>Strategy name<input aria-label="New strategy name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="My Strategy" /></label><label>Description<input aria-label="New strategy description" value={newDescription} onChange={(event) => setNewDescription(event.target.value)} /></label></div><p className="muted">A new strategy starts with a clean QQQ-only draft. Existing rules, regimes, and version lineage are not copied.</p><div className="action-buttons"><button className="button button-primary" type="button" disabled={!newId.trim() || !newName.trim()} onClick={() => { onNew({ strategy_id: newId.trim(), name: newName.trim(), description: newDescription }); setNewOpen(false); }}>Create draft</button><button className="button button-secondary" type="button" onClick={() => setNewOpen(false)}>Cancel</button></div></section>}
      <section className="panel library-toolbar">
        <div className="segmented-control" aria-label="Strategy lifecycle"><button className={tab === "ACTIVE" ? "is-selected" : ""} type="button" onClick={() => switchTab("ACTIVE")}>Active</button><button className={tab === "TRASHED" ? "is-selected" : ""} type="button" onClick={() => switchTab("TRASHED")}>Trash</button></div>
        <label>Search strategies<input aria-label="Search strategies" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or strategy ID" /></label>
        <label>Mode<select aria-label="Filter strategy mode" value={mode} onChange={(event) => setMode(event.target.value as "all" | StrategyMode)}><option value="all">All modes</option><option value="rule_based">Rule Based</option><option value="regime_state_machine">Regime State Machine</option></select></label>
        <span className="muted">{filtered.length} of {strategies.filter((item) => (item.lifecycle_status ?? "ACTIVE") === tab).length} strategies</span>
        {tab === "ACTIVE" && <button className="button button-secondary" type="button" disabled={selected.length === 0} onClick={() => { onBulkTrash(selected); setSelected([]); }}>Move selected to Trash</button>}
      </section>
      {diagnostics && <section className="panel" aria-label="Strategy diagnostics"><div className="section-header compact"><div><span className="eyebrow">SYSTEM DIAGNOSTICS</span><h2>Canonical persistence</h2></div><span className="preview-tag">Schema v{diagnostics.schema_version ?? "?"}</span></div><p className="muted strategy-id-text">{diagnostics.database_path}</p><dl className="library-meta"><div><dt>Strategies</dt><dd>{diagnostics.strategy_count}</dd></div><div><dt>Drafts</dt><dd>{diagnostics.draft_count}</dd></div><div><dt>Versions</dt><dd>{diagnostics.version_count}</dd></div></dl></section>}
      {loading ? <section className="panel"><p className="muted">Loading strategy identities...</p></section> : filtered.length === 0 ? <section className="panel"><p className="muted">No strategies match the current filter.</p></section> : <div className="library-grid">{filtered.map((strategy) => (
        <article className="panel library-card" key={strategy.strategy_id}>
          <div className="section-header compact"><div>{tab === "ACTIVE" && <input aria-label={`Select ${strategy.name}`} type="checkbox" checked={selected.includes(strategy.strategy_id)} onChange={() => toggleSelected(strategy.strategy_id)} />}<span className="eyebrow">{tab === "TRASHED" ? "TRASHED STRATEGY" : "STRATEGY IDENTITY"}</span><h2>{strategy.name}</h2></div><span className="preview-tag">{strategy.draft_active_mode ?? "No draft"}</span></div>
          <p className="muted strategy-id-text">{strategy.strategy_id}</p>
          <p>{strategy.description || "No description."}</p>
          <dl className="library-meta"><div><dt>Draft</dt><dd>{strategy.draft_status === "not_saved" ? "Not started" : `r${strategy.draft_revision} · ${strategy.draft_status}`}</dd></div><div><dt>Latest version</dt><dd>{strategy.latest_version_number ? `v${strategy.latest_version_number}` : "None"}</dd></div></dl>
          {tab === "TRASHED" && <p className="muted">Deleted {strategy.deleted_at ?? "at an earlier time"}. Versions and research evidence are retained.</p>}
          <div className="action-buttons library-actions">{tab === "ACTIVE" ? <><button className="button button-secondary" type="button" onClick={() => onOpenDraft(strategy.strategy_id)}>Open Draft</button>{strategy.latest_version_id && <><button className="button button-secondary" type="button" onClick={() => onOpenLatest(strategy.strategy_id, strategy.latest_version_id as string)}>Latest Version</button><button className="button button-quiet" type="button" onClick={() => onOpenHistory(strategy.strategy_id, strategy.latest_version_id as string)}>Version History</button><button className="button button-quiet" type="button" onClick={() => onClone(strategy.strategy_id, strategy.latest_version_id as string)}>Clone</button></>}<button className="button button-quiet" type="button" onClick={() => onProtect(strategy.strategy_id, !strategy.is_protected)}>{strategy.is_protected ? "Unprotect" : "Protect"}</button><button className="button button-danger" type="button" onClick={() => onTrash(strategy.strategy_id)}>Move to Trash</button></> : <><button className="button button-secondary" type="button" onClick={() => onRestore(strategy.strategy_id)}>Restore</button><button className="button button-danger" type="button" onClick={() => onPermanentDelete(strategy.strategy_id)}>Delete Permanently</button></>}</div>
        </article>
      ))}</div>}
    </main>
  );
}
