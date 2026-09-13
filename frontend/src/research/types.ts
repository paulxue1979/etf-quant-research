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
