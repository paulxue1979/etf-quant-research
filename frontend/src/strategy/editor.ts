import type {
  ComparisonOperator,
  EditorAllocation,
  EditorAllocationRule,
  EditorCondition,
  EditorOperand,
  EditorRuleGroup,
  EditorRuleNode,
  EditorState,
  LogicalOperator,
  OperandType,
  PriceField,
  RebalanceFrequency,
  StrategyAllocationPayload,
  StrategyConditionPayload,
  StrategyOperandPayload,
  StrategyPayload,
  StrategyRuleGroupPayload,
} from "./types";

let identifier = 0;

function nextId(prefix: string): string {
  identifier += 1;
  return `${prefix}-${identifier}`;
}

function firstAsset(assets: string[]): string {
  return assets[0] ?? "QQQ";
}

export function createOperand(
  assets: string[],
  type: OperandType = "price",
): EditorOperand {
  return {
    id: nextId("operand"),
    type,
    asset: firstAsset(assets),
    period: type === "ma" || type === "ema" ? "20" : "",
    value: type === "constant" ? "0" : "",
  };
}

export function createCondition(assets: string[]): EditorCondition {
  return {
    id: nextId("condition"),
    type: "condition",
    left: createOperand(assets, "price"),
    operator: "greater_than",
    right: { ...createOperand(assets, "ma"), period: "20" },
    thresholdPercent: "",
  };
}

export function createRuleGroup(
  assets: string[],
  operator: LogicalOperator = "and",
): EditorRuleGroup {
  return {
    id: nextId("group"),
    type: "group",
    operator,
    children: [createCondition(assets)],
  };
}

function createAllocation(assets: string[], weight = "0"): EditorAllocation {
  return {
    id: nextId("allocation"),
    symbol: firstAsset(assets),
    targetWeightPercent: weight,
  };
}

export function createAllocationRule(assets: string[], index: number): EditorAllocationRule {
  return {
    id: nextId("rule"),
    ruleId: `rule-${index}`,
    name: `Rule ${index}`,
    priority: String(index),
    condition: createRuleGroup(assets),
    allocations: [createAllocation(assets)],
    remainingSymbol: "",
  };
}

export function createDefaultEditorState(): EditorState {
  const assets = ["QQQ", "TQQQ", "SGOV"];
  const firstCondition = createCondition(assets);
  firstCondition.thresholdPercent = "4";
  const secondCondition: EditorCondition = {
    ...createCondition(assets),
    left: { ...createOperand(assets, "ma"), period: "20" },
    right: { ...createOperand(assets, "ma"), period: "50" },
  };
  const rule: EditorAllocationRule = {
    id: nextId("rule"),
    ruleId: "risk-on",
    name: "Risk On",
    priority: "1",
    condition: {
      id: nextId("group"),
      type: "group",
      operator: "and",
      children: [firstCondition, secondCondition],
    },
    allocations: [
      { id: nextId("allocation"), symbol: "QQQ", targetWeightPercent: "60" },
      { id: nextId("allocation"), symbol: "TQQQ", targetWeightPercent: "30" },
      { id: nextId("allocation"), symbol: "SGOV", targetWeightPercent: "10" },
    ],
    remainingSymbol: "",
  };
  return {
    strategyId: "qqq-tqqq-sgov-dynamic-allocation",
    name: "QQQ / TQQQ / SGOV Dynamic Allocation",
    description: "Trend-following allocation with an SGOV defensive fallback.",
    priceField: "adjusted_close",
    assets,
    rules: [rule],
    fallbackAllocations: [
      { id: nextId("allocation"), symbol: "SGOV", targetWeightPercent: "100" },
    ],
    rebalanceFrequency: "weekly",
    rebalanceThresholdPercent: "5",
  };
}

function updateGroup(
  group: EditorRuleGroup,
  groupId: string,
  update: (current: EditorRuleGroup) => EditorRuleGroup,
): EditorRuleGroup {
  if (group.id === groupId) {
    return update(group);
  }
  return {
    ...group,
    children: group.children.map((child) =>
      child.type === "group" ? updateGroup(child, groupId, update) : child,
    ),
  };
}

function updateCondition(
  group: EditorRuleGroup,
  conditionId: string,
  update: (current: EditorCondition) => EditorCondition,
): EditorRuleGroup {
  return {
    ...group,
    children: group.children.map((child) => {
      if (child.type === "condition") {
        return child.id === conditionId ? update(child) : child;
      }
      return updateCondition(child, conditionId, update);
    }),
  };
}

function removeChild(group: EditorRuleGroup, parentId: string, childId: string): EditorRuleGroup {
  if (group.id === parentId) {
    return { ...group, children: group.children.filter((child) => child.id !== childId) };
  }
  return {
    ...group,
    children: group.children.map((child) =>
      child.type === "group" ? removeChild(child, parentId, childId) : child,
    ),
  };
}

