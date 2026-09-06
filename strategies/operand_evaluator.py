"""Pure evaluation of strategy operands against prepared runtime context."""

from __future__ import annotations

from datetime import date

from strategies.enums import OperandType
from strategies.evaluation import EvaluationContext, IndicatorKey, OperandValue
from strategies.exceptions import (
    InvalidConditionConfigurationError,
    MissingIndicatorError,
    MissingMarketDataError,
    MissingOperandValueError,
)
from strategies.models import Operand


def evaluate_operand(
    operand: Operand,
    context: EvaluationContext,
    as_of_date: date,
) -> OperandValue:
    """Resolve one operand at exactly ``as_of_date`` without mutating inputs."""
    if not isinstance(operand, Operand):
        raise TypeError("operand must be an Operand value")
    if not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext value")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")

    if operand.operand_type is OperandType.CONSTANT:
        return OperandValue(
            value=operand.value,
            asset=None,
            operand_type=operand.operand_type,
            price_field_used=None,
            date=as_of_date,
        )

    if operand.operand_type is OperandType.PRICE and operand.price_field is None:
        raise InvalidConditionConfigurationError(
            f"PRICE({operand.symbol}) requires an explicit price_field"
        )
    if operand.operand_type in (OperandType.MA, OperandType.EMA) and (
        operand.period is None or operand.price_field is None
    ):
        raise InvalidConditionConfigurationError(
            f"{operand.operand_type.value.upper()}({operand.symbol}) requires period "
            "and price_field"
        )

    if operand.operand_type is OperandType.PRICE:
        if operand.symbol not in context.market_data:
            raise MissingMarketDataError(
                f"market data is missing for {operand.symbol} on {as_of_date.isoformat()}"
            )
        dataset = context.market_data[operand.symbol]
        point = next((item for item in dataset.points if item.date == as_of_date), None)
        if point is None:
            raise MissingOperandValueError(
                f"price is missing for {operand.symbol} on {as_of_date.isoformat()}"
            )
        value = point.price_for(operand.price_field)
        return OperandValue(
            value,
            operand.symbol,
            operand.operand_type,
            operand.price_field,
            as_of_date,
        )

    if operand.operand_type in (OperandType.MA, OperandType.EMA):
        key = IndicatorKey(
            operand.symbol,
            "ma" if operand.operand_type is OperandType.MA else "ema",
            operand.period,
            operand.price_field,
        )
        series = context.indicators.get(key)
        if series is None:
            raise MissingIndicatorError(
                f"{operand.operand_type.value.upper()}({operand.symbol},{operand.period}) "
                f"is missing from the evaluation context"
            )
        point = next((item for item in series.points if item.date == as_of_date), None)
        if point is None or point.value is None:
            raise MissingOperandValueError(
                f"{operand.operand_type.value.upper()}({operand.symbol},{operand.period}) "
                f"is missing on {as_of_date.isoformat()}"
            )
        return OperandValue(
            value=point.value,
            asset=operand.symbol,
            operand_type=operand.operand_type,
            price_field_used=operand.price_field,
            date=as_of_date,
        )

    raise InvalidConditionConfigurationError(
        f"unsupported operand type: {operand.operand_type.value}"
    )


__all__ = ["evaluate_operand"]
