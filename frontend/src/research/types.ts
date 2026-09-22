export type ResultStatus = "completed" | "failed" | "not_evaluable" | "missing_result" | "inconsistent";
export type ExecutionStatus = "pending" | "running" | "completed" | "failed" | null;

export interface MetricValue {
  value: number | null;
  status: "available" | "not_evaluable";
  reason: string | null;
}

export interface CandidateResult {
  candidate_id: string;
  candidate_index: number;
  parameter_set: Record<string, unknown>;
  parameter_set_hash: string;
  candidate_set_hash: string;
  parameter_space_hash: string;
  objective_spec_hash: string;
  execution_status: ExecutionStatus;
  result_status: ResultStatus;
  failure_code?: string | null;
  failure_summary?: string | null;
  experiment_result_id?: string | null;
  result_hash?: string | null;
  backtest_run_id?: string | null;
  base_strategy_version_id?: string | null;
  base_strategy_version_hash?: string | null;
  derived_strategy_version_id?: string | null;
  derived_strategy_version_hash?: string | null;
  binding_hash?: string | null;
  materialization_spec_hash?: string | null;
  is_start?: string | null;
  is_end?: string | null;
  warmup_start?: string | null;
  warmup_end?: string | null;
  price_field_used?: string | null;
  backtest_configuration_hash?: string | null;
  engine_version?: string | null;
  analysis_version?: string | null;
  data_snapshot_reference?: Record<string, unknown>;
  configuration_snapshot?: Record<string, unknown>;
  performance_summary?: Record<string, MetricValue>;
}

export interface ResultsSummary {
  candidate_count: number;
  completed_count: number;
  failed_count: number;
  not_evaluable_count: number;
  running_count: number;
  pending_count: number;
  result_count: number;
  complete: boolean;
  completeness_status: string;
}

export interface ExperimentResults {
  experiment_id: string;
  protocol_id: string;
  experiment_status: string;
  is_start: string;
  is_end: string;
  parameter_space_hash: string;
  objective_spec_hash: string;
  base_strategy_version_id: string;
  base_strategy_version_hash: string;
  engine_version: string;
  analysis_version: string;
  candidates: CandidateResult[];
  summary: ResultsSummary;
  candidate_set?: { candidate_set_hash: string | null };
  provenance?: Record<string, unknown>;
}

export interface CompatibilityMismatch {
  candidate_id: string;
  dimension: string;
  reason_code: string;
  reference_value: unknown;
  candidate_value: unknown;
  message: string;
}

export interface CompatibilityDiagnostic {
  status: "compatible" | "incompatible" | "not_evaluable";
  is_compatible: boolean;
  experiment_id: string;
  protocol_id: string;
  reference_candidate_id: string | null;
  compared_candidate_ids: string[];
  unavailable_candidate_ids: string[];
  checked_dimensions: string[];
  mismatches: CompatibilityMismatch[];
  provenance?: Record<string, unknown>;
}

export type ObjectiveState = "pass" | "fail" | "not_evaluable";
export interface ConstraintResult {
  metric: string;
  operator: string;
  threshold: number;
  metric_value: number | null;
  metric_status: string;
  state: ObjectiveState;
  reason_code: string;
  reason: string;
}
export interface ObjectiveEvaluation {
  experiment_id: string;
  candidate_id: string;
  objective_hash: string;
  experiment_result_id: string | null;
  result_hash: string | null;
  constraint_results: ConstraintResult[];
  overall_constraint_state: ObjectiveState;
}
export interface ObjectiveEvaluationResponse {
  experiment_id: string;
  protocol_id: string;
  objective_spec_hash: string;
  objective_specification: Record<string, unknown>;
  evaluations: Array<{ candidate_id: string; candidate_index: number; evaluation: ObjectiveEvaluation }>;
  provenance?: Record<string, unknown>;
}

