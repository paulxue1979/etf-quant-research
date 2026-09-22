import type { PriceField, StrategyVersionSummary } from "../strategy/types";

export interface StrategyCatalogItem {
  strategy_id: string;
  name: string;
  version_count: number;
  latest_version: number | null;
}

export interface CommissionRequest {
  rate: number;
  per_order: number;
}

export type ContributionFrequency = "one_time" | "monthly";

export interface ContributionScheduleRequest {
  frequency: ContributionFrequency;
  amount: string;
  requested_date?: string;
  currency?: "USD";
}

export interface BacktestRequest {
  strategy_id: string;
  strategy_version_id: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  commission: CommissionRequest;
  slippage: number;
  price_field_used: PriceField;
  execution_rule: "next_trading_day_open";
  fractional_shares: boolean;
  contribution_schedule?: ContributionScheduleRequest;
  benchmark_symbol?: string;
}

export interface MetricValue {
  value: number | null;
  status: "available" | "not_evaluable";
  reason: string | null;
}

export interface DrawdownPoint {
  date: string;
  value: number;
}

export interface WealthPoint {
  date: string;
  value: number;
}

export interface ExposurePoint {
  date: string;
  cash_weight: number;
  gross_exposure: number;
  net_exposure: number;
  asset_weights: Record<string, number>;
  target_cash_weight: number;
  target_asset_weights: Record<string, number>;
}

export interface PerformanceAnalysis {
  backtest_run_id: string;
  strategy_id: string;
  strategy_version_id: string;
  start_date: string;
  end_date: string;
  price_field_used: PriceField;
  rebalance_frequency: string;
  observation_frequency: string;
  initial_capital: number;
  final_equity: number;
  total_return: MetricValue;
  cagr: MetricValue;
  annualized_volatility: MetricValue;
  sharpe_ratio: MetricValue;
  sortino_ratio: MetricValue;
  max_drawdown: MetricValue;
  max_drawdown_duration: MetricValue;
  recovery_duration: MetricValue;
  max_drawdown_recovered: boolean;
  calmar_ratio: MetricValue;
  xirr: MetricValue;
  turnover: MetricValue;
  twr_wealth_curve: WealthPoint[];
  exposure_curve: ExposurePoint[];
  exposure_summary: Record<string, unknown>;
  turnover_provenance: Record<string, unknown>;
  trade_metrics: {
    number_of_closed_trades: number;
    winning_trades: number;
    losing_trades: number;
    win_rate: MetricValue;
    profit_factor: MetricValue;
    average_trade_return: MetricValue;
    best_trade: MetricValue;
    worst_trade: MetricValue;
    average_holding_period: MetricValue;
    turnover: MetricValue;
  };
  drawdown_curve: DrawdownPoint[];
}

export interface EquityPoint {
  date: string;
  cash: number;
  asset_values: Record<string, number>;
  total_equity: number;
}

export interface PositionSnapshot {
  as_of_date: string;
  cash: number;
  total_equity: number;
  positions: Array<{
    symbol: string;
    quantity: number;
    market_price: number;
    market_value: number;
    unrealized_pnl: number;
  }>;
}

export interface BacktestResult {
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_equity: number;
  cumulative_contributions: number;
  total_capital_invested: number;
  investment_profit: number;
  equity_curve: EquityPoint[];
  orders: Array<{
    order_id: string;
    signal_date: string;
    date: string;
    symbol: string;
    side: string;
    quantity: number;
    execution_price: number;
    status: string;
  }>;
  fills: Array<{
    order_id: string;
    date: string;
    symbol: string;
    side: string;
    quantity: number;
    price: number;
  }>;
  trades: Array<{
    symbol: string;
    entry_date: string;
    exit_date: string;
    entry_price: number;
    exit_price: number;
    quantity: number;
    pnl: number;
    pnl_pct: number;
    holding_period: number;
  }>;
  positions: PositionSnapshot[];
  allocation_history: Array<{
    date: string;
    symbol: string;
    target_weight: number;
    actual_weight: number;
  }>;
}

export interface BacktestRun {
  backtest_run_id: string;
  strategy_id: string;
  strategy_version_id: string;
  created_at: string;
  strategy_version_content_hash: string;
  backtest_result: BacktestResult;
  performance_analysis: PerformanceAnalysis;
  provenance: Record<string, unknown>;
}

export type ReportAvailability = "available" | "not_available" | "not_evaluable";

export interface ContributionScheduleReport {
  enabled: boolean;
  frequency: ContributionFrequency | null;
  amount: string | null;
  requested_date: string | null;
  currency: "USD";
  requested_date_semantics: "month_start" | "explicit_date" | null;
}