function updateRule(
  state: EditorState,
  ruleId: string,
  update: (current: EditorAllocationRule) => EditorAllocationRule,
): EditorState {
  return { ...state, rules: state.rules.map((rule) => (rule.id === ruleId ? update(rule) : rule)) };
}

export type EditorAction =
  | { type: "basic"; field: "strategyId" | "name" | "description"; value: string }
  | { type: "priceField"; value: PriceField }
  | { type: "assets"; assets: string[] }
  | { type: "addRule" }
  | { type: "removeRule"; ruleId: string }
  | { type: "rule"; ruleId: string; field: "ruleId" | "name" | "priority" | "remainingSymbol"; value: string }
  | { type: "groupOperator"; ruleId: string; groupId: string; operator: LogicalOperator }
  | { type: "addCondition"; ruleId: string; groupId: string }
  | { type: "addGroup"; ruleId: string; groupId: string }
  | { type: "removeNode"; ruleId: string; parentId: string; nodeId: string }
  | { type: "condition"; ruleId: string; conditionId: string; condition: EditorCondition }
  | { type: "allocation"; ruleId: string; allocation: EditorAllocation }
  | { type: "addAllocation"; ruleId: string }
  | { type: "removeAllocation"; ruleId: string; allocationId: string }
  | { type: "fallbackAllocation"; allocation: EditorAllocation }
  | { type: "addFallbackAllocation" }
  | { type: "removeFallbackAllocation"; allocationId: string }
  | { type: "rebalance"; field: "frequency" | "threshold"; value: string }
  | { type: "replace"; state: EditorState };

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "basic":
      return { ...state, [action.field]: action.value };
    case "priceField":
      return { ...state, priceField: action.value };
    case "assets":
      return { ...state, assets: action.assets };
    case "addRule":
      return { ...state, rules: [...state.rules, createAllocationRule(state.assets, state.rules.length + 1)] };
    case "removeRule":
      return { ...state, rules: state.rules.filter((rule) => rule.id !== action.ruleId) };
    case "rule":
      return updateRule(state, action.ruleId, (rule) => ({ ...rule, [action.field]: action.value }));
    case "groupOperator":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        condition: updateGroup(rule.condition, action.groupId, (group) => ({
          ...group,
          operator: action.operator,
        })),
      }));
    case "addCondition":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        condition: updateGroup(rule.condition, action.groupId, (group) => ({
          ...group,
          children: [...group.children, createCondition(state.assets)],
        })),
      }));
    case "addGroup":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        condition: updateGroup(rule.condition, action.groupId, (group) => ({
          ...group,
          children: [...group.children, createRuleGroup(state.assets, "or")],
        })),
      }));
    case "removeNode":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        condition: removeChild(rule.condition, action.parentId, action.nodeId),
      }));
    case "condition":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        condition: updateCondition(rule.condition, action.conditionId, () => action.condition),
      }));
    case "allocation":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        allocations: rule.allocations.map((allocation) =>
          allocation.id === action.allocation.id ? action.allocation : allocation,
        ),
      }));
    case "addAllocation":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        allocations: [...rule.allocations, createAllocation(state.assets)],
      }));
    case "removeAllocation":
      return updateRule(state, action.ruleId, (rule) => ({
        ...rule,
        allocations: rule.allocations.filter((allocation) => allocation.id !== action.allocationId),
      }));
    case "fallbackAllocation":
      return {
        ...state,
        fallbackAllocations: state.fallbackAllocations.map((allocation) =>
          allocation.id === action.allocation.id ? action.allocation : allocation,
        ),
      };
    case "addFallbackAllocation":
      return {
        ...state,
        fallbackAllocations: [...state.fallbackAllocations, createAllocation(state.assets)],
      };
    case "removeFallbackAllocation":
      return {
        ...state,
        fallbackAllocations: state.fallbackAllocations.filter(
          (allocation) => allocation.id !== action.allocationId,
        ),
      };
    case "rebalance":
      return action.field === "frequency"
        ? { ...state, rebalanceFrequency: action.value as RebalanceFrequency }
        : { ...state, rebalanceThresholdPercent: action.value };
    case "replace":
      return action.state;
  }
}

function toNumber(value: string): number {
  return Number(value);
}

function toWeightPayload(allocation: EditorAllocation): StrategyAllocationPayload {
  return { symbol: allocation.symbol, target_weight: toNumber(allocation.targetWeightPercent) / 100 };
}

function toOperandPayload(operand: EditorOperand, priceField: PriceField): StrategyOperandPayload {
  if (operand.type === "constant") {
    return { type: "constant", asset: operand.asset, value: toNumber(operand.value) };
  }
  if (operand.type === "price") {
    return { type: "price", asset: operand.asset, price_field: priceField };
  }
  return {
    type: operand.type,
    asset: operand.asset,
    period: toNumber(operand.period),
    price_field: priceField,
  };
}

function toConditionPayload(
  condition: EditorCondition,
  priceField: PriceField,
): StrategyConditionPayload {
  const threshold =
    condition.operator === "equal" || condition.thresholdPercent.trim() === ""
      ? null
      : { type: "relative" as const, value: toNumber(condition.thresholdPercent) / 100 };
  return {
    type: "condition",
    left: toOperandPayload(condition.left, priceField),
    operator: condition.operator,
    right: toOperandPayload(condition.right, priceField),
    threshold,
  };
}

