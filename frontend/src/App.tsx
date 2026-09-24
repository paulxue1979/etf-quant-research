import { useEffect, useReducer, useState } from "react";

import { BacktestLab } from "./backtest/BacktestLab";
import { ExperimentResearch } from "./research/ExperimentResearch";
import { OosResearchView } from "./research/OosResearchView";
import { strategyApi, StrategyApiError } from "./strategy/api";
import {
  allocationTotalPercent,
  comparisonOptions,
  createDefaultEditorState,
  editorReducer,
  fromStrategyPayload,
  isAssetReferenced,
  rebalanceOptions,
  toStrategyPayload,
  validateEditorState,
} from "./strategy/editor";
import type {
  EditorAllocation,
  EditorAllocationRule,
  EditorCondition,
  EditorOperand,
  EditorRuleGroup,
  EditorState,
  LogicalOperator,
  OperandType,
  StrategyVersionSummary,
  ValidationIssue,
  ValidationResult,
} from "./strategy/types";

const emptyValidation: ValidationResult = { is_valid: false, errors: [], warnings: [] };

function percent(value: number): string {
  if (!Number.isFinite(value)) return "Invalid";
  return `${value.toFixed(2)}%`;
}

function operandLabel(operand: EditorOperand): string {
  if (operand.type === "price") return `${operand.asset} ${operand.timeframe.toUpperCase()} Close`;
  if (operand.type === "constant") return `Constant ${operand.value || "0"}`;
  return `${operand.asset} ${operand.timeframe.toUpperCase()} ${operand.type.toUpperCase()}${operand.period || "?"}`;
}

function thresholdLabel(value: string): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  return `${numeric >= 0 ? "+" : ""}${numeric}%`;
}

function issueForPath(issues: ValidationIssue[], pathPart: string): ValidationIssue | undefined {
  return issues.find((issue) => issue.path.includes(pathPart));
}

function FieldError({ issue }: { issue?: ValidationIssue }) {
  return issue ? <span className="field-error">{issue.message}</span> : null;
}

function OperandEditor({
  operand,
  assets,
  priceField,
  onChange,
}: {
  operand: EditorOperand;
  assets: string[];
  priceField: EditorState["priceField"];
  onChange: (operand: EditorOperand) => void;
}) {
  const set = (patch: Partial<EditorOperand>) => onChange({ ...operand, ...patch });
  return (
    <div className="operand-editor">
      <select
        aria-label="Operand type"
        value={operand.type}
        onChange={(event) => {
          const type = event.target.value as OperandType;
          set({
            type,
            period: type === "ma" || type === "ema" ? operand.period || "20" : "",
            value: type === "constant" ? operand.value || "0" : "",
          });
        }}
      >
        <option value="price">PRICE</option>
        <option value="ma">MA</option>
        <option value="ema">EMA</option>
        <option value="constant">CONSTANT</option>
      </select>
      {operand.type !== "constant" && (
        <select
          aria-label="Operand asset"
          value={operand.asset}
          onChange={(event) => set({ asset: event.target.value })}
        >
          {assets.map((asset) => (
            <option key={asset} value={asset}>
              {asset}
            </option>
          ))}
        </select>
      )}
      {operand.type !== "constant" && (
        <label className="operand-timeframe">
          Timeframe
          <select
            aria-label="Operand timeframe"
            value={operand.timeframe}
            onChange={(event) => set({ timeframe: event.target.value as EditorOperand["timeframe"] })}
          >
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>
      )}
      {operand.type === "constant" && (
        <input
          aria-label="Constant value"
          inputMode="decimal"
          value={operand.value}
          onChange={(event) => set({ value: event.target.value })}
          placeholder="0"
        />
      )}
      {(operand.type === "ma" || operand.type === "ema") && (
        <input
          aria-label="Indicator period"
          type="number"
          min="1"
          step="1"
          value={operand.period}
          onChange={(event) => set({ period: event.target.value })}
        />
      )}
      {operand.type !== "constant" && (
        <span className="operand-price-field">Price: {priceField.toUpperCase()}</span>
      )}
      <span className="operand-preview">{operandLabel(operand)}</span>
    </div>
  );
}

