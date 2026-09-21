export type PriceField = "raw_close" | "adjusted_close";
export type AssetRole = "signal_source" | "execution_asset" | "both";
export type Timeframe = "daily" | "weekly";
export type StrategySchemaVersion = "1.0" | "2.0";
export type OperandType = "price" | "ma" | "ema" | "constant";
export type ComparisonOperator =
  | "greater_than"
  | "greater_or_equal"
  | "less_than"
  | "less_or_equal"
  | "equal";
export type LogicalOperator = "and" | "or";
export type RebalanceFrequency = "daily" | "weekly" | "monthly" | "on_signal_change";
export type NoMatchBehavior = "use_fallback" | "hold_previous_allocation";

export interface EditorOperand {
  id: string;
  type: OperandType;
  asset: string;
  period: string;
  value: string;
  timeframe: Timeframe;
}

export interface EditorCondition {
  id: string;
  type: "condition";
  left: EditorOperand;
  operator: ComparisonOperator;
  right: EditorOperand;
  thresholdPercent: string;
}

export interface EditorRuleGroup {
  id: string;
  type: "group";
  operator: LogicalOperator;
  children: EditorRuleNode[];
}

export type EditorRuleNode = EditorCondition | EditorRuleGroup;

export interface EditorAllocation {
  id: string;
  symbol: string;
  targetWeightPercent: string;
}

export interface EditorAllocationRule {
  id: string;
  ruleId: string;
  name: string;
  priority: string;
  condition: EditorRuleGroup;
  allocations: EditorAllocation[];
  remainingSymbol: string;
}

export interface EditorState {
  strategyId: string;
  name: string;
  description: string;
  priceField: PriceField;
  assets: string[];
  rules: EditorAllocationRule[];
  noMatchBehavior: NoMatchBehavior;
  fallbackAllocations: EditorAllocation[];
  initialAllocations: EditorAllocation[];
  rebalanceFrequency: RebalanceFrequency;
  rebalanceThresholdPercent: string;
}

export interface StrategyOperandPayload {
  type: OperandType;
  asset: string;
  period?: number;
  price_field?: PriceField;
  value?: number;
  timeframe?: Timeframe;
}

export interface StrategyConditionPayload {
  type: "condition";
  left: StrategyOperandPayload;
  operator: ComparisonOperator;
  right: StrategyOperandPayload;
  threshold: { type: "relative"; value: number } | null;
}

export interface StrategyRuleGroupPayload {
  type: "group";
  operator: LogicalOperator;
  children: Array<StrategyConditionPayload | StrategyRuleGroupPayload>;
}

export interface StrategyAllocationPayload {
  symbol: string;
  target_weight: number;
}

export interface StrategyPayload {
  strategy_schema_version?: StrategySchemaVersion;
  strategy_id: string;
  name: string;
  description: string;
  assets: Array<{ symbol: string; role?: AssetRole }>;
  price_field: PriceField;
  rules: Array<{
    rule_id: string;
    name: string;
    priority: number;
    condition: StrategyRuleGroupPayload;
    allocations: StrategyAllocationPayload[];
    remaining: { symbol: string } | null;
  }>;
  fallback: { name: string; allocations: StrategyAllocationPayload[] };
  no_match_behavior?: NoMatchBehavior;
  initial_allocation?: { allocations: StrategyAllocationPayload[] };
  rebalance_policy: { frequency: RebalanceFrequency; threshold: number | null };
}

export interface ValidationIssue {
  code: string;
  path: string;
  message: string;
}

export interface ValidationResult {
  is_valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface StrategyVersionSummary {
  strategy_id: string;
  version_id: string;
  version_number: number;
  created_at: string;
  content_hash: string;
  status: string;
}

export interface StrategyVersion extends StrategyVersionSummary {
  configuration: StrategyPayload;
}
