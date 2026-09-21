"""Immutable models returned by the indicator engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from data.models import PriceField, Timeframe


class IndicatorKind(StrEnum):
    """Indicator calculations currently supported by PHASE 2."""

    MOVING_AVERAGE = "ma"
    EXPONENTIAL_MOVING_AVERAGE = "ema"


@dataclass(frozen=True)
class IndicatorPoint:
    """One indicator value aligned to a market-data date."""

    date: date
    value: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date.isoformat(), "value": self.value}


@dataclass(frozen=True)
class IndicatorSeries:
    """A date-aligned, price-field-explicit indicator result."""

    kind: IndicatorKind
    period: int
    price_field_used: PriceField
    points: tuple[IndicatorPoint, ...]
    timeframe: Timeframe = Timeframe.DAILY

    def __post_init__(self) -> None:
        if not isinstance(self.timeframe, Timeframe):
            object.__setattr__(self, "timeframe", Timeframe(self.timeframe))

    @property
    def name(self) -> str:
        """Return a stable identifier suitable for future consumers."""
        return f"{self.kind.value}_{self.period}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "period": self.period,
            "price_field": self.price_field_used.value,
            "timeframe": self.timeframe.value,
            "points": [point.to_dict() for point in self.points],
        }
