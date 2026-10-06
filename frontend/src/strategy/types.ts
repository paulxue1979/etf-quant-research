export type PriceField = "raw_close" | "adjusted_close";
export type AssetRole = "signal_source" | "execution_asset" | "both";
export type Timeframe = "daily" | "weekly";
export type StrategySchemaVersion = "1.0" | "2.0";
export type OperandType = "price" | "ma" | "ema" | "peak_drawdown" | "constant";
export type ComparisonOperator =
  | "greater_than"
  | "greater_or_equal"
  | "less_than"
  | "less_or_equal"
  | "equal";
export type LogicalOperator = "and" | "or";
export type RebalanceFrequency = "daily" | "weekly" | "monthly" | "on_signal_change";
export type NoMatchBehavior = "use_fallback" | "hold_previous_allocation";
export type StrategyMode = "rule_based" | "regime_state_machine";
export type ValueZoneTrigger = "match" | "enter" | "exit";

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

export interface EditorRegime {
  id: string;
  stateId: string;
  displayName: string;
  description: string;
  allocations: EditorAllocation[];
  metadata: Record<string, string>;
  source?: Record<string, unknown>;
}

export interface EditorRegimeTransition {
  id: string;
  transitionId: string;
  fromState: string;
  toState: string;
  priority: string;
  description: string;
  condition: EditorRuleGroup;
  valueZoneId: string;
  valueZoneTrigger: ValueZoneTrigger;
  source?: Record<string, unknown>;
}

export interface EditorValueZone {
  id: string;
  zoneId: string;
  displayName: string;
  asset: string;
  timeframe: Timeframe;
  indicatorKind: "ma" | "ema";
  period: string;
  priceField: PriceField;
  entryThresholdPercent: string;
  exitThresholdPercent: string;
  entryOperator: ComparisonOperator;
  exitOperator: ComparisonOperator;
  priority: string;
  metadata: Record<string, string>;
  source?: Record<string, unknown>;
}

export interface RegimeEditorState {
  initialRegime: string;
  regimes: EditorRegime[];
  transitions: EditorRegimeTransition[];
  valueZones: EditorValueZone[];
  template: "blank" | "long_term_value_trend";
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
  strategyMode?: StrategyMode;
  regime?: RegimeEditorState;
  ruleBasedAssets: string[];
  regimeAssets: string[];
  draftRevision: number;
  isDirty?: boolean;
  sourceSchemaVersion?: string;
  sourcePayload?: StrategyPayload;
}

export interface StrategyDraftModePayload {
  assets: string[];
  editor_state: Record<string, unknown>;
  source_payload?: Record<string, unknown> | null;
}

export interface StrategyDraftPayload {
  expected_revision: number;
  active_mode: StrategyMode;
  strategy_id: string;
  name: string;
  description: string;
  price_field: PriceField;
  rebalance_frequency: RebalanceFrequency;
  rebalance_threshold_percent: string;
  rule_based_draft: StrategyDraftModePayload;
  regime_draft: StrategyDraftModePayload;
  active_strategy_payload: StrategyPayload | null;
  source_metadata: Record<string, unknown>;
}

export interface StrategyDraftResponse extends Omit<StrategyDraftPayload, "expected_revision" | "active_strategy_payload"> {
  draft_revision: number;
  active_strategy_payload: StrategyPayload | null;
  created_at: string;
  updated_at: string;
  draft_hash: string;
}

export interface StrategyRecord {
  strategy_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  status: string;
  metadata: Record<string, unknown>;
  draft_revision?: number | null;
  draft_status?: "not_saved" | "saved" | "dirty" | string;
  draft_active_mode?: StrategyMode | null;
  latest_version_id?: string | null;
  latest_version_number?: number | null;
  latest_version_created_at?: string | null;
  lifecycle_status?: "ACTIVE" | "TRASHED" | string;
  is_protected?: boolean;
  deleted_at?: string | null;
  version_count?: number;
}

export interface StrategyDependency {
  kind: string;
  identifier: string;
  detail: string;
}

export interface StrategyLifecycleResponse {
  strategy_id: string;
  result: string;
  dependencies: StrategyDependency[];
}

export interface BulkStrategyTrashResponse {
  requested: number;
  trashed: string[];
  skipped_protected: string[];
  blocked_dependencies: string[];
  not_found: string[];
}

export interface StrategyDiagnostics {
  database_path: string;
  schema_version: number | null;
  strategy_count: number;
  draft_count: number;
  version_count: number;
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
  [key: string]: unknown;
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
  fallback?: { name: string; allocations: StrategyAllocationPayload[] };
  no_match_behavior?: NoMatchBehavior;
  initial_allocation?: { allocations: StrategyAllocationPayload[] };
  strategy_mode?: StrategyMode;
  initial_regime?: string | null;
  regimes?: unknown[];
  transitions?: unknown[];
  value_zones?: unknown[];
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

export type AssetReferenceType =
  | "RULE_CONDITION"
  | "RULE_ALLOCATION"
  | "RULE_REMAINING_ALLOCATION"
  | "FALLBACK_ALLOCATION"
  | "STARTING_ALLOCATION"
  | "STATE_ALLOCATION"
  | "TRANSITION_CONDITION"
  | "TRANSITION_VALUE_ZONE"
  | "VALUE_ZONE";

export interface AssetReferenceDescriptor {
  mode: StrategyMode;
  reference_type: AssetReferenceType;
  entity_id: string;
  display_path: string;
  field: string;
  symbol: string;
}

export interface StrategyVersionSummary {
  strategy_id: string;
  version_id: string;
  version_number: number;
  created_at: string;
  content_hash: string;
  status: string;
  active_mode?: StrategyMode;
  source_draft_revision?: number | null;
  source_draft_hash?: string | null;
  source_provenance?: Record<string, unknown>;
  description?: string;
}

export interface StrategyVersion extends StrategyVersionSummary {
  configuration: StrategyPayload;
}

export interface SemanticTargetPreview {
  parameter_name: string;
  value_type: string;
  target_kind: string;
  label: string;
  descriptor: Record<string, unknown>;
  supported: boolean;
}

export interface SemanticTargetCatalog {
  strategy_version_id: string;
  strategy_version_hash: string;
  targets: SemanticTargetPreview[];
  unsupported: Array<{ target: string; reason: string }>;
}

export interface VersionDiffItem {
  path: string;
  before?: unknown;
  after?: unknown;
  change: "added" | "removed" | "changed";
}

export interface VersionDiff {
  strategy_id: string;
  from_version_id: string;
  to_version_id: string;
  from_version_number: number;
  to_version_number: number;
  items: VersionDiffItem[];
}
