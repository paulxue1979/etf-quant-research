"""Pure performance analytics for completed backtest results."""

from analytics.benchmark import BenchmarkEvaluation, BenchmarkEvaluationService
from analytics.exceptions import AnalyticsError, AnalyticsInputError
from analytics.models import (
    DrawdownPoint,
    ExposurePoint,
    MetricStatus,
    MetricValue,
    PerformanceAnalysisResult,
    TradeMetrics,
    WealthPoint,
)
from analytics.performance import (
    PerformanceAnalyticsConfig,
    analyze_backtest,
    flow_adjusted_wealth_curve,
)

__all__ = [
    "AnalyticsError",
    "AnalyticsInputError",
    "DrawdownPoint",
    "ExposurePoint",
    "MetricStatus",
    "MetricValue",
    "PerformanceAnalysisResult",
    "PerformanceAnalyticsConfig",
    "TradeMetrics",
    "WealthPoint",
    "BenchmarkEvaluation",
    "BenchmarkEvaluationService",
    "analyze_backtest",
    "flow_adjusted_wealth_curve",
]
