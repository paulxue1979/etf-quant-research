"""Immutable runtime contracts for PHASE 4C condition evaluation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from data.models import HistoricalDataSet, PriceField
from indicators.models import IndicatorKind, IndicatorSeries
from strategies.enums import ComparisonOperator, LogicalOperator, OperandType
from strategies.exceptions import InvalidEvaluationValueError
from strategies.models import AssetReference, Threshold


@dataclass(frozen=True)
class IndicatorKey:
    """Asset-qualified identifier for one PHASE 2 indicator series."""

    asset: AssetReference | str
    kind: IndicatorKind
    period: int
    price_field: PriceField

    def __post_init__(self) -> None:
        normalized_asset = (
            self.asset if isinstance(self.asset, AssetReference) else AssetReference(self.asset)
        )
        if not isinstance(self.kind, IndicatorKind):
            object.__setattr__(self, "kind", IndicatorKind(self.kind))
        if isinstance(self.period, bool) or not isinstance(self.period, int) or self.period <= 0:
            raise ValueError("indicator period must be a positive integer")
        if not isinstance(self.price_field, PriceField):
            object.__setattr__(self, "price_field", PriceField(self.price_field))
        object.__setattr__(self, "asset", normalized_asset)

    @property
    def symbol(self) -> str:
        return self.asset.symbol

    @classmethod
    def from_series(
        cls, asset: AssetReference | str, series: IndicatorSeries
    ) -> IndicatorKey:
        return cls(asset, series.kind, series.period, series.price_field_used)


@dataclass(frozen=True)
class EvaluationContext:
    """Read-only market and indicator inputs prepared by the caller."""

    market_data: Mapping[str, HistoricalDataSet]
    indicators: Mapping[IndicatorKey, IndicatorSeries]

    def __post_init__(self) -> None:
        normalized_market: dict[str, HistoricalDataSet] = {}
        for symbol, dataset in self.market_data.items():
            normalized_symbol = symbol.strip().upper()
            if not isinstance(dataset, HistoricalDataSet):
                raise TypeError("market_data values must be HistoricalDataSet values")
            if normalized_symbol != dataset.request.symbol:
                raise ValueError("market data key must match the dataset symbol")
            normalized_market[normalized_symbol] = dataset

        normalized_indicators: dict[IndicatorKey, IndicatorSeries] = {}
        for key, series in self.indicators.items():
            if not isinstance(key, IndicatorKey):
                raise TypeError("indicator keys must be IndicatorKey values")
            if not isinstance(series, IndicatorSeries):
                raise TypeError("indicator values must be IndicatorSeries values")
            expected = IndicatorKey.from_series(key.asset, series)
            if expected != key:
                raise ValueError("indicator key does not match the indicator series")
            normalized_indicators[key] = series

        object.__setattr__(self, "market_data", MappingProxyType(normalized_market))
        object.__setattr__(self, "indicators", MappingProxyType(normalized_indicators))

    @classmethod
    def from_components(
        cls,
        market_data: Mapping[str, HistoricalDataSet],
        indicators: Iterable[tuple[AssetReference | str, IndicatorSeries]] = (),
    ) -> EvaluationContext:
        """Build a context from asset-qualified indicator series."""
        keyed = {
            IndicatorKey.from_series(asset, series): series for asset, series in indicators
        }
        return cls(market_data=market_data, indicators=keyed)


@dataclass(frozen=True)
class OperandValue:
    """One explainable operand value resolved for one date."""

    value: float
    asset: str | None
    operand_type: OperandType
    price_field_used: PriceField | None
    date: date

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("operand result date must be a date")
        if not isinstance(self.operand_type, OperandType):
            object.__setattr__(self, "operand_type", OperandType(self.operand_type))
        if self.price_field_used is not None and not isinstance(
            self.price_field_used, PriceField
        ):
            object.__setattr__(self, "price_field_used", PriceField(self.price_field_used))
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise InvalidEvaluationValueError("evaluated operand value must be numeric")
        if not math.isfinite(float(self.value)):
            raise InvalidEvaluationValueError("evaluated operand value must be finite")
        object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True)
class ConditionResult:
    """Explainable result of evaluating one condition on one date."""

    condition_id: str
    date: date
    passed: bool
    left_operand_result: OperandValue
    right_operand_result: OperandValue
    operator: ComparisonOperator
    threshold: Threshold | None
    effective_right_value: float
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.condition_id, str) or not self.condition_id.strip():
            raise ValueError("condition result condition_id must not be empty")
        if not isinstance(self.date, date):
            raise TypeError("condition result date must be a date")
        if not isinstance(self.left_operand_result, OperandValue):
            raise TypeError("left_operand_result must be an OperandValue")
        if not isinstance(self.right_operand_result, OperandValue):
            raise TypeError("right_operand_result must be an OperandValue")
        if (
            self.left_operand_result.date != self.date
            or self.right_operand_result.date != self.date
        ):
            raise ValueError("condition operand result dates must match condition result date")
        if not isinstance(self.operator, ComparisonOperator):
            object.__setattr__(self, "operator", ComparisonOperator(self.operator))
        if not math.isfinite(float(self.effective_right_value)):
            raise InvalidEvaluationValueError("effective threshold value must be finite")
        if not isinstance(self.passed, bool):
            raise TypeError("condition result passed must be boolean")
        object.__setattr__(self, "effective_right_value", float(self.effective_right_value))


@dataclass(frozen=True)
class RuleGroupResult:
    """Explainable recursive result of evaluating one rule group."""

    rule_group_id: str
    date: date
    operator: LogicalOperator
    passed: bool
    child_results: tuple[ConditionResult | RuleGroupResult, ...]
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.rule_group_id, str) or not self.rule_group_id.strip():
            raise ValueError("rule group result rule_group_id must not be empty")
        if not isinstance(self.date, date):
            raise TypeError("rule group result date must be a date")
        if not isinstance(self.operator, LogicalOperator):
            object.__setattr__(self, "operator", LogicalOperator(self.operator))
        if not isinstance(self.passed, bool):
            raise TypeError("rule group result passed must be boolean")
        if isinstance(self.child_results, (str, bytes)):
            raise TypeError("rule group result child_results must be a sequence")
        results = tuple(self.child_results)
        if not results:
            raise ValueError("rule group result child_results must not be empty")
        if not all(isinstance(item, (ConditionResult, RuleGroupResult)) for item in results):
            raise TypeError("rule group result children must be ConditionResult or RuleGroupResult")
        if not all(item.date == self.date for item in results):
            raise ValueError("rule group result child dates must match result date")
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ValueError("rule group result explanation must not be empty")
        object.__setattr__(self, "child_results", results)


__all__ = [
    "ConditionResult",
    "EvaluationContext",
    "IndicatorKey",
    "OperandValue",
    "RuleGroupResult",
]
