"""Benchmark evaluation through the canonical PHASE 3 backtest engine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from analytics.models import MetricValue, PerformanceAnalysisResult
from analytics.performance import analyze_backtest
from backtest import BacktestConfig, BacktestEngine, BacktestResult, TargetAllocation
from backtest.exceptions import BacktestError
from data.models import HistoricalDataSet


@dataclass(frozen=True)
class BenchmarkEvaluation:
    """Immutable benchmark artifact paired with one frozen backtest configuration."""

    benchmark_symbol: str
    status: str
    backtest_result: BacktestResult | None
    performance_analysis: PerformanceAnalysisResult | None
    provenance: Mapping[str, Any]
    identity_hash: str
    reason: str | None = None

    def __post_init__(self) -> None:
        symbol = self.benchmark_symbol.strip().upper()
        if not symbol:
            raise ValueError("benchmark_symbol must not be empty")
        if self.status not in {"available", "not_evaluable"}:
            raise ValueError("benchmark status is invalid")
        if self.status == "available":
            if self.backtest_result is None or self.performance_analysis is None:
                raise ValueError("available benchmark requires result and analysis")
            if self.reason is not None:
                raise ValueError("available benchmark must not have a reason")
        elif not self.reason:
            raise ValueError("not-evaluable benchmark requires a reason")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("benchmark provenance must be a mapping")
        if not isinstance(self.identity_hash, str) or not self.identity_hash.strip():
            raise ValueError("benchmark identity_hash must not be empty")
        object.__setattr__(self, "benchmark_symbol", symbol)
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        object.__setattr__(self, "identity_hash", self.identity_hash.strip())

    @property
    def ending_value(self) -> MetricValue:
        if self.backtest_result is None:
            return MetricValue.not_evaluable(self.reason or "benchmark is not evaluable")
        return MetricValue.available(self.backtest_result.final_equity)

    def to_dict(self) -> dict[str, Any]:
        analysis = self.performance_analysis
        return {
            "benchmark_symbol": self.benchmark_symbol,
            "status": self.status,
            "reason": self.reason,
            "identity_hash": self.identity_hash,
            "provenance": dict(self.provenance),
            "ending_value": self.ending_value.to_dict(),
            "performance": (
                {
                    "twr_total_return": analysis.total_return.to_dict(),
                    "cagr": analysis.cagr.to_dict(),
                    "max_drawdown": analysis.max_drawdown.to_dict(),
                    "twr_wealth_curve": [item.to_dict() for item in analysis.twr_wealth_curve],
                    "drawdown_curve": [item.to_dict() for item in analysis.drawdown_curve],
                }
                if analysis is not None
                else None
            ),
        }


class BenchmarkEvaluationService:
    """Evaluate a benchmark with the exact owner run execution semantics."""

    def __init__(self, engine: BacktestEngine | None = None) -> None:
        self._engine = engine or BacktestEngine()

    def evaluate(
        self,
        benchmark_symbol: str,
        data: HistoricalDataSet,
        config: BacktestConfig,
    ) -> BenchmarkEvaluation:
        if not isinstance(data, HistoricalDataSet):
            raise TypeError("data must be a HistoricalDataSet")
        if not isinstance(config, BacktestConfig):
            raise TypeError("config must be a BacktestConfig")
        symbol = benchmark_symbol.strip().upper()
        if symbol != data.request.symbol:
            raise ValueError("benchmark symbol must match the supplied data set")
        provenance = {
            "source": "BacktestEngine",
            "benchmark_symbol": symbol,
            "price_field_used": config.price_field_used.value,
            "execution_rule": config.execution_rule.value,
            "same_initial_capital": True,
            "same_contribution_schedule": True,
            "requested_start_date": config.start_date.isoformat(),
            "requested_end_date": config.end_date.isoformat(),
            "data_source": data.source.value,
            "data_request": data.request.cache_identity(),
        }
        identity_payload = {
            "benchmark_symbol": symbol,
            "configuration": config.snapshot({"benchmark": data.request.cache_identity()}),
            "data_request": data.request.cache_identity(),
        }
        identity_hash = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            if not data.points:
                raise ValueError("benchmark data set is empty")
            allocation = TargetAllocation.from_weights(data.points[0].date, {symbol: 1.0})
            result = self._engine.run({symbol: data}, (allocation,), config)
            analysis = analyze_backtest(result)
        except (BacktestError, ValueError, RuntimeError) as exc:
            return BenchmarkEvaluation(
                benchmark_symbol=symbol,
                status="not_evaluable",
                backtest_result=None,
                performance_analysis=None,
                provenance=provenance,
                identity_hash=identity_hash,
                reason=str(exc),
            )
        return BenchmarkEvaluation(
            benchmark_symbol=symbol,
            status="available",
            backtest_result=result,
            performance_analysis=analysis,
            provenance=provenance,
            identity_hash=identity_hash,
        )


__all__ = ["BenchmarkEvaluation", "BenchmarkEvaluationService"]
