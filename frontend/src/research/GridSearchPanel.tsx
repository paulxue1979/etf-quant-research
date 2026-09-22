import { useMemo, useState } from "react";

import { researchApi, ResearchApiError } from "./api";
import type { GridSearchPreflight, GridSearchProgress, GridSearchTemplate } from "./types";

interface GridSearchPanelProps {
  experimentId: string;
}

type GridAction = "template" | "preflight" | "prepare" | "execute" | "cancel" | "progress";

function errorMessage(reason: unknown): string {
  return reason instanceof ResearchApiError
    ? `${reason.code}: ${reason.message}`
    : "Grid search request failed.";
}

function CountCell({ label, value }: { label: string; value: number }) {
  return <div><span>{label}</span><strong>{value.toLocaleString()}</strong></div>;
}

export function GridSearchPanel({ experimentId }: GridSearchPanelProps) {
  const [template, setTemplate] = useState<GridSearchTemplate | null>(null);
  const [definitionJson, setDefinitionJson] = useState("");
  const [preflight, setPreflight] = useState<GridSearchPreflight | null>(null);
  const [preflightSource, setPreflightSource] = useState("");
  const [progress, setProgress] = useState<GridSearchProgress | null>(null);
  const [prepared, setPrepared] = useState(false);
  const [busy, setBusy] = useState<GridAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentExperiment = experimentId.trim();
  const definitionIsCurrent = Boolean(preflight && preflightSource === definitionJson);
  const canPrepare = Boolean(preflight?.can_execute && definitionIsCurrent && !prepared);
  const canExecute = Boolean(
    prepared
      && progress?.status !== "running"
      && progress?.status !== "completed"
      && progress?.status !== "cancelled",
  );
  const canCancel = Boolean(
    prepared && progress?.status !== "completed" && progress?.status !== "cancelled",
  );
  const status = progress?.status ?? (prepared ? "prepared" : preflight?.status ?? "draft");
  const tunableNames = useMemo(
    () => template?.tunable_parameters.map(
      (item) => String(item.name ?? item.parameter_id ?? "unnamed"),
    ) ?? [],
    [template],
  );

  function parseDefinition(): Record<string, unknown> | null {
    try {
      const parsed: unknown = JSON.parse(definitionJson);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("definition must be an object");
      }
      return parsed as Record<string, unknown>;
    } catch {
      setError("INVALID_GRID_DEFINITION: Definition must be valid JSON object data.");
      return null;
    }
  }

  async function loadTemplate() {
    if (!currentExperiment) {
      setError("Enter an experiment ID before loading a grid definition.");
      return;
    }
    setBusy("template");
    setError(null);
    try {
      const response = await researchApi.getGridTemplate(currentExperiment);
      setTemplate(response);
      setDefinitionJson(JSON.stringify(response.definition, null, 2));
      setPreflight(null);
      setPreflightSource("");
      setProgress(null);
      setPrepared(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  async function runPreflight() {
    const definition = parseDefinition();
    if (!definition) return;
    setBusy("preflight");
    setError(null);
    try {
      const response = await researchApi.preflightGrid(currentExperiment, definition);
      setPreflight(response);
      setPreflightSource(definitionJson);
      setPrepared(false);
      setProgress(null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  async function prepare() {
    const definition = parseDefinition();
    if (!definition || !canPrepare) return;
    setBusy("prepare");
    setError(null);
    try {
      const response = await researchApi.prepareGrid(currentExperiment, definition);
      setPreflight(response);
      setPreflightSource(definitionJson);
      setPrepared(response.can_execute);
      if (response.can_execute) {
        setProgress(await researchApi.getGridProgress(currentExperiment));
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  async function execute() {
    if (!canExecute) return;
    setBusy("execute");
    setError(null);
    try {
      setProgress(await researchApi.executeGrid(currentExperiment));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (!canCancel) return;
    setBusy("cancel");
    setError(null);
    try {
      setProgress(await researchApi.cancelGrid(currentExperiment));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  async function refreshProgress() {
    if (!prepared) return;
    setBusy("progress");
    setError(null);
    try {
      setProgress(await researchApi.getGridProgress(currentExperiment));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel grid-search-panel">
      <div className="section-header compact">
        <div>
          <span className="eyebrow">CONTROLLED IS GRID SEARCH</span>
          <h2>Deterministic candidate execution</h2>
        </div>
        <span className={`status-pill ${status === "failed" ? "is-error" : status === "completed" ? "is-valid" : "is-draft"}`}>
          {status.toUpperCase()}
        </span>
      </div>
      <div className="grid-search-toolbar">
        <button className="button button-secondary" type="button" disabled={Boolean(busy) || !currentExperiment} onClick={() => void loadTemplate()}>
          {busy === "template" ? "Loading..." : "Load frozen definition"}
        </button>
        <span className="muted">{currentExperiment || "No experiment selected"}</span>
      </div>
      {template && <>
        <div className="grid-search-scope">
          <div><span>Fixed parameters</span><strong>{Object.keys(template.fixed_parameters).length}</strong></div>
          <div><span>Tunable parameters</span><strong>{tunableNames.length}</strong></div>
          <div className="grid-search-tunables"><span>Parameter IDs</span><strong>{tunableNames.join(", ") || "None"}</strong></div>
        </div>
        <label className="grid-definition-field">
          Grid definition
          <textarea aria-label="Grid definition" spellCheck={false} value={definitionJson} onChange={(event) => { setDefinitionJson(event.target.value); setPrepared(false); }} />
        </label>
        <div className="action-buttons grid-search-actions">
          <button className="button button-secondary" type="button" disabled={Boolean(busy)} onClick={() => void runPreflight()}>{busy === "preflight" ? "Checking..." : "Run preflight"}</button>
          <button className="button button-primary" type="button" disabled={Boolean(busy) || !canPrepare} onClick={() => void prepare()}>{busy === "prepare" ? "Preparing..." : "Prepare candidate set"}</button>
          <button className="button button-primary" type="button" disabled={Boolean(busy) || !canExecute} onClick={() => void execute()}>{busy === "execute" ? "Executing..." : "Execute IS grid"}</button>
          <button className="button button-quiet" type="button" disabled={Boolean(busy) || !canCancel} onClick={() => void cancel()}>{busy === "cancel" ? "Cancelling..." : "Cancel"}</button>
          <button className="button button-quiet" type="button" disabled={Boolean(busy) || !prepared} onClick={() => void refreshProgress()}>{busy === "progress" ? "Refreshing..." : "Refresh progress"}</button>
        </div>
      </>}
      {preflight && definitionIsCurrent && <div className="grid-counts" aria-label="Grid preflight counts">
        <CountCell label="Theoretical" value={preflight.theoretical_count} />
        <CountCell label="Pruned" value={preflight.pruned_count} />
        <CountCell label="Valid" value={preflight.valid_count} />
        <CountCell label="Duplicates" value={preflight.duplicate_count} />
        <CountCell label="Executable" value={preflight.executable_count} />
        <CountCell label="Allowed" value={preflight.max_allowed} />
      </div>}
      {preflight && definitionIsCurrent && !preflight.can_execute && <div className="research-error" role="alert">{preflight.issues.join(", ") || "Grid preflight failed."}</div>}
      {progress && <div className="grid-progress" aria-label="Grid execution progress">
        <div className="grid-progress-heading"><strong>{progress.progress_percentage.toFixed(1)}%</strong><span>{progress.completed} completed · {progress.failed} failed ({progress.retryable} retryable) · {progress.pending} pending · {progress.running} running</span></div>
        <progress max={100} value={progress.progress_percentage} />
      </div>}
      {error && <div className="research-error" role="alert">{error}</div>}
    </section>
  );
}