export interface ContributionReportEvent {
  sequence: number;
  requested_date: string;
  effective_date: string;
  amount: string;
  currency: "USD";
  frequency: ContributionFrequency;
  source: "ContributionEvent";
  strategy_signal: false;
  deployment: {
    cause: "contribution";
    status: "rebalance_executed" | "no_contribution_rebalance_execution";
    order_count: number;
    fill_count: number;
    symbols: string[];
  };
}

export interface ContributionReport {
  status: ReportAvailability;
  reason?: string;
  schedule: ContributionScheduleReport | null;
  event_count: number;
  events: ContributionReportEvent[];
  integrity: {
    status: "consistent" | "inconsistent" | "not_available";
    event_amount_total: string | null;
    external_cash_flow_total?: string | null;
    cumulative_contributions: number | null;
  };
}

export interface StrategyProvenanceRecord {
  signal_date: string;
  matched_rule_id: string | null;
  allocation_source: string;
  target_allocation: Record<string, number>;
  execution_date: string | null;
  execution_status: string;
  omission_reason: string | null;
}

export interface StrategyTimelineMarker {
  date: string;
  marker_type: "signal" | "execution";
  signal_date?: string | null;
  allocation_source?: string | null;
  matched_rule_id?: string | null;
  target_allocation?: Record<string, number>;
  execution_date?: string | null;
  execution_status?: string | null;
  omission_reason?: string | null;
  rebalance_cause?: string | null;
  order_count?: number;
  fill_count?: number;
  symbols?: string[];
  sides?: string[];
}

export interface StrategyProvenanceReport {
  status?: ReportAvailability;
  reason?: string;
  source?: string;
  record_count?: number;
  allocation_sources?: Record<string, number>;
  submitted_count?: number;
  omitted_count?: number;
  records?: StrategyProvenanceRecord[];
  markers?: StrategyTimelineMarker[];
}

export interface AllocationTimelinePoint {
  date: string;
  asset_weights: Record<string, number>;
  target_asset_weights?: Record<string, number>;
  cash_weight: number;
  allocation_source?: string | null;
  matched_rule_id?: string | null;
}

export interface AllocationTimeline {
  status?: ReportAvailability;
  reason?: string;
  source?: string;
  record_count?: number;
  timeline?: AllocationTimelinePoint[];
  asset_symbols?: string[];
}

export interface ReportAllocations {
  target?: AllocationTimeline;
  actual?: AllocationTimeline;
  cash_semantics?: string;
}

export type HoldingStatus = "OPEN" | "CLOSED";
export type HoldingFilterStatus = HoldingStatus | "ALL";
export type HoldingSort = "entry_date" | "exit_date" | "symbol" | "holding_return" | "pnl" | "duration";

export interface HoldingMetricAvailability {
  status: "available" | "not_available" | "not_evaluable";
  value: number | null;
  reason: string | null;
}

export interface HoldingReportItem {
  holding_id: string;
  lot_id: string;
  symbol: string;
  status: HoldingStatus;
  quantity: number;
  entry_fill_id: string;
  entry_signal_date: string | null;
  entry_execution_date: string;
  entry_price: number;
  entry_execution_cause: string;
  entry_matched_rule_id: string | null;
  entry_allocation_source: string | null;
  exit_fill_id: string | null;
  exit_signal_date: string | null;
  exit_execution_date: string | null;
  exit_price: number | null;
  exit_execution_cause: string | null;
  exit_matched_rule_id: string | null;
  exit_allocation_source: string | null;
  report_end_date: string | null;
  ending_price: number | null;
  market_value: number | null;
  holding_days: number;
  holding_days_basis: "calendar_days";
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  pnl: number;
  pnl_type: "realized" | "unrealized";
  holding_return: number;
}

export interface HoldingReport {
  report_schema_version: "1.0";
  identity: { backtest_run_id: string; strategy_version_id: string };
  status: ReportAvailability;
  source?: string;
  reason?: string;
  summary: { open_count: number | null; closed_count: number | null };
  total: number;
  limit: number;
  offset: number;
  filters: { status: HoldingFilterStatus; symbol: string | null };
  sort: { by: HoldingSort; order: "asc" | "desc" };
  duration_basis: string;
  metric_availability: Record<"mfe" | "mae" | "holding_drawdown", HoldingMetricAvailability>;
  items: HoldingReportItem[];
}

