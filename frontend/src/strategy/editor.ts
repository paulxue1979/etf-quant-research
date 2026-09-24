import type {
  ComparisonOperator,
  EditorAllocation,
  EditorAllocationRule,
  EditorCondition,
  EditorOperand,
  EditorRuleGroup,
  EditorRuleNode,
  EditorState,
  EditorRegime,
  EditorRegimeTransition,
  EditorValueZone,
  RegimeEditorState,
  LogicalOperator,
  NoMatchBehavior,
  OperandType,
  PriceField,
  RebalanceFrequency,
  StrategyAllocationPayload,
  StrategyConditionPayload,
  StrategyOperandPayload,
  StrategyPayload,
  StrategyRuleGroupPayload,
  StrategyMode,
  ValueZoneTrigger,
  ValidationIssue,
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
    timeframe: "daily",
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
    noMatchBehavior: "use_fallback",
    fallbackAllocations: [
      { id: nextId("allocation"), symbol: "SGOV", targetWeightPercent: "100" },
    ],
    initialAllocations: [],
    rebalanceFrequency: "weekly",
    rebalanceThresholdPercent: "5",
    strategyMode: "rule_based",
    isDirty: false,
  };
}

function regimeAllocation(symbol: string, targetWeightPercent: string): EditorAllocation {
  return { id: nextId("regime-allocation"), symbol, targetWeightPercent };
}

function regimeState(
  stateId: string,
  displayName: string,
  allocations: EditorAllocation[],
  description = "",
): EditorRegime {
  return {
    id: nextId("regime"),
    stateId,
    displayName,
    description,
    allocations,
    metadata: {},
  };
}

function regimeCondition(
  assets: string[],
  leftTimeframe: "daily" | "weekly",
  rightTimeframe: "daily" | "weekly",
  operator: ComparisonOperator,
  thresholdPercent: string,
): EditorRuleGroup {
  const condition = createCondition(assets);
  condition.left = { ...condition.left, timeframe: leftTimeframe };
  condition.right = { ...condition.right, period: "200", timeframe: rightTimeframe };
  condition.operator = operator;
  condition.thresholdPercent = thresholdPercent;
  return {
    id: nextId("transition-group"),
    type: "group",
    operator: "and",
    children: [condition],
  };
}

function regimeTransition(
  assets: string[],
  transitionId: string,
  fromState: string,
  toState: string,
  priority: string,
  condition: EditorRuleGroup,
  description: string,
): EditorRegimeTransition {
  return {
    id: nextId("transition"),
    transitionId,
    fromState,
    toState,
    priority,
    description,
    condition,
    valueZoneId: "",
    valueZoneTrigger: "match",
  };
}

export function createBlankRegimeEditor(assets: string[]): RegimeEditorState {
  const first = firstAsset(assets);
  return {
    initialRegime: "",
    regimes: [regimeState("PROTECTIVE", "Protective", [regimeAllocation(first, "0")])],
    transitions: [],
    valueZones: [],
    template: "blank",
  };
}

