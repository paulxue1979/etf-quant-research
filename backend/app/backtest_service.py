"""Application orchestration for immutable strategy backtest runs."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from analytics.benchmark import BenchmarkEvaluationService
from analytics.performance import analyze_backtest
from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestRepository
from backend.app.strategy_execution_provenance import strategy_execution_provenance
from backend.app.strategy_repository import StrategyRepository
from backtest.integration import run_strategy_backtest
from data.cache import DiskCache
from data.config import TiingoSettings
from data.exceptions import DataEngineError
from data.models import HistoricalDataRequest, PriceField
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from indicators import exponential_moving_average, moving_average
from indicators.models import IndicatorKind
from strategies.evaluation import EvaluationContext
from strategies.strategy_evaluation import evaluate_strategy, required_indicators


class BacktestServiceError(RuntimeError):
    """Raised when a requested backtest cannot be safely completed."""


class BacktestService:
    """Orchestrate existing strategy, data, backtest, and analytics modules."""

    def __init__(
        self,
        *,
        strategy_repository: StrategyRepository,
        backtest_repository: BacktestRepository,
        data_service: HistoricalDataService,
        benchmark_service: BenchmarkEvaluationService | None = None,
    ) -> None:
        self._strategies = strategy_repository
        self._runs = backtest_repository
        self._data = data_service
        self._benchmarks = benchmark_service or BenchmarkEvaluationService()

    def run(self, request: Any) -> BacktestRun:
        """Execute and persist one new run for an immutable strategy version."""
        strategy_version = self._strategies.get(
            request.strategy_id,
            request.strategy_version_id,
        )
        if strategy_version is None:
            raise BacktestServiceError("strategy version was not found")
        strategy = strategy_version.configuration
        if strategy.price_field is not request.price_field_used:
            raise BacktestServiceError(
                "backtest price field must match the strategy version price field"
            )

        config = request.to_config(strategy_version.version_id, strategy.rebalance_policy)
        warmup_start = _warmup_start(request.start_date, strategy_version)
        data = {
            asset.symbol: self._data.get_history(
                HistoricalDataRequest(
                    symbol=asset.symbol,
                    start_date=warmup_start,
                    end_date=request.end_date,
                    price_field_used=request.price_field_used,
                )
            )
            for asset in strategy.assets
        }
        requirements = required_indicators(strategy_version)
        indicators = tuple(
            (
                requirement.asset,
                _calculate_indicator(
                    data[requirement.symbol],
                    requirement.kind,
                    requirement.period,
                    requirement.price_field,
                ),
            )
            for requirement in requirements
        )
        context = EvaluationContext.from_components(data, indicators)
        source_reference = (
            f"historical-data:{warmup_start.isoformat()}:{request.end_date.isoformat()}"
        )
        timeline = evaluate_strategy(
            strategy_version,
            context,
            request.start_date,
            request.end_date,
            source_data_reference=source_reference,
        )
        integration = run_strategy_backtest(strategy_version, timeline, data, config)
        analysis = analyze_backtest(integration.backtest_result)
        benchmark_evaluation = None
        benchmark_symbol = getattr(request, "benchmark_symbol", None)
        if benchmark_symbol is not None:
            required_dates = tuple(point.date for point in integration.backtest_result.equity_curve)
            try:
                benchmark_data = self._data.get_history(
                    HistoricalDataRequest(
                        symbol=benchmark_symbol,
                        start_date=request.start_date,
                        end_date=request.end_date,
                        price_field_used=request.price_field_used,
                    )
                )
            except DataEngineError as exc:
                benchmark = self._benchmarks.unavailable(
                    benchmark_symbol,
                    config,
                    reason=f"benchmark market data unavailable: {type(exc).__name__}",
                    required_dates=required_dates,
                )
            else:
                benchmark = self._benchmarks.evaluate(
                    benchmark_symbol,
                    benchmark_data,
                    config,
                    required_dates=required_dates,
                )
            benchmark_evaluation = benchmark.to_dict()
        provenance = {
            "source": "BacktestService",
            "data_source_reference": source_reference,
            "warmup_start_date": warmup_start.isoformat(),
            "requested_start_date": request.start_date.isoformat(),
            "requested_end_date": request.end_date.isoformat(),
            "effective_start_date": integration.backtest_result.effective_start_date.isoformat(),
            "effective_end_date": integration.backtest_result.effective_end_date.isoformat(),
            "indicator_requirements": [
                {
                    "asset": item.symbol,
                    "kind": item.kind.value,
                    "period": item.period,
                    "price_field": item.price_field.value,
                }
                for item in requirements
            ],
            "benchmark": (
                {
                    "symbol": benchmark_symbol,
                    "status": benchmark_evaluation["status"],
                    "identity_hash": benchmark_evaluation["identity_hash"],
                }
                if benchmark_evaluation is not None
                else None
            ),
        }
        run = BacktestRun.create(
            strategy_id=strategy_version.strategy_id,
            strategy_version_id=strategy_version.version_id,
            strategy_version_content_hash=strategy_version.content_hash or "",
            backtest_result=integration.backtest_result,
            performance_analysis=analysis,
            provenance=provenance,
            strategy_provenance=strategy_execution_provenance(integration.signal_records),
            benchmark_evaluation=benchmark_evaluation,
        )
        return self._runs.create(run)


def create_default_backtest_service() -> BacktestService:
    """Build the local SQLite, Tiingo, and disk-cache backed service."""
    settings = TiingoSettings.from_environment()
    return BacktestService(
        strategy_repository=StrategyRepository(),
        backtest_repository=BacktestRepository(),
        data_service=HistoricalDataService(TiingoClient(settings), DiskCache()),
    )


def _warmup_start(start_date: date, strategy_version: Any) -> date:
    requirements = required_indicators(strategy_version)
    max_period = max((item.period for item in requirements), default=1)
    return start_date - timedelta(days=max_period * 3 + 10)


def _calculate_indicator(
    data: Any, kind: IndicatorKind, period: int, price_field: PriceField
) -> Any:
    if kind is IndicatorKind.MOVING_AVERAGE:
        return moving_average(data, period=period, price_field=price_field)
    if kind is IndicatorKind.EXPONENTIAL_MOVING_AVERAGE:
        return exponential_moving_average(data, period=period, price_field=price_field)
    raise BacktestServiceError(f"unsupported indicator kind: {kind.value}")


__all__ = ["BacktestService", "BacktestServiceError", "create_default_backtest_service"]