export interface BacktestReport {
  report_schema_version: "1.0";
  identity: {
    backtest_run_id: string;
    strategy_id: string;
    strategy_version_id: string;
    strategy_version_content_hash: string;
    created_at: string;
    engine_version: string;
  };
  availability: Record<string, ReportAvailability>;
  summary_period: { start_date: string; end_date: string };
  summary: {
    account?: { ending_value?: number; unit?: string };
    strategy_performance?: Record<string, unknown>;
    benchmark?: {
      status?: ReportAvailability;
      ending_value?: MetricValue;
      provenance?: { same_contribution_schedule?: boolean; [key: string]: unknown };
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  capital: {
    initial_capital: number;
    cumulative_contributions: number;
    total_capital_invested: number | null;
  };
  profit: { investment_profit: number | null };
  performance: { twr_total_return?: MetricValue; [key: string]: unknown };
  investor_experience: { xirr?: MetricValue; xirr_unit?: string; [key: string]: unknown };
  series_metadata: Record<string, unknown>;
  contributions: Array<Record<string, unknown>>;
  contribution_report: ContributionReport;
  strategy_provenance: StrategyProvenanceReport;
  allocations: ReportAllocations;
  holdings: Record<string, unknown>;
  trades: Record<string, unknown>;
  configuration: Record<string, unknown>;
  provenance: Record<string, unknown>;
}

export type BacktestReportSeriesName =
  | "equity"
  | "capital"
  | "twr"
  | "drawdown"
  | "benchmark"
  | "benchmark_twr"
  | "benchmark_drawdown";

export interface BacktestReportSeriesPayload {
  status?: ReportAvailability;
  reason?: string | null;
  points?: Array<{ date: string; value: number }>;
  unit?: string;
}

export interface BacktestReportSeries {
  report_schema_version: "1.0";
  identity: { backtest_run_id: string; strategy_version_id: string };
  summary_period: { start_date: string; end_date: string };
  window: { from: string | null; to: string | null };
  series: Partial<Record<BacktestReportSeriesName, BacktestReportSeriesPayload>>;
}

export type ResearchSortBy =
  | "created_at"
  | "cagr"
  | "sharpe_ratio"
  | "sortino_ratio"
  | "max_drawdown"
  | "total_return"
  | "annualized_volatility"
  | "calmar_ratio"
  | "win_rate"
  | "profit_factor"
  | "average_trade_return"
  | "best_trade"
  | "worst_trade"
  | "average_holding_period"
  | "turnover";

export interface ResearchMetrics {
  total_return: MetricValue;
  cagr: MetricValue;
  annualized_volatility: MetricValue;
  sharpe_ratio: MetricValue;
  sortino_ratio: MetricValue;
  max_drawdown: MetricValue;
  calmar_ratio: MetricValue;
  win_rate: MetricValue;
  profit_factor: MetricValue;
  average_trade_return: MetricValue;
  best_trade: MetricValue;
  worst_trade: MetricValue;
  average_holding_period: MetricValue;
  turnover: MetricValue;
}

export interface ResearchBacktestSummary {
  backtest_run_id: string;
  strategy_id: string;
  strategy_version_id: string;
  strategy_version_content_hash: string;
  created_at: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_equity: number;
  price_field_used: PriceField;
  engine_version: string;
  analysis_version: string;
  configuration_snapshot: Record<string, unknown>;
  data_snapshot_reference: Record<string, unknown>;
  provenance: Record<string, unknown>;
  metrics: ResearchMetrics;
  metadata_projection_version?: string;
  metadata_projection_status?: "available" | "missing";
  result_available?: boolean;
  experiment_id?: string | null;
  candidate_id?: string | null;
  candidate_index?: number | null;
}

export interface ResearchBacktestList {
  items: ResearchBacktestSummary[];
  total: number;
  limit: number;
  offset: number;
  sort_by: ResearchSortBy;
  order: "asc" | "desc";
}

export type ComparisonCompatibilityStatus = "COMPARABLE" | "WARNING" | "INCOMPATIBLE" | "UNKNOWN";

export interface ComparisonCompatibility {
  status: ComparisonCompatibilityStatus;
  reason_codes: string[];
  human_readable_reasons: string[];
  dimensions: Record<string, unknown>;
}

export interface ComparisonRunIdentity {
  backtest_run_id: string;
  strategy_id: string;
  strategy_version_id: string;
  strategy_name: string;
  strategy_version: string;
  short_display_label: string;
  start_date: string;
  end_date: string;
  initial_capital?: number;
  final_equity?: number;
  total_capital_invested?: number;
  investment_profit?: number;
  asset_universe: string[];
  metrics_status: "included" | "excluded";
  metrics: Record<string, ComparisonMetricValue>;
}

export interface ComparisonMetricValue {
  value: unknown;
  status: "available" | "not_available" | "not_evaluable";
  reason: string | null;
}

export interface ComparisonSeriesCapability {
  status: "available" | "not_available" | "excluded";
  reason?: string;
  unit?: string;
  source?: string;
  normalization?: Record<string, unknown>;
  fair_comparison_requires?: string;
  points: Array<{ date: string; value: number }>;
}

export interface ComparisonSeries {
  backtest_run_id: string;
  strategy_version_id: string;
  start_date: string;
  end_date: string;
  twr: ComparisonSeriesCapability;
  drawdown: ComparisonSeriesCapability;
  portfolio_value: ComparisonSeriesCapability;
  capital_invested?: ComparisonSeriesCapability;
  investment_profit?: ComparisonSeriesCapability;
}

export interface ResearchComparison {
  comparison_schema_version: "2.0" | "2.1";
  ordering: "request_order";
  include: {
    twr: boolean;
    drawdown: boolean;
    portfolio_value: boolean;
    capital_invested?: boolean;
    investment_profit?: boolean;
    metrics: boolean;
  };
  compatibility: { twr: ComparisonCompatibility } & Record<string, ComparisonCompatibility>;
  runs: ComparisonRunIdentity[];
  series: ComparisonSeries[];
  provenance_notice: string;
}

export type ResearchProtocolStatus =
  | "draft"
  | "frozen"
  | "is_evaluated"
  | "selection_recorded"
  | "oos_evaluated"
  | "closed";

export interface ResearchEvaluationConfig {
  price_field_used: PriceField;
  initial_capital: number;
  commission: CommissionRequest;
  slippage: number;
  execution_rule: "next_trading_day_open";
  fractional_shares: boolean;
  rebalance_policy: {
    frequency: "daily" | "weekly" | "monthly" | "on_signal_change";
    threshold: number | null;
  };
  engine_version: string;
  contribution_schedule?: ContributionScheduleRequest | null;
}

export interface ResearchProtocol {
  protocol_id: string;
  protocol_version: number;
  created_at: string;
  is_start_date: string;
  is_end_date: string;
  oos_start_date: string;
  oos_end_date: string;
  split_type: "holdout";
  split_policy: string;
  timezone: string;
  gap_days: number;
  embargo_days: number;
  selection_rules: string[];
  allowed_metrics: string[];
  forbidden_actions: string[];
  strategy_freeze_required: true;
  data_policy: Record<string, unknown>;
  execution_policy: Record<string, unknown>;
  evaluation_policy: Record<string, unknown>;
  evaluation_config: ResearchEvaluationConfig | null;
  provenance: Record<string, unknown>;
  status: ResearchProtocolStatus;
}

export interface CandidateSet {
  candidate_set_id: string;
  protocol_id: string;
  strategy_version_ids: string[];
  strategy_version_content_hashes: Record<string, string>;
  created_at: string;
  status: "open" | "locked";
}

export interface SelectionDecision {
  decision_id: string;
  protocol_id: string;
  candidate_set_id: string;
  selected_strategy_version_id: string;
  is_backtest_run_ids: string[];
  selected_metrics: Record<string, unknown>;
  rationale: string;
  created_at: string;
  data_provenance: Record<string, unknown>;
  source: "human";
}

export interface StrategyFreezeRecord {
  freeze_id: string;
  protocol_id: string;
  strategy_version_id: string;
  strategy_version_content_hash: string;
  selection_decision_id: string;
  frozen_at: string;
  reason: string;
}

export interface OOSEvaluationRecord {
  evaluation_id: string;
  protocol_id: string;
  freeze_id: string;
  strategy_version_id: string;
  backtest_run_id: string;
  status: "planned" | "observed" | "sealed";
  observed_at: string | null;
  created_at: string;
  provenance: Record<string, unknown>;
  untouched_oos: boolean;
}

export interface ResearchProtocolDetail {
  protocol: ResearchProtocol;
  candidate_sets: CandidateSet[];
  selections: SelectionDecision[];
  freezes: StrategyFreezeRecord[];
  oos_evaluations: OOSEvaluationRecord[];
  data_provenance_notice: string;
}

export interface ResearchProtocolCreateRequest {
  is_start_date: string;
  is_end_date: string;
  oos_start_date: string;
  oos_end_date: string;
  gap_days: number;
  embargo_days: number;
  selection_rules: string[];
  allowed_metrics: string[];
  evaluation_config: ResearchEvaluationConfig;
}

export type BacktestVersion = StrategyVersionSummary;