export function createDefaultRegimeEditor(assets: string[]): RegimeEditorState {
  const primary = firstAsset(assets);
  const safeAsset = assets.includes("SGOV") ? "SGOV" : primary;
  const leverageAsset = assets.includes("TQQQ") ? "TQQQ" : primary;
  const states = [
    regimeState("PROTECTIVE", "Protective", [regimeAllocation(primary, "20"), regimeAllocation(safeAsset, "40")], "Trend deterioration protection."),
    regimeState("APPROACH", "Approach", [regimeAllocation(primary, "30"), regimeAllocation(leverageAsset, "10"), regimeAllocation(safeAsset, "30")], "Approach to the long-term value zone."),
    regimeState("VALUE", "Value", [regimeAllocation(primary, "35"), regimeAllocation(leverageAsset, "25"), regimeAllocation(safeAsset, "10")], "Meaningful long-term value zone."),
    regimeState("DEEP_VALUE", "Deep Value", [regimeAllocation(primary, "40"), regimeAllocation(leverageAsset, "30")], "Deep value accumulation."),
    regimeState("RECOVERY_HOLD", "Recovery Hold", [regimeAllocation(primary, "40"), regimeAllocation(leverageAsset, "20")], "Hold the core position established at deep value."),
    regimeState("FULL_RISK_ON", "Full Risk On", [regimeAllocation(primary, "40"), regimeAllocation(leverageAsset, "60")], "Daily trend confirmation for full risk exposure."),
  ];
  const transitions = [
    regimeTransition(assets, "protective-to-approach", "PROTECTIVE", "APPROACH", "1", regimeCondition(assets, "weekly", "weekly", "less_or_equal", "10"), "Enter the approach zone."),
    regimeTransition(assets, "approach-to-value", "APPROACH", "VALUE", "1", regimeCondition(assets, "weekly", "weekly", "less_or_equal", "5"), "Enter the value zone."),
    regimeTransition(assets, "value-to-deep-value", "VALUE", "DEEP_VALUE", "1", regimeCondition(assets, "weekly", "weekly", "less_or_equal", "0"), "Enter deep value."),
    regimeTransition(assets, "deep-value-to-recovery", "DEEP_VALUE", "RECOVERY_HOLD", "1", regimeCondition(assets, "weekly", "weekly", "greater_than", "0"), "Weekly recovery from deep value."),
    regimeTransition(assets, "recovery-to-full-risk", "RECOVERY_HOLD", "FULL_RISK_ON", "1", regimeCondition(assets, "daily", "daily", "greater_than", "3"), "Daily trend confirmation after recovery."),
    regimeTransition(assets, "full-risk-to-protective", "FULL_RISK_ON", "PROTECTIVE", "1", regimeCondition(assets, "daily", "daily", "less_than", "-4"), "Daily risk deterioration protection."),
  ];
  const zone = (zoneId: string, displayName: string, entry: string, exit: string, priority: string): EditorValueZone => ({
    id: nextId("value-zone"),
    zoneId,
    displayName,
    asset: primary,
    timeframe: "weekly",
    indicatorKind: "ma",
    period: "200",
    priceField: "adjusted_close",
    entryThresholdPercent: entry,
    exitThresholdPercent: exit,
    entryOperator: "less_or_equal",
    exitOperator: "greater_or_equal",
    priority,
    metadata: {},
  });
  return {
    initialRegime: "PROTECTIVE",
    regimes: states,
    transitions,
    valueZones: [
      zone("APPROACH_ZONE", "Approach", "10", "13", "1"),
      zone("VALUE_ZONE", "Value", "5", "8", "2"),
      zone("DEEP_VALUE_ZONE", "Deep Value", "0", "3", "3"),
    ],
    template: "long_term_value_trend",
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
  | { type: "strategyMode"; value: StrategyMode }
  | { type: "regime"; value: RegimeEditorState }
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
  | { type: "noMatchBehavior"; value: NoMatchBehavior }
  | { type: "initialAllocation"; allocation: EditorAllocation }
  | { type: "addInitialAllocation" }
  | { type: "removeInitialAllocation"; allocationId: string }
  | { type: "rebalance"; field: "frequency" | "threshold"; value: string }
  | { type: "replace"; state: EditorState };

function reduceEditorState(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "basic":
      return { ...state, [action.field]: action.value };
    case "priceField":
      return { ...state, priceField: action.value };
    case "strategyMode":
      return {
        ...state,
        strategyMode: action.value,
        regime:
          action.value === "regime_state_machine"
            ? state.regime ?? createBlankRegimeEditor(state.assets)
            : state.regime,
      };
    case "regime":
      return { ...state, regime: action.value, strategyMode: "regime_state_machine" };
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
    case "noMatchBehavior":
      return { ...state, noMatchBehavior: action.value };
    case "initialAllocation":
      return {
        ...state,
        initialAllocations: state.initialAllocations.map((allocation) =>
          allocation.id === action.allocation.id ? action.allocation : allocation,
        ),
      };
    case "addInitialAllocation":
      return {
        ...state,
        initialAllocations: [...state.initialAllocations, createAllocation(state.assets)],
      };
    case "removeInitialAllocation":
      return {
        ...state,
        initialAllocations: state.initialAllocations.filter(
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

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  const next = reduceEditorState(state, action);
  return action.type === "replace" ? { ...next, isDirty: false } : { ...next, isDirty: true };
}

function toNumber(value: string, field: string): number {
  const numeric = Number(value);
  if (value.trim() === "" || !Number.isFinite(numeric)) {
    throw new Error(`${field} must be a finite number`);
  }
  return numeric;
}

function formatPercentValue(value: number): string {
  return String(Number((value * 100).toFixed(8)));
}

function toWeightPayload(allocation: EditorAllocation): StrategyAllocationPayload {
  return {
    symbol: allocation.symbol,
    target_weight: toNumber(allocation.targetWeightPercent, "allocation weight") / 100,
  };
}

function toOperandPayload(operand: EditorOperand, priceField: PriceField): StrategyOperandPayload {
  if (operand.type === "constant") {
    return {
      type: "constant",
      asset: operand.asset,
      value: toNumber(operand.value, "constant"),
    };
  }
  if (operand.type === "price") {
    return {
      type: "price",
      asset: operand.asset,
      price_field: priceField,
      timeframe: operand.timeframe,
    };
  }
  return {
    type: operand.type,
    asset: operand.asset,
    period: toNumber(operand.period, "indicator period"),
    price_field: priceField,
    timeframe: operand.timeframe,
  };
}

function toConditionPayload(
  condition: EditorCondition,
  priceField: PriceField,
): StrategyConditionPayload {
  const threshold =
    condition.operator === "equal" || condition.thresholdPercent.trim() === ""
      ? null
      : {
          type: "relative" as const,
          value: toNumber(condition.thresholdPercent, "relative threshold") / 100,
        };
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

function toRegimePayload(state: RegimeEditorState, priceField: PriceField): {
  initial_regime: string | null;
  regimes: unknown[];
  transitions: unknown[];
  value_zones: unknown[];
} {
  return {
    initial_regime: state.initialRegime || null,
    regimes: state.regimes.map((regime) => ({
      ...(regime.source ?? {}),
      state_id: regime.stateId.trim(),
      display_name: regime.displayName,
      target_allocation: {
        ...((regime.source?.target_allocation as Record<string, unknown> | undefined) ?? {}),
        allocations: regime.allocations.map(toWeightPayload),
      },
      metadata: { ...regime.metadata },
    })),
    transitions: state.transitions.map((transition) => ({
      ...(transition.source ?? {}),
      transition_id: transition.transitionId.trim(),
      from_state: transition.fromState,
      to_state: transition.toState,
      priority: toNumber(transition.priority, "transition priority"),
      description: transition.description,
      condition: toRuleGroupPayload(transition.condition, priceField),
      ...(transition.valueZoneId
        ? { value_zone_id: transition.valueZoneId, value_zone_trigger: transition.valueZoneTrigger }
        : { value_zone_id: null }),
    })),
    value_zones: state.valueZones.map((zone) => ({
      ...(zone.source ?? {}),
      zone_id: zone.zoneId.trim(),
      display_name: zone.displayName,
      asset: zone.asset,
      timeframe: zone.timeframe,
      indicator_kind: zone.indicatorKind,
      period: toNumber(zone.period, "value zone period"),
      price_field: zone.priceField,
      entry_threshold: toNumber(zone.entryThresholdPercent, "value zone entry threshold") / 100,
      exit_threshold:
        zone.exitThresholdPercent.trim() === ""
          ? null
          : toNumber(zone.exitThresholdPercent, "value zone exit threshold") / 100,
      entry_operator: zone.entryOperator,
      exit_operator: zone.exitOperator,
      priority: toNumber(zone.priority, "value zone priority"),
      metadata: { ...zone.metadata },
    })),
  };
}

export function toStrategyPayload(state: EditorState): StrategyPayload {
  if (!state.isDirty && state.sourcePayload) {
    return clonePayload(state.sourcePayload);
  }
  const payload: StrategyPayload = {
    ...(state.sourcePayload ? clonePayload(state.sourcePayload) : {}),
    strategy_schema_version: "2.0",
    strategy_id: state.strategyId.trim(),
    name: state.name,
    description: state.description,
    assets: state.assets.map((symbol) => ({ symbol, role: "both" })),
    price_field: state.priceField,
    rules: state.rules.map((rule) => ({
      rule_id: rule.ruleId,
      name: rule.name,
      priority: toNumber(rule.priority, "rule priority"),
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
          : toNumber(state.rebalanceThresholdPercent, "rebalance threshold") / 100,
    },
  };
  if (state.noMatchBehavior === "hold_previous_allocation") {
    payload.no_match_behavior = state.noMatchBehavior;
    payload.initial_allocation = {
      allocations: state.initialAllocations.map(toWeightPayload),
    };
  }
  if (state.strategyMode === "regime_state_machine" && state.regime) {
    const regimePayload = toRegimePayload(state.regime, state.priceField);
    payload.strategy_mode = "regime_state_machine";
    payload.initial_regime = regimePayload.initial_regime;
    payload.regimes = regimePayload.regimes;
    payload.transitions = regimePayload.transitions;
    payload.value_zones = regimePayload.value_zones;
  } else {
    delete payload.strategy_mode;
    delete payload.initial_regime;
    delete payload.regimes;
    delete payload.transitions;
    delete payload.value_zones;
  }
  return payload;
}

function fromOperandPayload(payload: StrategyOperandPayload): EditorOperand {
  return {
    id: nextId("operand"),
    type: payload.type,
    asset: payload.asset,
    period: payload.period === undefined ? "" : String(payload.period),
    value: payload.value === undefined ? "" : String(payload.value),
    timeframe: payload.timeframe ?? "daily",
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
      thresholdPercent: node.threshold ? formatPercentValue(node.threshold.value) : "",
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function fromRegimePayload(payload: StrategyPayload): RegimeEditorState | undefined {
  if (payload.strategy_mode !== "regime_state_machine") return undefined;
  const regimes = Array.isArray(payload.regimes) ? payload.regimes : [];
  const transitions = Array.isArray(payload.transitions) ? payload.transitions : [];
  const zones = Array.isArray(payload.value_zones) ? payload.value_zones : [];
  const assets = payload.assets.map((asset) => asset.symbol);
  const regimeEditors = regimes.map((item) => {
    const source = asRecord(item);
    const allocation = asRecord(source.target_allocation);
    const rawAllocations = Array.isArray(allocation.allocations) ? allocation.allocations : [];
    const metadata = asRecord(source.metadata);
    return {
      id: nextId("regime"),
      stateId: String(source.state_id ?? ""),
      displayName: String(source.display_name ?? source.state_id ?? ""),
      description: String(source.description ?? ""),
      allocations: rawAllocations.map((entry) => fromAllocationPayload(asRecord(entry) as unknown as StrategyAllocationPayload)),
      metadata: Object.fromEntries(Object.entries(metadata).filter(([, value]) => typeof value === "string")) as Record<string, string>,
      source: clonePayload({
        strategy_id: "source",
        name: "source",
        description: "source",
        assets: [{ symbol: "QQQ" }],
        price_field: "adjusted_close",
        rules: [],
        fallback: { name: "fallback", allocations: [] },
        rebalance_policy: { frequency: "daily", threshold: null },
        regime_source: source,
      }).regime_source as Record<string, unknown>,
    };
  });
  const transitionEditors = transitions.map((item) => {
    const source = asRecord(item);
    const conditionPayload = asRecord(source.condition);
    const conditionNode = conditionPayload.type === "condition" || (conditionPayload.type === "group" && Array.isArray(conditionPayload.children))
      ? fromRuleNode(conditionPayload as unknown as StrategyRuleGroupPayload)
      : createRuleGroup(assets);
    const condition = conditionNode.type === "group"
      ? conditionNode
      : { id: nextId("transition-group"), type: "group" as const, operator: "and" as const, children: [conditionNode] };
    return {
      id: nextId("transition"),
      transitionId: String(source.transition_id ?? ""),
      fromState: String(source.from_state ?? ""),
      toState: String(source.to_state ?? ""),
      priority: String(source.priority ?? ""),
      description: String(source.description ?? ""),
      condition,
      valueZoneId: String(source.value_zone_id ?? ""),
      valueZoneTrigger: (source.value_zone_trigger === "enter" || source.value_zone_trigger === "exit" ? source.value_zone_trigger : "match") as ValueZoneTrigger,
      source,
    };
  });
  const zoneEditors = zones.map((item) => {
    const source = asRecord(item);
    return {
      id: nextId("value-zone"),
      zoneId: String(source.zone_id ?? ""),
      displayName: String(source.display_name ?? source.zone_id ?? ""),
      asset: String(source.asset ?? firstAsset(assets)),
      timeframe: source.timeframe === "weekly" ? "weekly" as const : "daily" as const,
      indicatorKind: source.indicator_kind === "ema" ? "ema" as const : "ma" as const,
      period: String(source.period ?? "200"),
      priceField: source.price_field === "raw_close" ? "raw_close" as const : "adjusted_close" as const,
      entryThresholdPercent: formatPercentValue(Number(source.entry_threshold ?? 0)),
      exitThresholdPercent: source.exit_threshold == null ? "" : formatPercentValue(Number(source.exit_threshold)),
      entryOperator: (source.entry_operator ?? "less_or_equal") as EditorValueZone["entryOperator"],
      exitOperator: (source.exit_operator ?? "greater_or_equal") as EditorValueZone["exitOperator"],
      priority: String(source.priority ?? "0"),
      metadata: Object.fromEntries(Object.entries(asRecord(source.metadata)).filter(([, value]) => typeof value === "string")) as Record<string, string>,
      source,
    };
  });
  return {
    initialRegime: payload.initial_regime ?? "",
    regimes: regimeEditors,
    transitions: transitionEditors,
    valueZones: zoneEditors,
    template: "blank",
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
    noMatchBehavior: payload.no_match_behavior ?? "use_fallback",
    fallbackAllocations: payload.fallback.allocations.map(fromAllocationPayload),
    initialAllocations: (payload.initial_allocation?.allocations ?? []).map(
      fromAllocationPayload,
    ),
    rebalanceFrequency: payload.rebalance_policy.frequency,
    rebalanceThresholdPercent:
      payload.rebalance_policy.threshold === null
        ? ""
        : formatPercentValue(payload.rebalance_policy.threshold),
    strategyMode: payload.strategy_mode ?? "rule_based",
    regime: fromRegimePayload(payload),
    isDirty: false,
    sourceSchemaVersion: payload.strategy_schema_version ?? "1.0",
    sourcePayload: clonePayload(payload),
  };
}

function clonePayload(payload: StrategyPayload): StrategyPayload {
  return JSON.parse(JSON.stringify(payload)) as StrategyPayload;
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
    ) ||
    state.fallbackAllocations.some((allocation) => allocation.symbol === asset) ||
    state.initialAllocations.some((allocation) => allocation.symbol === asset) ||
    state.regime?.regimes.some((regime) => regime.allocations.some((allocation) => allocation.symbol === asset)) === true ||
    state.regime?.valueZones.some((zone) => zone.asset === asset) === true ||
    state.regime?.transitions.some((transition) => {
      const visit = (group: EditorRuleGroup): boolean => group.children.some((child) => child.type === "group" ? visit(child) : child.left.asset === asset || child.right.asset === asset);
      return visit(transition.condition);
    }) === true
  );
}

function numericIssue(value: string, path: string, label: string): ValidationIssue | null {
  if (value.trim() !== "" && Number.isFinite(Number(value))) return null;
  return { code: "InvalidNumericInput", path, message: `${label} must be a finite number.` };
}

function allocationIssues(
  allocations: EditorAllocation[],
  assets: string[],
  path: string,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  let total = 0;
  allocations.forEach((allocation, index) => {
    const itemPath = `${path}.allocations[${index}]`;
    if (!assets.includes(allocation.symbol)) {
      issues.push({
        code: "UnknownAsset",
        path: `${itemPath}.symbol`,
        message: `${allocation.symbol || "Allocation"} must use a declared strategy asset.`,
      });
    }
    const numeric = numericIssue(
      allocation.targetWeightPercent,
      `${itemPath}.target_weight`,
      "Allocation weight",
    );
    if (numeric) {
      issues.push(numeric);
      return;
    }
    const weight = Number(allocation.targetWeightPercent);
    if (weight < 0) {
      issues.push({
        code: "InvalidAllocationWeight",
        path: `${itemPath}.target_weight`,
        message: "Allocation weight cannot be negative.",
      });
    }
    total += weight;
  });
  if (total > 100) {
    issues.push({
      code: "AllocationExceeds100Percent",
      path: `${path}.allocations`,
      message: "Explicit allocation cannot exceed 100%.",
    });
  }
  return issues;
}

function ruleNumericIssues(rule: EditorAllocationRule, index: number): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const priority = numericIssue(rule.priority, `rules[${index}].priority`, "Rule priority");
  if (priority) issues.push(priority);
  const visit = (group: EditorRuleGroup) => {
    group.children.forEach((child) => {
      if (child.type === "group") {
        visit(child);
        return;
      }
      if (child.operator !== "equal" && child.thresholdPercent.trim() !== "") {
        const issue = numericIssue(
          child.thresholdPercent,
          `rules[${index}].condition.${child.id}.threshold`,
          "Relative threshold",
        );
        if (issue) issues.push(issue);
      }
      [child.left, child.right].forEach((operand) => {
        if (operand.type !== "constant" && !["daily", "weekly"].includes(operand.timeframe)) {
          issues.push({
            code: "InvalidTimeframe",
            path: `rules[${index}].condition.${child.id}.${operand.id}.timeframe`,
            message: "Timeframe must be Daily or Weekly.",
          });
        }
        const field = operand.type === "constant" ? operand.value : operand.period;
        if (operand.type === "price") return;
        const issue = numericIssue(
          field,
          `rules[${index}].condition.${child.id}.${operand.id}`,
          operand.type === "constant" ? "Constant" : "Indicator period",
        );
        if (issue) issues.push(issue);
        else if (operand.type === "ma" || operand.type === "ema") {
          const period = Number(operand.period);
          if (!Number.isInteger(period) || period <= 0) {
            issues.push({
              code: "InvalidIndicatorPeriod",
              path: `rules[${index}].condition.${child.id}.${operand.id}.period`,
              message: "Indicator period must be a positive integer.",
            });
          }
        }
      });
    });
  };
  visit(rule.condition);
  return issues;
}

export function validateEditorState(state: EditorState): ValidationIssue[] {
  const issues = state.rules.flatMap((rule, index) => [
    ...ruleNumericIssues(rule, index),
    ...allocationIssues(rule.allocations, state.assets, `rules[${index}]`),
  ]);
  if (state.noMatchBehavior === "use_fallback") {
    issues.push(...allocationIssues(state.fallbackAllocations, state.assets, "fallback"));
  } else {
    issues.push(...allocationIssues(state.initialAllocations, state.assets, "initial_allocation"));
  }
  if (state.rebalanceThresholdPercent.trim() !== "") {
    const threshold = numericIssue(
      state.rebalanceThresholdPercent,
      "rebalance_policy.threshold",
      "Rebalance threshold",
    );
    if (threshold) issues.push(threshold);
  }
  return issues;
}

export function validateConditionGroup(
  group: EditorRuleGroup,
  assets: string[],
  path: string,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const visit = (node: EditorRuleGroup, nodePath: string) => {
    node.children.forEach((child, index) => {
      if (child.type === "group") {
        visit(child, `${nodePath}.children[${index}]`);
        return;
      }
      const conditionPath = `${nodePath}.children[${index}]`;
      if (!assets.includes(child.left.asset) && child.left.type !== "constant") {
        issues.push({ code: "UnknownAsset", path: `${conditionPath}.left.asset`, message: `${child.left.asset} is not a declared asset.` });
      }
      if (!assets.includes(child.right.asset) && child.right.type !== "constant") {
        issues.push({ code: "UnknownAsset", path: `${conditionPath}.right.asset`, message: `${child.right.asset} is not a declared asset.` });
      }
      [child.left, child.right].forEach((operand, operandIndex) => {
        if (operand.type !== "constant" && !["daily", "weekly"].includes(operand.timeframe)) {
          issues.push({ code: "InvalidTimeframe", path: `${conditionPath}.operand[${operandIndex}].timeframe`, message: "Timeframe must be Daily or Weekly." });
        }
        if (operand.type === "ma" || operand.type === "ema") {
          const period = Number(operand.period);
          if (!Number.isInteger(period) || period <= 0) {
            issues.push({ code: "InvalidIndicatorPeriod", path: `${conditionPath}.operand[${operandIndex}].period`, message: "Indicator period must be a positive integer." });
          }
        }
      });
      if (child.operator !== "equal" && child.thresholdPercent.trim() !== "" && !Number.isFinite(Number(child.thresholdPercent))) {
        issues.push({ code: "InvalidThreshold", path: `${conditionPath}.threshold`, message: "Relative threshold must be a finite number." });
      }
    });
  };
  visit(group, path);
  return issues;
}

export function validateRegimeEditor(
  regime: RegimeEditorState,
  assets: string[],
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const stateIds = regime.regimes.map((item) => item.stateId.trim());
  const stateSet = new Set(stateIds);
  if (!regime.initialRegime.trim()) {
    issues.push({ code: "MissingInitialRegime", path: "initial_regime", message: "Initial state must be selected." });
  } else if (!stateSet.has(regime.initialRegime)) {
    issues.push({ code: "MissingInitialRegime", path: "initial_regime", message: "Initial state must reference a declared state." });
  }
  stateIds.forEach((stateId, index) => {
    if (!stateId) issues.push({ code: "InvalidStateId", path: `regimes[${index}].state_id`, message: "State ID must not be empty." });
    if (stateId && stateIds.indexOf(stateId) !== index) issues.push({ code: "DuplicateRegimeState", path: `regimes[${index}].state_id`, message: "State IDs must be unique." });
  });
  regime.regimes.forEach((state, index) => {
    const seen = new Set<string>();
    let total = 0;
    state.allocations.forEach((allocation, allocationIndex) => {
      if (!assets.includes(allocation.symbol)) issues.push({ code: "UnknownAsset", path: `regimes[${index}].allocations[${allocationIndex}].symbol`, message: `${allocation.symbol || "Allocation"} is not a declared execution asset.` });
      if (seen.has(allocation.symbol)) issues.push({ code: "DuplicateAllocation", path: `regimes[${index}].allocations[${allocationIndex}].symbol`, message: "An asset may appear only once in a state allocation." });
      seen.add(allocation.symbol);
      const weight = Number(allocation.targetWeightPercent);
      if (!Number.isFinite(weight) || weight < 0) issues.push({ code: "InvalidAllocationWeight", path: `regimes[${index}].allocations[${allocationIndex}].target_weight`, message: "State allocation must be a non-negative finite percentage." });
      if (Number.isFinite(weight)) total += weight;
    });
    if (total > 100) issues.push({ code: "AllocationExceeds100Percent", path: `regimes[${index}].allocations`, message: "State allocation cannot exceed 100%; Cash is the implicit remainder." });
  });
  const transitionIds = new Set<string>();
  const priorities = new Map<string, Set<number>>();
  regime.transitions.forEach((transition, index) => {
    if (transitionIds.has(transition.transitionId.trim())) issues.push({ code: "DuplicateTransitionId", path: `transitions[${index}].transition_id`, message: "Transition IDs must be unique." });
    transitionIds.add(transition.transitionId.trim());
    if (!stateSet.has(transition.fromState)) issues.push({ code: "InvalidRegimeGraph", path: `transitions[${index}].from_state`, message: "From state must reference a declared state." });
    if (!stateSet.has(transition.toState)) issues.push({ code: "InvalidRegimeGraph", path: `transitions[${index}].to_state`, message: "To state must reference a declared state." });
    if (transition.fromState === transition.toState) issues.push({ code: "SelfRegimeTransition", path: `transitions[${index}].to_state`, message: "Self-transitions are not allowed." });
    const priority = Number(transition.priority);
    const fromPriorities = priorities.get(transition.fromState) ?? new Set<number>();
    if (Number.isInteger(priority) && fromPriorities.has(priority)) issues.push({ code: "RegimePriorityConflict", path: `transitions[${index}].priority`, message: "Outgoing transition priorities must be unique per state." });
    if (Number.isInteger(priority)) fromPriorities.add(priority);
    priorities.set(transition.fromState, fromPriorities);
    if (transition.valueZoneId && !regime.valueZones.some((zone) => zone.zoneId === transition.valueZoneId)) issues.push({ code: "UnknownValueZoneReference", path: `transitions[${index}].value_zone_id`, message: "Transition value zone reference is not declared." });
    issues.push(...validateConditionGroup(transition.condition, assets, `transitions[${index}].condition`));
  });
  const zoneIds = new Set<string>();
  const zonePriorities = new Set<number>();
  regime.valueZones.forEach((zone, index) => {
    if (zoneIds.has(zone.zoneId.trim())) issues.push({ code: "DuplicateValueZone", path: `value_zones[${index}].zone_id`, message: "Value Zone IDs must be unique." });
    zoneIds.add(zone.zoneId.trim());
    const priority = Number(zone.priority);
    if (Number.isInteger(priority) && zonePriorities.has(priority)) issues.push({ code: "ValueZonePriorityConflict", path: `value_zones[${index}].priority`, message: "Value Zone priorities must be unique." });
    if (Number.isInteger(priority)) zonePriorities.add(priority);
    if (!assets.includes(zone.asset)) issues.push({ code: "UnknownAsset", path: `value_zones[${index}].asset`, message: `${zone.asset} is not a declared signal asset.` });
    const entry = Number(zone.entryThresholdPercent);
    const exit = zone.exitThresholdPercent.trim() === "" ? null : Number(zone.exitThresholdPercent);
    if (!Number.isFinite(entry) || (exit !== null && !Number.isFinite(exit))) issues.push({ code: "InvalidValueZone", path: `value_zones[${index}]`, message: "Value Zone thresholds must be finite numbers." });
    if (exit !== null && zone.entryOperator.startsWith("less") && exit <= entry) issues.push({ code: "InvalidValueZoneHysteresis", path: `value_zones[${index}].exit_threshold`, message: "Downward Value Zones require Exit > Entry." });
    if (exit !== null && zone.entryOperator.startsWith("greater") && exit >= entry) issues.push({ code: "InvalidValueZoneHysteresis", path: `value_zones[${index}].exit_threshold`, message: "Upward Value Zones require Exit < Entry." });
  });
  if (regime.initialRegime && stateSet.has(regime.initialRegime)) {
    const reachable = new Set([regime.initialRegime]);
    let changed = true;
    while (changed) {
      changed = false;
      regime.transitions.forEach((transition) => {
        if (reachable.has(transition.fromState) && !reachable.has(transition.toState)) {
          reachable.add(transition.toState);
          changed = true;
        }
      });
    }
    regime.regimes.forEach((state, index) => {
      if (!reachable.has(state.stateId)) issues.push({ code: "UnreachableState", path: `regimes[${index}].state_id`, message: "State is unreachable from the initial state.", });
    });
  }
  return issues;
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
