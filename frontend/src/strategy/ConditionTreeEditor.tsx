import type {
  EditorCondition,
  EditorOperand,
  EditorRuleGroup,
  PriceField,
  ValidationIssue,
} from "./types";
import { comparisonOptions, createCondition, createRuleGroup } from "./editor";

export function operandLabel(operand: EditorOperand): string {
  if (operand.type === "price") return `${operand.asset} ${operand.timeframe.toUpperCase()} Close`;
  if (operand.type === "constant") return `Constant ${operand.value || "0"}`;
  return `${operand.asset} ${operand.timeframe.toUpperCase()} ${operand.type.toUpperCase()}${operand.period || "?"}`;
}

export function OperandEditor({
  operand,
  assets,
  priceField,
  onChange,
}: {
  operand: EditorOperand;
  assets: string[];
  priceField: PriceField;
  onChange: (operand: EditorOperand) => void;
}) {
  const set = (patch: Partial<EditorOperand>) => onChange({ ...operand, ...patch });
  return (
    <div className="operand-editor">
      <select
        aria-label="Operand type"
        value={operand.type}
        onChange={(event) => {
          const type = event.target.value as EditorOperand["type"];
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
        <select aria-label="Operand asset" value={operand.asset} onChange={(event) => set({ asset: event.target.value })}>
          {assets.map((asset) => <option key={asset} value={asset}>{asset}</option>)}
        </select>
      )}
      {operand.type !== "constant" && (
        <label className="operand-timeframe">
          Timeframe
          <select aria-label="Operand timeframe" value={operand.timeframe} onChange={(event) => set({ timeframe: event.target.value as EditorOperand["timeframe"] })}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>
      )}
      {operand.type === "constant" && <input aria-label="Constant value" inputMode="decimal" value={operand.value} onChange={(event) => set({ value: event.target.value })} placeholder="0" />}
      {(operand.type === "ma" || operand.type === "ema") && <input aria-label="Indicator period" type="number" min="1" step="1" value={operand.period} onChange={(event) => set({ period: event.target.value })} />}
      {operand.type !== "constant" && <span className="operand-price-field">Price: {priceField.toUpperCase()}</span>}
      <span className="operand-preview">{operandLabel(operand)}</span>
    </div>
  );
}

function updateGroup(group: EditorRuleGroup, groupId: string, update: (group: EditorRuleGroup) => EditorRuleGroup): EditorRuleGroup {
  if (group.id === groupId) return update(group);
  return { ...group, children: group.children.map((child) => child.type === "group" ? updateGroup(child, groupId, update) : child) };
}

function updateCondition(group: EditorRuleGroup, conditionId: string, update: (condition: EditorCondition) => EditorCondition): EditorRuleGroup {
  return {
    ...group,
    children: group.children.map((child) => child.type === "group"
      ? updateCondition(child, conditionId, update)
      : child.id === conditionId ? update(child) : child),
  };
}

function removeNode(group: EditorRuleGroup, parentId: string, nodeId: string): EditorRuleGroup {
  if (group.id === parentId) return { ...group, children: group.children.filter((child) => child.id !== nodeId) };
  return { ...group, children: group.children.map((child) => child.type === "group" ? removeNode(child, parentId, nodeId) : child) };
}

function ConditionRow({
  condition,
  assets,
  priceField,
  issues,
  onChange,
  onRemove,
}: {
  condition: EditorCondition;
  assets: string[];
  priceField: PriceField;
  issues: ValidationIssue[];
  onChange: (condition: EditorCondition) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<EditorCondition>) => onChange({ ...condition, ...patch });
  const issue = issues.find((item) => item.path.includes(condition.id));
  return (
    <div className="condition-row regime-condition-row">
      <div className="condition-operands">
        <OperandEditor operand={condition.left} assets={assets} priceField={priceField} onChange={(left) => set({ left })} />
        <select aria-label="Condition operator" value={condition.operator} onChange={(event) => set({ operator: event.target.value as EditorCondition["operator"], thresholdPercent: event.target.value === "equal" ? "" : condition.thresholdPercent })}>
          {comparisonOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <OperandEditor operand={condition.right} assets={assets} priceField={priceField} onChange={(right) => set({ right })} />
        <label className="threshold-input">
          Relative Threshold (%)
          <input aria-label="Relative threshold percent" inputMode="decimal" disabled={condition.operator === "equal"} value={condition.thresholdPercent} onChange={(event) => set({ thresholdPercent: event.target.value })} placeholder="+3 / -4 / 0" />
        </label>
        <button className="button button-quiet" type="button" onClick={onRemove}>Remove</button>
      </div>
      <div className="condition-summary"><span>{operandLabel(condition.left)}</span><strong>{comparisonOptions.find((option) => option.value === condition.operator)?.label}</strong><span>{operandLabel(condition.right)}</span>{condition.thresholdPercent && condition.operator !== "equal" && <em>{Number(condition.thresholdPercent) >= 0 ? `+${condition.thresholdPercent}%` : `${condition.thresholdPercent}%`}</em>}</div>
      {issue && <span className="field-error">{issue.message}</span>}
    </div>
  );
}

export function ConditionTreeEditor({
  group,
  assets,
  priceField,
  issues,
  onChange,
}: {
  group: EditorRuleGroup;
  assets: string[];
  priceField: PriceField;
  issues: ValidationIssue[];
  onChange: (group: EditorRuleGroup) => void;
}) {
  return (
    <div className="rule-group regime-condition-tree">
      <div className="group-heading">
        <span className="group-kicker">CONDITION GROUP</span>
        <select aria-label="Transition group operator" value={group.operator} onChange={(event) => onChange({ ...group, operator: event.target.value as EditorRuleGroup["operator"] })}>
          <option value="and">ALL / AND</option>
          <option value="or">ANY / OR</option>
        </select>
        <button className="button button-quiet" type="button" onClick={() => onChange(updateGroup(group, group.id, (current) => ({ ...current, children: [...current.children, createCondition(assets)] })))}>+ Condition</button>
        <button className="button button-quiet" type="button" onClick={() => onChange(updateGroup(group, group.id, (current) => ({ ...current, children: [...current.children, createRuleGroup(assets, "or")] })))}>+ Group</button>
      </div>
      <div className="group-children">
        {group.children.map((child) => child.type === "condition" ? (
          <ConditionRow key={child.id} condition={child} assets={assets} priceField={priceField} issues={issues} onChange={(condition) => onChange(updateCondition(group, child.id, () => condition))} onRemove={() => onChange(removeNode(group, group.id, child.id))} />
        ) : (
          <div className="nested-group" key={child.id}>
            <ConditionTreeEditor group={child} assets={assets} priceField={priceField} issues={issues} onChange={(next) => onChange(updateGroup(group, child.id, () => next))} />
            <button className="button button-quiet nested-remove" type="button" onClick={() => onChange(removeNode(group, group.id, child.id))}>Remove group</button>
          </div>
        ))}
      </div>
    </div>
  );
}

export function conditionSummary(group: EditorRuleGroup): string {
  const condition = group.children.find((child): child is EditorCondition => child.type === "condition");
  if (!condition) return `${group.operator.toUpperCase()} condition group`;
  const operator = comparisonOptions.find((item) => item.value === condition.operator)?.label ?? condition.operator;
  const threshold = condition.thresholdPercent && condition.operator !== "equal" ? ` ${Number(condition.thresholdPercent) >= 0 ? "+" : ""}${condition.thresholdPercent}%` : "";
  return `${operandLabel(condition.left)} ${operator} ${operandLabel(condition.right)}${threshold}`;
}