function toRuleGroupPayload(group: EditorRuleGroup, priceField: PriceField): StrategyRuleGroupPayload {
  return {
    type: "group",
    operator: group.operator,
    children: group.children.map((child) =>
      child.type === "condition"
        ? toConditionPayload(child, priceField)
        : toRuleGroupPayload(child, priceField),
    ),
  };
}

export function toStrategyPayload(state: EditorState): StrategyPayload {
  return {
    strategy_id: state.strategyId.trim(),
    name: state.name,
    description: state.description,
    assets: state.assets.map((symbol) => ({ symbol })),
    price_field: state.priceField,
    rules: state.rules.map((rule) => ({
      rule_id: rule.ruleId,
      name: rule.name,
      priority: toNumber(rule.priority),
      condition: toRuleGroupPayload(rule.condition, state.priceField),
      allocations: rule.allocations.map(toWeightPayload),
      remaining: rule.remainingSymbol ? { symbol: rule.remainingSymbol } : null,
    })),
    fallback: { name: "fallback", allocations: state.fallbackAllocations.map(toWeightPayload) },
    rebalance_policy: {
      frequency: state.rebalanceFrequency,
      threshold:
        state.rebalanceThresholdPercent.trim() === ""
          ? null
          : toNumber(state.rebalanceThresholdPercent) / 100,
    },
  };
}

function fromOperandPayload(payload: StrategyOperandPayload): EditorOperand {
  return {
    id: nextId("operand"),
    type: payload.type,
    asset: payload.asset,
    period: payload.period === undefined ? "" : String(payload.period),
    value: payload.value === undefined ? "" : String(payload.value),
  };
}

function fromRuleNode(
  node: StrategyConditionPayload | StrategyRuleGroupPayload,
): EditorRuleNode {
  if (node.type === "condition") {
    return {
      id: nextId("condition"),
      type: "condition",
      left: fromOperandPayload(node.left),
      operator: node.operator,
      right: fromOperandPayload(node.right),
      thresholdPercent: node.threshold ? String(node.threshold.value * 100) : "",
    };
  }
  return {
    id: nextId("group"),
    type: "group",
    operator: node.operator,
    children: node.children.map(fromRuleNode),
  };
}

function fromAllocationPayload(payload: StrategyAllocationPayload): EditorAllocation {
  return {
    id: nextId("allocation"),
    symbol: payload.symbol,
    targetWeightPercent: String(payload.target_weight * 100),
  };
}

export function fromStrategyPayload(payload: StrategyPayload): EditorState {
  return {
    strategyId: payload.strategy_id,
    name: payload.name,
    description: payload.description,
    priceField: payload.price_field,
    assets: payload.assets.map((asset) => asset.symbol),
    rules: payload.rules.map((rule) => {
      const condition = fromRuleNode(rule.condition);
      if (condition.type !== "group") {
        throw new Error("Strategy Lab expects allocation-rule conditions to be rule groups");
      }
      return {
        id: nextId("rule"),
        ruleId: rule.rule_id,
        name: rule.name,
        priority: String(rule.priority),
        condition,
        allocations: rule.allocations.map(fromAllocationPayload),
        remainingSymbol: rule.remaining?.symbol ?? "",
      };
    }),
    fallbackAllocations: payload.fallback.allocations.map(fromAllocationPayload),
    rebalanceFrequency: payload.rebalance_policy.frequency,
    rebalanceThresholdPercent:
      payload.rebalance_policy.threshold === null
        ? ""
        : String(payload.rebalance_policy.threshold * 100),
  };
}

export function allocationTotalPercent(allocations: EditorAllocation[]): number {
  return allocations.reduce((total, allocation) => total + Number(allocation.targetWeightPercent), 0);
}

export function isAssetReferenced(state: EditorState, asset: string): boolean {
  const hasOperand = (group: EditorRuleGroup): boolean =>
    group.children.some((child) => {
      if (child.type === "group") {
        return hasOperand(child);
      }
      return child.left.asset === asset || child.right.asset === asset;
    });
  return (
    state.rules.some(
      (rule) =>
        hasOperand(rule.condition) ||
        rule.remainingSymbol === asset ||
        rule.allocations.some((allocation) => allocation.symbol === asset),
    ) || state.fallbackAllocations.some((allocation) => allocation.symbol === asset)
  );
}

export const comparisonOptions: Array<{ value: ComparisonOperator; label: string }> = [
  { value: "greater_than", label: ">" },
  { value: "greater_or_equal", label: ">=" },
  { value: "less_than", label: "<" },
  { value: "less_or_equal", label: "<=" },
  { value: "equal", label: "==" },
];

export const rebalanceOptions: Array<{ value: RebalanceFrequency; label: string }> = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "on_signal_change", label: "On signal change" },
];