function ConditionEditor({
  condition,
  state,
  issues,
  onChange,
  onRemove,
}: {
  condition: EditorCondition;
  state: EditorState;
  issues: ValidationIssue[];
  onChange: (condition: EditorCondition) => void;
  onRemove: () => void;
}) {
  const issue = issueForPath(issues, condition.id);
  const update = (patch: Partial<EditorCondition>) => onChange({ ...condition, ...patch });
  return (
    <div className="condition-row">
      <div className="condition-operands">
        <OperandEditor
          operand={condition.left}
          assets={state.assets}
          priceField={state.priceField}
          onChange={(left) => update({ left })}
        />
        <select
          aria-label="Condition operator"
          value={condition.operator}
          onChange={(event) => {
            const operator = event.target.value as EditorCondition["operator"];
            update({ operator, thresholdPercent: operator === "equal" ? "" : condition.thresholdPercent });
          }}
        >
          {comparisonOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <OperandEditor
          operand={condition.right}
          assets={state.assets}
          priceField={state.priceField}
          onChange={(right) => update({ right })}
        />
        <label className="threshold-input">
          Relative Threshold (%)
          <input
            aria-label="Relative threshold percent"
            inputMode="decimal"
            disabled={condition.operator === "equal"}
            value={condition.thresholdPercent}
            onChange={(event) => update({ thresholdPercent: event.target.value })}
            placeholder="+3 / -4 / 0"
          />
        </label>
        <button className="button button-quiet" type="button" onClick={onRemove}>
          Remove
        </button>
      </div>
      <div className="condition-summary">
        <span>{operandLabel(condition.left)}</span>
        <strong>{comparisonOptions.find((option) => option.value === condition.operator)?.label}</strong>
        <span>{operandLabel(condition.right)}</span>
        {condition.thresholdPercent && condition.operator !== "equal" && (
          <em>{thresholdLabel(condition.thresholdPercent)}</em>
        )}
      </div>
      <FieldError issue={issue} />
    </div>
  );
}

function RuleGroupEditor({
  group,
  ruleId,
  state,
  issues,
  dispatch,
  depth = 0,
}: {
  group: EditorRuleGroup;
  ruleId: string;
  state: EditorState;
  issues: ValidationIssue[];
  dispatch: React.Dispatch<Parameters<typeof editorReducer>[1]>;
  depth?: number;
}) {
  return (
    <div className={`rule-group depth-${Math.min(depth, 3)}`}>
      <div className="group-heading">
        <span className="group-kicker">RULE GROUP</span>
        <select
          aria-label="Rule group operator"
          value={group.operator}
          onChange={(event) =>
            dispatch({
              type: "groupOperator",
              ruleId,
              groupId: group.id,
              operator: event.target.value as LogicalOperator,
            })
          }
        >
          <option value="and">ALL / AND</option>
          <option value="or">ANY / OR</option>
        </select>
        <button
          className="button button-quiet"
          type="button"
          onClick={() => dispatch({ type: "addCondition", ruleId, groupId: group.id })}
        >
          + Condition
        </button>
        <button
          className="button button-quiet"
          type="button"
          onClick={() => dispatch({ type: "addGroup", ruleId, groupId: group.id })}
        >
          + Group
        </button>
      </div>
      <div className="group-children">
        {group.children.map((child) =>
          child.type === "condition" ? (
            <ConditionEditor
              key={child.id}
              condition={child}
              state={state}
              issues={issues}
              onChange={(condition) => dispatch({ type: "condition", ruleId, conditionId: child.id, condition })}
              onRemove={() =>
                dispatch({ type: "removeNode", ruleId, parentId: group.id, nodeId: child.id })
              }
            />
          ) : (
            <div className="nested-group" key={child.id}>
              <RuleGroupEditor
                group={child}
                ruleId={ruleId}
                state={state}
                issues={issues}
                dispatch={dispatch}
                depth={depth + 1}
              />
              <button
                className="button button-quiet nested-remove"
                type="button"
                onClick={() => dispatch({ type: "removeNode", ruleId, parentId: group.id, nodeId: child.id })}
              >
                Remove group
              </button>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

function AllocationEditor({
  allocation,
  assets,
  onChange,
  onRemove,
  labelPrefix = "Allocation",
}: {
  allocation: EditorAllocation;
  assets: string[];
  onChange: (allocation: EditorAllocation) => void;
  onRemove: () => void;
  labelPrefix?: string;
}) {
  return (
    <div className="allocation-row">
      <select
        aria-label={`${labelPrefix} asset`}
        value={allocation.symbol}
        onChange={(event) => onChange({ ...allocation, symbol: event.target.value })}
      >
        {assets.map((asset) => (
          <option key={asset} value={asset}>
            {asset}
          </option>
        ))}
      </select>
      <div className="weight-input">
        <input
          aria-label={`${labelPrefix} weight percent`}
          inputMode="decimal"
          value={allocation.targetWeightPercent}
          onChange={(event) => onChange({ ...allocation, targetWeightPercent: event.target.value })}
        />
        <span>%</span>
      </div>
      <button className="button button-quiet" type="button" onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function AllocationRuleEditor({
  rule,
  state,
  issues,
  dispatch,
}: {
  rule: EditorAllocationRule;
  state: EditorState;
  issues: ValidationIssue[];
  dispatch: React.Dispatch<Parameters<typeof editorReducer>[1]>;
}) {
  const total = allocationTotalPercent(rule.allocations);
  return (
    <section className="panel rule-panel">
      <div className="section-header">
        <div>
          <span className="eyebrow">ALLOCATION RULE</span>
          <h3>{rule.name || "Untitled rule"}</h3>
        </div>
        <button className="button button-quiet" type="button" onClick={() => dispatch({ type: "removeRule", ruleId: rule.id })}>
          Delete rule
        </button>
      </div>
      <div className="inline-fields">
        <label>
          Rule name
          <input
            value={rule.name}
            onChange={(event) => dispatch({ type: "rule", ruleId: rule.id, field: "name", value: event.target.value })}
          />
        </label>
        <label>
          Rule ID
          <input
            value={rule.ruleId}
            onChange={(event) => dispatch({ type: "rule", ruleId: rule.id, field: "ruleId", value: event.target.value })}
          />
        </label>
        <label>
          Priority
          <input
            type="number"
            step="1"
            value={rule.priority}
            onChange={(event) => dispatch({ type: "rule", ruleId: rule.id, field: "priority", value: event.target.value })}
          />
        </label>
      </div>
      <RuleGroupEditor group={rule.condition} ruleId={rule.id} state={state} issues={issues} dispatch={dispatch} />
      <div className="allocation-block">
        <div className="subsection-heading">
          <div>
            <span className="eyebrow">TARGET WEIGHTS</span>
            <h4>Explicit allocation</h4>
          </div>
          <strong className={total > 100 ? "invalid-number" : "valid-number"}>{percent(total)}</strong>
        </div>
        {rule.allocations.map((allocation) => (
          <AllocationEditor
            key={allocation.id}
            allocation={allocation}
            assets={state.assets}
            onChange={(next) => dispatch({ type: "allocation", ruleId: rule.id, allocation: next })}
            onRemove={() => dispatch({ type: "removeAllocation", ruleId: rule.id, allocationId: allocation.id })}
          />
        ))}
        <div className="allocation-actions">
          <button className="button button-quiet" type="button" onClick={() => dispatch({ type: "addAllocation", ruleId: rule.id })}>
            + Add allocation
          </button>
          {total > 100 && <span className="inline-error">Explicit allocation exceeds 100%.</span>}
        </div>
        <label className="remaining-select">
          Remaining allocation recipient
          <select
            aria-label="Remaining allocation recipient"
            value={rule.remainingSymbol}
            onChange={(event) => dispatch({ type: "rule", ruleId: rule.id, field: "remainingSymbol", value: event.target.value })}
          >
            <option value="">Cash buffer</option>
            {state.assets.map((asset) => (
              <option key={asset} value={asset}>
                {asset}
              </option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}

function VersionHistory({
  versions,
  loading,
  onLoad,
}: {
  versions: StrategyVersionSummary[];
  loading: boolean;
  onLoad: (versionId: string) => void;
}) {
  return (
    <aside className="history-panel">
      <div className="section-header compact">
        <div>
          <span className="eyebrow">IMMUTABLE SNAPSHOTS</span>
          <h2>Version history</h2>
        </div>
        {loading && <span className="status-dot">Loading</span>}
      </div>
      {versions.length === 0 ? (
        <p className="muted">No saved versions yet.</p>
      ) : (
        <div className="version-list">
          {versions.map((version) => (
            <button className="version-item" type="button" key={version.version_id} onClick={() => onLoad(version.version_id)}>
              <span className="version-number">v{version.version_number}</span>
              <span className="version-meta">
                <strong>{new Date(version.created_at).toLocaleString()}</strong>
                <small>{version.status} · {version.content_hash.slice(0, 10)}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}

function Preview({ state }: { state: EditorState }) {
  const firstRule = state.rules[0];
  const noMatchSummary =
    state.noMatchBehavior === "hold_previous_allocation"
      ? `Hold previous · Cash ${percent(100 - allocationTotalPercent(state.initialAllocations))}`
      : state.fallbackAllocations
          .map((item) => `${item.symbol} ${item.targetWeightPercent}%`)
          .join(" · ");
  return (
    <section className="panel preview-panel">
      <div className="section-header">
        <div>
          <span className="eyebrow">SERIALIZED STRATEGY PREVIEW</span>
          <h2>{state.name || "Untitled strategy"}</h2>
        </div>
        <span className="preview-tag">{state.priceField.toUpperCase()}</span>
      </div>
      <div className="preview-grid">
        <div><span>Assets</span><strong>{state.assets.join(" · ") || "None"}</strong></div>
        <div><span>Rules</span><strong>{state.rules.length}</strong></div>
        <div><span>Rebalance</span><strong>{state.rebalanceFrequency.replaceAll("_", " ")}</strong></div>
        <div><span>No match</span><strong>{noMatchSummary || "Cash 100.00%"}</strong></div>
      </div>
      {firstRule && (
        <div className="preview-rule">
          <span className="eyebrow">RULE 1 · PRIORITY {firstRule.priority}</span>
          <p>{firstRule.name}: {firstRule.allocations.map((item) => `${item.symbol} ${item.targetWeightPercent}%`).join(" · ")}</p>
        </div>
      )}
    </section>
  );
}

export function App() {
  const [view, setView] = useState<"strategy" | "backtest" | "research" | "oos">("strategy");
  const [state, dispatch] = useReducer(editorReducer, undefined, () => {
    return createDefaultEditorState();
  });
  const [validation, setValidation] = useState<ValidationResult>(emptyValidation);
  const [busy, setBusy] = useState<"validate" | "save" | "history" | "load" | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [versions, setVersions] = useState<StrategyVersionSummary[]>([]);
  const [newAsset, setNewAsset] = useState("");

  useEffect(() => {
    void loadVersions(state.strategyId);
    // Load history when the strategy identity changes, not on every editor keystroke.
  }, [state.strategyId]);

  async function loadVersions(strategyId: string) {
    if (!strategyId.trim()) return;
    setBusy("history");
    try {
      setVersions(await strategyApi.listVersions(strategyId));
    } catch {
      setVersions([]);
    } finally {
      setBusy(null);
    }
  }

  async function validateCurrent(): Promise<ValidationResult> {
    setBusy("validate");
    setNotice(null);
    const localErrors = validateEditorState(state);
    if (localErrors.length > 0) {
      const result = { ...emptyValidation, errors: localErrors };
      setValidation(result);
      setNotice({ tone: "error", text: "Fix the highlighted configuration before saving." });
      setBusy(null);
      return result;
    }
    try {
      const result = await strategyApi.validate(toStrategyPayload(state));
      setValidation(result);
      setNotice(result.is_valid ? { tone: "success", text: "Strategy configuration is valid." } : { tone: "error", text: "Fix the highlighted configuration before saving." });
      return result;
    } catch (error) {
      const text = error instanceof StrategyApiError ? error.message : "Strategy validation failed.";
      setNotice({ tone: "error", text });
      return { ...emptyValidation, errors: [{ code: "NetworkError", path: "$", message: text }] };
    } finally {
      setBusy(null);
    }
  }

  async function saveCurrent() {
    const result = await validateCurrent();
    if (!result.is_valid) return;
    setBusy("save");
    try {
      const saved = await strategyApi.saveVersion(toStrategyPayload(state));
      if ("is_valid" in saved) {
        setValidation(saved);
        setNotice({ tone: "error", text: "The backend rejected this version." });
      } else {
        setNotice({ tone: "success", text: `Saved immutable version v${saved.version_number}.` });
        await loadVersions(state.strategyId);
      }
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof StrategyApiError ? error.message : "Version save failed." });
    } finally {
      setBusy(null);
    }
  }

  async function loadVersion(versionId: string) {
    setBusy("load");
    try {
      const version = await strategyApi.getVersion(state.strategyId, versionId);
      dispatch({ type: "replace", state: fromStrategyPayload(version.configuration) });
      setValidation({ is_valid: true, errors: [], warnings: [] });
      setNotice({ tone: "success", text: `Loaded v${version.version_number} as an editable draft.` });
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof StrategyApiError ? error.message : "Version load failed." });
    } finally {
      setBusy(null);
    }
  }

  const addAsset = () => {
    const symbol = newAsset.trim().toUpperCase();
    if (!symbol || state.assets.includes(symbol)) {
      setNotice({ tone: "error", text: "Asset symbol must be non-empty and unique." });
      return;
    }
    dispatch({ type: "assets", assets: [...state.assets, symbol] });
    setNewAsset("");
  };

  const removeAsset = (asset: string) => {
    if (state.assets.length === 1 || isAssetReferenced(state, asset)) {
      setNotice({ tone: "error", text: "Remove references to this asset before deleting it." });
      return;
    }
    dispatch({ type: "assets", assets: state.assets.filter((item) => item !== asset) });
  };

  return (
    view === "backtest" ? <BacktestLab onBack={() => setView("strategy")} /> :
    view === "research" ? <ExperimentResearch onBack={() => setView("strategy")} /> :
    view === "oos" ? <OosResearchView onBack={() => setView("strategy")} /> :
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">ETF QUANT RESEARCH SYSTEM · STRATEGY LAB</span>
          <h1>Strategy Lab</h1>
          <p>Configure, validate and version research strategies.</p>
        </div>
        <div className="topbar-status">
          <button className="button button-secondary" type="button" onClick={() => setView("backtest")}>Open Backtest Lab</button>
          <button className="button button-secondary" type="button" onClick={() => setView("research")}>Open Experiment Research</button>
          <button className="button button-secondary" type="button" onClick={() => setView("oos")}>Open OOS Research View</button>
          <span className={`status-pill ${validation.is_valid ? "is-valid" : "is-draft"}`}>
            {validation.is_valid ? "Validated" : "Draft"}
          </span>
          <span className="status-pill">{state.priceField.toUpperCase()}</span>
        </div>
      </header>
      {state.strategyMode === "regime_state_machine" && (
        <div className="strategy-mode-notice" role="status">
          <strong>REGIME STATE MACHINE</strong>
          <span>This version is preserved read-only in Strategy Lab. Regimes, transitions, and value zones are kept in the source payload for PHASE 12E.</span>
        </div>
      )}
      <div className="workspace">
        <div className="main-column">
          <section className="panel basic-panel">
            <div className="section-header">
              <div>
                <span className="eyebrow">STRATEGY DEFINITION</span>
                <h2>Configuration</h2>
              </div>
              <span className="draft-label">New version on save</span>
            </div>
            <div className="basic-grid">
              <label>
                Strategy ID
                <input value={state.strategyId} onChange={(event) => dispatch({ type: "basic", field: "strategyId", value: event.target.value })} />
              </label>
              <label>
                Strategy name
                <input value={state.name} onChange={(event) => dispatch({ type: "basic", field: "name", value: event.target.value })} />
              </label>
              <label className="wide-field">
                Description
                <textarea rows={2} value={state.description} onChange={(event) => dispatch({ type: "basic", field: "description", value: event.target.value })} />
              </label>
              <label>
                Price field
                <select value={state.priceField} onChange={(event) => dispatch({ type: "priceField", value: event.target.value as EditorState["priceField"] })}>
                  <option value="adjusted_close">ADJUSTED_CLOSE</option>
                  <option value="raw_close">RAW_CLOSE</option>
                </select>
              </label>
            </div>
          </section>

          <section className="panel">
            <div className="section-header">
              <div>
                <span className="eyebrow">ASSET UNIVERSE</span>
                <h2>Research assets</h2>
              </div>
              <span className="muted">{state.assets.length} configured</span>
            </div>
            <div className="asset-list">
              {state.assets.map((asset) => (
                <div className="asset-chip" key={asset}>
                  <span className="asset-mark">{asset.slice(0, 1)}</span>
                  <strong>{asset}</strong>
                  <button type="button" aria-label={`Remove ${asset}`} onClick={() => removeAsset(asset)}>×</button>
                </div>
              ))}
            </div>
            <div className="add-row">
              <input aria-label="New asset symbol" value={newAsset} onChange={(event) => setNewAsset(event.target.value)} placeholder="Add symbol, e.g. SPY" />
              <button className="button button-secondary" type="button" onClick={addAsset}>Add asset</button>
            </div>
          </section>

          <div className="section-title-row">
            <div>
              <span className="eyebrow">DECISION TREE</span>
              <h2>Allocation rules</h2>
            </div>
            <button className="button button-secondary" type="button" onClick={() => dispatch({ type: "addRule" })}>+ Add rule</button>
          </div>
          {state.rules.map((rule) => (
            <AllocationRuleEditor key={rule.id} rule={rule} state={state} issues={validation.errors} dispatch={dispatch} />
          ))}

          <section className="panel no-match-panel">
            <div className="section-header">
              <div>
                <span className="eyebrow">NO MATCH</span>
                <h2>No match behavior</h2>
              </div>
              <span className="muted">Choose the strategy target when no rule matches</span>
            </div>
            <fieldset className="behavior-options">
              <legend className="sr-only">No Match Behavior</legend>
              <label className="behavior-option">
                <input
                  aria-label="Use Fallback Allocation"
                  type="radio"
                  name="no-match-behavior"
                  value="use_fallback"
                  checked={state.noMatchBehavior === "use_fallback"}
                  onChange={() => dispatch({ type: "noMatchBehavior", value: "use_fallback" })}
                />
                <span><strong>Use Fallback Allocation</strong><small>Resolve a fixed fallback target.</small></span>
              </label>
              <label className="behavior-option">
                <input
                  aria-label="Hold Previous Allocation"
                  type="radio"
                  name="no-match-behavior"
                  value="hold_previous_allocation"
                  checked={state.noMatchBehavior === "hold_previous_allocation"}
                  onChange={() => dispatch({ type: "noMatchBehavior", value: "hold_previous_allocation" })}
                />
                <span><strong>Hold Previous Allocation</strong><small>Continue using the most recently resolved target allocation.</small></span>
              </label>
            </fieldset>

            {state.noMatchBehavior === "use_fallback" ? (
              <div className="allocation-block no-match-allocation">
                <div className="subsection-heading">
                  <div><span className="eyebrow">FIXED TARGET</span><h3>Fallback allocation</h3></div>
                  <strong className={allocationTotalPercent(state.fallbackAllocations) > 100 ? "invalid-number" : "valid-number"}>
                    {percent(allocationTotalPercent(state.fallbackAllocations))}
                  </strong>
                </div>
                {state.fallbackAllocations.map((allocation) => (
                  <AllocationEditor
                    key={allocation.id}
                    allocation={allocation}
                    assets={state.assets}
                    onChange={(next) => dispatch({ type: "fallbackAllocation", allocation: next })}
                    onRemove={() => dispatch({ type: "removeFallbackAllocation", allocationId: allocation.id })}
                  />
                ))}
                <button className="button button-quiet" type="button" onClick={() => dispatch({ type: "addFallbackAllocation" })}>+ Add fallback allocation</button>
              </div>
            ) : (
              <div className="allocation-block no-match-allocation">
                <div className="strategy-semantics-note">
                  <strong>Hold Previous</strong>
                  <span>If no rule matches, the strategy keeps its most recently resolved target. You do not re-enter that allocation.</span>
                </div>
                <div className="subsection-heading">
                  <div><span className="eyebrow">SEED TARGET</span><h3>Starting Allocation</h3></div>
                  <strong className={allocationTotalPercent(state.initialAllocations) > 100 ? "invalid-number" : "valid-number"}>
                    Cash {percent(100 - allocationTotalPercent(state.initialAllocations))}
                  </strong>
                </div>
                <p className="help-text">Used only before the strategy has resolved its first target. It is not reapplied on every Hold Previous decision.</p>
                {state.initialAllocations.map((allocation) => (
                  <AllocationEditor
                    key={allocation.id}
                    allocation={allocation}
                    assets={state.assets}
                    labelPrefix="Starting allocation"
                    onChange={(next) => dispatch({ type: "initialAllocation", allocation: next })}
                    onRemove={() => dispatch({ type: "removeInitialAllocation", allocationId: allocation.id })}
                  />
                ))}
                <button className="button button-quiet" type="button" onClick={() => dispatch({ type: "addInitialAllocation" })}>+ Add starting allocation</button>
                {allocationTotalPercent(state.initialAllocations) > 100 && <span className="inline-error">Starting allocation exceeds 100%.</span>}
              </div>
            )}
          </section>

          <section className="panel rebalance-panel">
            <div className="section-header">
              <div>
                <span className="eyebrow">EXECUTION POLICY</span>
                <h2>Rebalance</h2>
              </div>
              <span className="muted">Stored with this strategy version</span>
            </div>
            <div className="inline-fields">
              <label>
                Frequency
                <select value={state.rebalanceFrequency} onChange={(event) => dispatch({ type: "rebalance", field: "frequency", value: event.target.value })}>
                  {rebalanceOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label>
                Absolute threshold %
                <input inputMode="decimal" value={state.rebalanceThresholdPercent} onChange={(event) => dispatch({ type: "rebalance", field: "threshold", value: event.target.value })} placeholder="5" />
              </label>
            </div>
          </section>

          <Preview state={state} />

          <section className="action-panel">
            <div>
              <span className="eyebrow">VERSIONED WORKFLOW</span>
              <p>{notice?.text ?? "Validation is required before an immutable version can be saved."}</p>
              {validation.errors.length > 0 && (
                <ul className="validation-errors">
                  {validation.errors.map((issue, index) => <li key={`${issue.code}-${issue.path}-${index}`}>{issue.message}</li>)}
                </ul>
              )}
            </div>
            <div className="action-buttons">
              <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void validateCurrent()}>
                {busy === "validate" ? "Validating..." : "Validate strategy"}
              </button>
              <button className="button button-primary" type="button" disabled={busy !== null} onClick={() => void saveCurrent()}>
                {busy === "save" ? "Saving..." : "Save new version"}
              </button>
            </div>
          </section>
        </div>
        <VersionHistory versions={versions} loading={busy === "history" || busy === "load"} onLoad={(id) => void loadVersion(id)} />
      </div>
    </main>
  );
}
