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
}

export interface ResearchBacktestList {
  items: ResearchBacktestSummary[];
  total: number;
  limit: number;
  offset: number;
  sort_by: ResearchSortBy;
  order: "asc" | "desc";
}

export interface ComparisonIncompatibility {
  code: string;
  field: string;
  reference_backtest_run_id: string;
  reference_value: unknown;
  values: Array<{ backtest_run_id: string; value: unknown }>;
}

export interface ComparisonSeries {
  backtest_run_id: string;
  equity_curve: Array<{ date: string; total_equity: number }>;
  drawdown_curve: DrawdownPoint[];
}

export interface ResearchComparison {
  comparable: boolean;
  incompatibility_reasons: ComparisonIncompatibility[];
  runs: ResearchBacktestSummary[];
  series: ComparisonSeries[];
  provenance_notice: string;
}

export type BacktestVersion = StrategyVersionSummary;
