"""Immutable models returned by the indicator engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from data.models import PriceField


class IndicatorKind(StrEnum):
    """Indicator calculations currently supported by PHASE 2."""

    MOVING_AVERAGE = "ma"
    EXPONENTIAL_MOVING_AVERAGE = "ema"


@dataclass(frozen=True)
class IndicatorPoint:
    """One indicator value aligned to a market-data date."""

    date: date
    value: float | None


@dataclass(frozen=True)
class IndicatorSeries:
    """A date-aligned, price-field-explicit indicator result."""

    kind: IndicatorKind
    period: int
    price_field_used: PriceField
    points: tuple[IndicatorPoint, ...]

    @property
    def name(self) -> str:
        """Return a stable identifier suitable for future consumers."""
        return f"{self.kind.value}_{self.period}"
