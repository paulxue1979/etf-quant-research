"""Integration boundary from strategy evaluation to the PHASE 3 backtest engine."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from backtest.engine import BacktestEngine
from backtest.exceptions import StrategyBacktestIntegrationError
from backtest.models import BacktestConfig, BacktestResult, TargetAllocation
from data.exceptions import DataValidationError
from data.models import HistoricalDataSet
from data.validation import validate_historical_data
from strategies.enums import StrategyEvaluationStatus
from strategies.models import StrategyVersion
from strategies.signal_engine import StrategySignal
from strategies.strategy_evaluation import StrategyEvaluationTimeline

_WEIGHT_TOLERANCE = 1e-12


@dataclass(frozen=True)
class StrategyBacktestAllocation:
    """One evaluated strategy signal and its backtest scheduling decision."""

    signal: StrategySignal
    target_allocation: TargetAllocation
    execution_date: date | None
    submitted_to_backtest: bool
    omission_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.signal, StrategySignal):
            raise TypeError("signal must be a StrategySignal")
        if not isinstance(self.target_allocation, TargetAllocation):
            raise TypeError("target_allocation must be a TargetAllocation")
        if self.target_allocation.date != self.signal.date:
            raise ValueError("target allocation date must match signal date")
        if self.execution_date is not None and self.execution_date <= self.signal.date:
            raise ValueError("execution date must be after signal date")
        if not isinstance(self.submitted_to_backtest, bool):
            raise TypeError("submitted_to_backtest must be boolean")
        if self.submitted_to_backtest and self.execution_date is None:
            raise ValueError("submitted allocations require an execution date")
        if self.submitted_to_backtest and self.omission_reason is not None:
            raise ValueError("submitted allocations must not have an omission reason")
        if not self.submitted_to_backtest and not self.omission_reason:
            raise ValueError("omitted allocations require an omission reason")


@dataclass(frozen=True)
class StrategyBacktestResult:
    """Backtest result plus the immutable strategy-to-execution provenance."""

    strategy_id: str
    strategy_version_id: str
    signal_records: tuple[StrategyBacktestAllocation, ...]
    evaluation_timeline: StrategyEvaluationTimeline
    backtest_result: BacktestResult

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be empty")
        if not isinstance(self.evaluation_timeline, StrategyEvaluationTimeline):
            raise TypeError("evaluation_timeline must be a StrategyEvaluationTimeline")
        if not isinstance(self.backtest_result, BacktestResult):
            raise TypeError("backtest_result must be a BacktestResult")
        records = tuple(self.signal_records)
        if not all(isinstance(item, StrategyBacktestAllocation) for item in records):
            raise TypeError("signal_records must contain StrategyBacktestAllocation values")
        if self.evaluation_timeline.strategy_version_id != self.strategy_version_id:
            raise ValueError("timeline strategy version must match result strategy version")
        if self.backtest_result.strategy_version_id != self.strategy_version_id:
            raise ValueError("backtest strategy version must match result strategy version")
        dates = tuple(item.signal.date for item in records)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise ValueError("signal records must be strictly date-ordered")
        object.__setattr__(self, "strategy_id", self.strategy_id.strip())
        object.__setattr__(self, "strategy_version_id", self.strategy_version_id.strip())
        object.__setattr__(self, "signal_records", records)

    @property
    def submitted_allocations(self) -> tuple[TargetAllocation, ...]:
        """Return only target allocations handed to PHASE 3."""
        return tuple(
            item.target_allocation
            for item in self.signal_records
            if item.submitted_to_backtest
        )

    @property
    def omitted_allocations(self) -> tuple[StrategyBacktestAllocation, ...]:
        """Return evaluated signals intentionally not submitted for execution."""
        return tuple(item for item in self.signal_records if not item.submitted_to_backtest)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic provenance view without duplicating accounting data."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_version_id": self.strategy_version_id,
            "signal_records": [
                {
                    "signal": item.signal.to_dict(),
                    "target_allocation": dict(item.target_allocation.as_mapping()),
                    "execution_date": (
                        item.execution_date.isoformat() if item.execution_date is not None else None
                    ),
                    "submitted_to_backtest": item.submitted_to_backtest,
                    "omission_reason": item.omission_reason,
                }
                for item in self.signal_records
            ],
            "evaluation_timeline": self.evaluation_timeline.to_dict(),
            "backtest_result": {
                "strategy_version_id": self.backtest_result.strategy_version_id,
                "start_date": self.backtest_result.start_date.isoformat(),
                "end_date": self.backtest_result.end_date.isoformat(),
                "final_equity": self.backtest_result.final_equity,
            },
        }


def target_allocations_from_timeline(
    strategy_version: StrategyVersion,
    evaluation_timeline: StrategyEvaluationTimeline,
    data: Mapping[str, HistoricalDataSet],
    config: BacktestConfig,
) -> tuple[StrategyBacktestAllocation, ...]:
    """Convert evaluated signals into PHASE 3 allocations without evaluating again."""
    _validate_integration_inputs(strategy_version, evaluation_timeline, data, config)
    trading_dates = _common_trading_dates(data, config)
    next_date = {
        trading_dates[index]: trading_dates[index + 1]
        for index in range(len(trading_dates) - 1)
    }

    records: list[StrategyBacktestAllocation] = []
    saw_evaluated = False
    for evaluation in evaluation_timeline.evaluations:
        if evaluation.status is StrategyEvaluationStatus.ERROR:
            failure = evaluation.failure
            code = failure.code if failure is not None else "UNKNOWN_ERROR"
            raise StrategyBacktestIntegrationError(
                f"strategy evaluation ERROR on {evaluation.date.isoformat()}: {code}"
            )
        if evaluation.status is StrategyEvaluationStatus.NOT_EVALUABLE:
            if saw_evaluated:
                failure = evaluation.failure
                code = failure.code if failure is not None else "NOT_EVALUABLE"
                raise StrategyBacktestIntegrationError(
                    "strategy became NOT_EVALUABLE after an evaluated signal on "
                    f"{evaluation.date.isoformat()}: {code}"
                )
            continue
        if evaluation.status is not StrategyEvaluationStatus.EVALUATED:
            raise StrategyBacktestIntegrationError(
                f"unsupported strategy evaluation status on {evaluation.date.isoformat()}"
            )
        saw_evaluated = True
        signal = evaluation.signal
        allocation_result = evaluation.target_allocation
        if signal is None or allocation_result is None:
            raise StrategyBacktestIntegrationError(
                f"evaluated strategy result on {evaluation.date.isoformat()} has no signal"
            )
        if signal.strategy_version_id != strategy_version.version_id:
            raise StrategyBacktestIntegrationError(
                f"signal on {evaluation.date.isoformat()} has the wrong strategy version"
            )
        if signal.price_field_used is not config.price_field_used:
            raise StrategyBacktestIntegrationError(
                f"signal on {evaluation.date.isoformat()} uses {signal.price_field_used.value}; "
                f"backtest requires {config.price_field_used.value}"
            )
        if evaluation.date not in trading_dates:
            raise StrategyBacktestIntegrationError(
                f"signal date {evaluation.date.isoformat()} is not a common trading date"
            )
        _validate_target_symbols(allocation_result.weights, strategy_version, evaluation.date)
        target = TargetAllocation.from_weights(evaluation.date, allocation_result.weights)
        execution_date = next_date.get(evaluation.date)
        if execution_date is None:
            records.append(
                StrategyBacktestAllocation(
                    signal=signal,
                    target_allocation=target,
                    execution_date=None,
                    submitted_to_backtest=False,
                    omission_reason="no next trading day exists within the backtest data",
                )
            )
        else:
            records.append(
                StrategyBacktestAllocation(
                    signal=signal,
                    target_allocation=target,
                    execution_date=execution_date,
                    submitted_to_backtest=True,
                )
            )
    return tuple(records)


def run_strategy_backtest(
    strategy_version: StrategyVersion,
    evaluation_timeline: StrategyEvaluationTimeline,
    data: Mapping[str, HistoricalDataSet],
    config: BacktestConfig,
    *,
    engine: BacktestEngine | None = None,
) -> StrategyBacktestResult:
    """Run PHASE 3 using target allocations emitted by PHASE 4G."""
    records = target_allocations_from_timeline(
        strategy_version, evaluation_timeline, data, config
    )
    backtest = (engine or BacktestEngine()).run(
        data,
        tuple(item.target_allocation for item in records if item.submitted_to_backtest),
        config,
    )
    return StrategyBacktestResult(
        strategy_id=strategy_version.strategy_id,
        strategy_version_id=strategy_version.version_id,
        signal_records=records,
        evaluation_timeline=evaluation_timeline,
        backtest_result=backtest,
    )


def _validate_integration_inputs(
    strategy_version: StrategyVersion,
    evaluation_timeline: StrategyEvaluationTimeline,
    data: Mapping[str, HistoricalDataSet],
    config: BacktestConfig,
) -> None:
    if not isinstance(strategy_version, StrategyVersion):
        raise StrategyBacktestIntegrationError("strategy_version must be a StrategyVersion")
    if not isinstance(evaluation_timeline, StrategyEvaluationTimeline):
        raise StrategyBacktestIntegrationError(
            "evaluation_timeline must be a StrategyEvaluationTimeline"
        )
    if not isinstance(config, BacktestConfig):
        raise StrategyBacktestIntegrationError("config must be a BacktestConfig")
    if not isinstance(data, Mapping) or not data:
        raise StrategyBacktestIntegrationError("data must be a non-empty mapping")
    strategy = strategy_version.configuration
    if evaluation_timeline.strategy_version_id != strategy_version.version_id:
        raise StrategyBacktestIntegrationError("timeline strategy version does not match strategy")
    if config.strategy_version_id != strategy_version.version_id:
        raise StrategyBacktestIntegrationError(
            "backtest config strategy version does not match strategy"
        )
    if (
        evaluation_timeline.start_date != config.start_date
        or evaluation_timeline.end_date != config.end_date
    ):
        raise StrategyBacktestIntegrationError(
            "evaluation timeline bounds must match backtest config bounds"
        )
    if config.price_field_used is not strategy.price_field:
        raise StrategyBacktestIntegrationError(
            f"strategy uses {strategy.price_field.value}; backtest requires "
            f"{config.price_field_used.value}"
        )
    if config.rebalance_policy.frequency.value != strategy.rebalance_policy.frequency.value:
        raise StrategyBacktestIntegrationError("strategy and backtest rebalance frequencies differ")
    if not _same_optional_float(
        config.rebalance_policy.threshold, strategy.rebalance_policy.threshold
    ):
        raise StrategyBacktestIntegrationError("strategy and backtest rebalance thresholds differ")

    expected_symbols = {asset.symbol for asset in strategy.assets}
    supplied_symbols = {symbol.strip().upper() for symbol in data}
    if supplied_symbols != expected_symbols:
        missing = sorted(expected_symbols - supplied_symbols)
        extra = sorted(supplied_symbols - expected_symbols)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise StrategyBacktestIntegrationError(
            "market data symbols must match strategy assets: " + "; ".join(details)
        )
    for supplied_symbol, dataset in data.items():
        symbol = supplied_symbol.strip().upper()
        if not isinstance(dataset, HistoricalDataSet):
            raise StrategyBacktestIntegrationError(f"market data for {symbol} is invalid")
        if dataset.request.symbol != symbol:
            raise StrategyBacktestIntegrationError(
                f"market data key {supplied_symbol!r} does not match {dataset.request.symbol!r}"
            )
        if dataset.request.price_field_used is not config.price_field_used:
            raise StrategyBacktestIntegrationError(
                f"market data for {symbol} uses {dataset.request.price_field_used.value}; "
                f"backtest requires {config.price_field_used.value}"
            )
        try:
            validate_historical_data(dataset)
        except DataValidationError as exc:
            raise StrategyBacktestIntegrationError(
                f"market data for {symbol} is invalid: {exc}"
            ) from exc


def _common_trading_dates(
    data: Mapping[str, HistoricalDataSet], config: BacktestConfig
) -> tuple[date, ...]:
    date_sets = [
        {
            point.date
            for point in dataset.points
            if config.start_date <= point.date <= config.end_date
        }
        for dataset in data.values()
    ]
    common = set.intersection(*date_sets)
    dates = tuple(sorted(common))
    if len(dates) < 2:
        raise StrategyBacktestIntegrationError(
            "at least two common trading dates are required for next-open execution"
        )
    return dates


def _validate_target_symbols(
    weights: Mapping[str, float], strategy_version: StrategyVersion, as_of_date: date
) -> None:
    declared = {asset.symbol for asset in strategy_version.configuration.assets}
    unknown = set(weights) - declared
    if unknown:
        raise StrategyBacktestIntegrationError(
            f"target allocation on {as_of_date.isoformat()} references undeclared assets: "
            + ", ".join(sorted(unknown))
        )
    for symbol, weight in weights.items():
        if (
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or weight < 0
        ):
            raise StrategyBacktestIntegrationError(
                f"target allocation weight for {symbol} is invalid"
            )


def _same_optional_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE)


__all__ = [
    "StrategyBacktestAllocation",
    "StrategyBacktestResult",
    "run_strategy_backtest",
    "target_allocations_from_timeline",
]