export type SelectionMethod = "researcher_judgment" | "constraint_filtered_researcher_selection" | "manual_research_selection";
export interface SelectionRequest {
  selected_candidate_id: string;
  selection_method: SelectionMethod;
  researcher_rationale: string;
}
export interface SelectionDecision {
  selection_id: string;
  selection_hash: string;
  experiment_id: string;
  protocol_id: string;
  selected_candidate_id: string;
  selected_candidate_index: number;
  selected_parameter_set_hash: string;
  selected_experiment_result_id: string | null;
  selected_result_hash: string | null;
  selected_derived_strategy_version_id: string;
  selected_derived_strategy_content_hash: string;
  objective_hash: string;
  candidate_set_hash: string;
  parameter_space_hash: string;
  selection_method: SelectionMethod;
  researcher_rationale: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface HandoffResponse {
  experiment_id: string;
  protocol_id: string;
  selection_decision: Record<string, unknown>;
  strategy_freeze: Record<string, unknown>;
  identity: { strategy_version_id: string; strategy_version_content_hash: string };
  provenance?: Record<string, unknown>;
}

export type OptimizationMetricStatus = "available" | "unavailable" | "not_applicable" | "failed" | "excluded";

export interface OptimizationMetricDefinition {
  metric_id: string;
  display_name: string;
  source_path: string[];
  direction: "maximize" | "minimize";
  format: "percent" | "ratio" | "integer" | "currency" | "days";
  category: string;
  nullable: boolean;
  heatmap_eligible: boolean;
  pareto_eligible: boolean;
}

export interface OptimizationMetricRegistry {
  schema_version: string;
  metrics: OptimizationMetricDefinition[];
  max_drawdown_semantics: string;
  turnover_semantics: string;
}

export interface OptimizationProjectedMetric {
  metric_id: string;
  value: number | null;
  status: OptimizationMetricStatus;
  reason: string | null;
}

export interface OptimizationCandidate {
  experiment_id: string;
  candidate_id: string;
  candidate_index: number;
  parameter_set_hash: string;
  candidate_set_hash: string;
  parameter_values: Record<string, unknown>;
  execution_status: string;
  result_status: string;
  experiment_result_id: string | null;
  result_hash: string | null;
  derived_strategy_version_id: string | null;
  derived_strategy_version_hash: string | null;
  backtest_configuration_hash: string | null;
  backtest_run_id: string | null;
  is_start: string | null;
  is_end: string | null;
  engine_version: string | null;
  analysis_version: string | null;
  failure_code: string | null;
  failure_summary: string | null;
  metrics: Record<string, OptimizationProjectedMetric>;
}

export interface OptimizationFilter {
  statuses?: string[];
  candidate_ids?: string[];
  minimum_trade_count?: number;
  maximum_turnover?: number;
  maximum_drawdown_magnitude?: number;
  minimum_cagr?: number;
  minimum_sharpe?: number;
  minimum_exposure?: number;
  maximum_exposure?: number;
  parameters?: Record<string, { exact?: unknown; minimum?: number; maximum?: number }>;
}

export interface OptimizationResults {
  schema_version: string;
  experiment_id: string;
  protocol_id: string;
  is_start: string;
  is_end: string;
  is_only: boolean;
  parameter_space: {
    parameters: Array<{
      name: string;
      type: string;
      min: number | null;
      max: number | null;
      step: number | null;
      precision: number | null;
      allowed_values: unknown[];
    }>;
  };
  source_count: number;
  filtered_count: number;
  applied_filter: OptimizationFilter;
  candidates: OptimizationCandidate[];
  exclusions: Record<string, string[]>;
}

export interface OptimizationHeatmap {
  schema_version: string;
  experiment_id: string;
  metric: OptimizationMetricDefinition;
  x_parameter: string;
  y_parameter: string;
  x_values: unknown[];
  y_values: unknown[];
  fixed_parameter_values: Record<string, unknown>;
  applied_filter: OptimizationFilter;
  source_count: number;
  filtered_count: number;
  aggregation: null;
  interpolation: false;
  cells: Array<{
    x_value: unknown;
    y_value: unknown;
    candidate_id: string | null;
    candidate_index: number | null;
    parameter_set_hash: string | null;
    metric: OptimizationProjectedMetric;
    cell_status: OptimizationMetricStatus | "pruned";
    supporting_metrics: Record<string, OptimizationProjectedMetric>;
  }>;
}

export interface OptimizationPareto {
  schema_version: string;
  objectives: OptimizationMetricDefinition[];
  source_count: number;
  filtered_count: number;
  eligible_count: number;
  excluded_count: number;
  frontier: OptimizationAnalysisPoint[];
  dominated: OptimizationAnalysisPoint[];
  exclusions: Record<string, string[]>;
  applied_filter: OptimizationFilter;
}

export interface OptimizationAnalysisPoint {
  candidate_id: string;
  candidate_index: number;
  parameter_values: Record<string, unknown>;
  metrics: Record<string, OptimizationProjectedMetric>;
}

export interface OptimizationStability {
  schema_version: string;
  center_candidate_id: string;
  metric: OptimizationMetricDefinition;
  center_metric: OptimizationProjectedMetric;
  topology: string;
  topology_uses_filtered_universe: false;
  statistics: Record<string, number | null>;
  neighbors: Array<{
    changed_parameter: string;
    from_value: unknown;
    to_value: unknown;
    candidate_id: string | null;
    candidate_index: number | null;
    status: string;
    metric: OptimizationProjectedMetric;
  }>;
}

export interface OptimizationSensitivity {
  schema_version: string;
  parameter: string;
  parameter_values: unknown[];
  metrics: OptimizationMetricDefinition[];
  fixed_parameter_values: Record<string, unknown>;
  interpolation: false;
  points: Array<{
    parameter_value: unknown;
    candidate_id: string | null;
    candidate_index: number | null;
    status: string;
    metrics: Record<string, OptimizationProjectedMetric>;
  }>;
}

export interface GridSearchTemplate {
  definition: Record<string, unknown>;
  parameter_space: Record<string, unknown>;
  fixed_parameters: Record<string, unknown>;
  tunable_parameters: Array<Record<string, unknown>>;
}

export interface GridSearchPreflight {
  definition_hash: string;
  parameter_space_hash: string;
  theoretical_count: number;
  pruned_count: number;
  valid_count: number;
  duplicate_count: number;
  executable_count: number;
  max_allowed: number;
  hard_maximum: number;
  theoretical_guard: number;
  pruning_reasons: Record<string, number>;
  status: "ready" | "failed";
  can_execute: boolean;
  issues: string[];
}

export interface GridSearchProgress {
  experiment_id: string;
  definition_hash: string;
  status: "prepared" | "running" | "completed" | "failed" | "cancelled";
  cancel_requested: boolean;
  theoretical: number;
  pruned: number;
  valid: number;
  duplicate: number;
  executable: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  retryable: number;
  cancelled: number;
  progress_percentage: number;
}

export interface OosResearchView {
  read_only: boolean;
  protocol: Record<string, unknown>;
  selection_decision: Record<string, unknown>;
  strategy_freeze: Record<string, unknown>;
  oos_result: {
    oos_result_id: string;
    protocol_id: string;
    execution_id: string | null;
    selection_decision_id: string;
    strategy_freeze_id: string;
    strategy_version_id: string;
    strategy_content_hash: string;
    backtest_run_id: string;
    oos_start: string;
    oos_end: string;
    warmup_start: string;
    price_field_used: string;
    configuration_hash: string;
    engine_version: string;
    analytics_version: string;
    data_provenance: Record<string, unknown>;
    performance_summary: { metrics: Record<string, MetricValue> };
    result_hash: string;
    created_at: string;
  };
  backtest_run: {
    backtest_run_id: string;
    strategy_id: string;
    strategy_version_id: string;
    created_at: string;
    strategy_version_content_hash: string;
    backtest_result: {
      start_date: string;
      end_date: string;
      initial_capital: number;
      final_equity: number;
      equity_curve: Array<{ date: string; cash: number; asset_values: Record<string, number>; total_equity: number }>;
      orders: Array<{ order_id: string; signal_date: string; date: string; symbol: string; side: string; quantity: number; execution_price: number; status: string }>;
      fills: Array<{ order_id: string; date: string; symbol: string; side: string; quantity: number; price: number }>;
      trades: Array<{ symbol: string; entry_date: string; exit_date: string; entry_price: number; exit_price: number; quantity: number; pnl: number; pnl_pct: number; holding_period: number }>;
      positions: Array<{ as_of_date: string; cash: number; total_equity: number; positions: Array<{ symbol: string; quantity: number; market_price: number; market_value: number; unrealized_pnl: number }> }>;
      allocation_history: Array<{ date: string; symbol: string; target_weight: number; actual_weight: number }>;
    };
    performance_analysis: {
      drawdown_curve: Array<{ date: string; value: number }>;
      [key: string]: unknown;
    };
  };
  provenance: Record<string, unknown>;
}
