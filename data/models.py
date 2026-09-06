"""Typed internal data models for EOD market data.

The data engine retains both raw and adjusted prices. Consumers must explicitly
carry a PriceField, rather than relying on an implicit close-price convention.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Any

DATA_SCHEMA_VERSION = "1"
CACHE_FORMAT_VERSION = 1
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class PriceField(StrEnum):
    """Supported close-price conventions for downstream consumers."""

    RAW_CLOSE = "raw_close"
    ADJUSTED_CLOSE = "adjusted_close"


class DataSource(StrEnum):
    """Origin of the returned normalized data set."""

    API_FRESH = "api_fresh"
    CACHE = "cache"


@dataclass(frozen=True)
class HistoricalDataRequest:
    """A cacheable request for daily EOD historical data."""

    symbol: str
    start_date: date
    end_date: date
    frequency: str = "daily"
    price_field_used: PriceField = PriceField.ADJUSTED_CLOSE
    schema_version: str = DATA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise ValueError("symbol must be a valid uppercase market ticker")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.frequency != "daily":
            raise ValueError("PHASE 1 supports only daily EOD data")
        object.__setattr__(self, "symbol", normalized_symbol)

    def cache_identity(self) -> dict[str, str]:
        """Return the request dimensions that define a cache entry."""
        return {
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "frequency": self.frequency,
            "price_field_used": self.price_field_used.value,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class MarketDataPoint:
    """One normalized EOD bar containing raw and adjusted values."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    adj_volume: float
    div_cash: float
    split_factor: float

    def price_for(self, price_field: PriceField) -> float:
        """Return the explicitly selected close field."""
        return self.close_for(price_field)

    def open_for(self, price_field: PriceField) -> float:
        """Return the explicitly selected open field."""
        if price_field is PriceField.RAW_CLOSE:
            return self.open
        if price_field is PriceField.ADJUSTED_CLOSE:
            return self.adj_open
        raise ValueError(f"Unsupported price field: {price_field}")

    def close_for(self, price_field: PriceField) -> float:
        """Return the explicitly selected close field."""
        if price_field is PriceField.RAW_CLOSE:
            return self.close
        if price_field is PriceField.ADJUSTED_CLOSE:
            return self.adj_close
        raise ValueError(f"Unsupported price field: {price_field}")

    def to_dict(self) -> dict[str, float | str]:
        """Serialize this data point for cache storage."""
        return {
            "date": self.date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "adj_open": self.adj_open,
            "adj_high": self.adj_high,
            "adj_low": self.adj_low,
            "adj_close": self.adj_close,
            "adj_volume": self.adj_volume,
            "div_cash": self.div_cash,
            "split_factor": self.split_factor,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MarketDataPoint:
        """Deserialize a point that was previously written to cache."""
        return cls(
            date=date.fromisoformat(str(payload["date"])),
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=float(payload["volume"]),
            adj_open=float(payload["adj_open"]),
            adj_high=float(payload["adj_high"]),
            adj_low=float(payload["adj_low"]),
            adj_close=float(payload["adj_close"]),
            adj_volume=float(payload["adj_volume"]),
            div_cash=float(payload["div_cash"]),
            split_factor=float(payload["split_factor"]),
        )


@dataclass(frozen=True)
class HistoricalDataSet:
    """A validated normalized EOD data set and its provenance."""

    request: HistoricalDataRequest
    points: tuple[MarketDataPoint, ...]
    source: DataSource

    @property
    def price_field_used(self) -> PriceField:
        """Expose the selected price convention in every result."""
        return self.request.price_field_used

    def with_source(self, source: DataSource) -> HistoricalDataSet:
        """Return the same data with a new fetch provenance."""
        return replace(self, source=source)

    def to_cache_payload(self) -> dict[str, Any]:
        """Serialize data independently of the outer cache format."""
        return {
            "request": self.request.cache_identity(),
            "source": self.source.value,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_cache_payload(cls, payload: Mapping[str, Any]) -> HistoricalDataSet:
        """Deserialize a stored normalized data set."""
        request_payload = payload["request"]
        if not isinstance(request_payload, Mapping):
            raise ValueError("cache request payload is invalid")
        points_payload = payload["points"]
        if not isinstance(points_payload, list):
            raise ValueError("cache point payload is invalid")
        request = HistoricalDataRequest(
            symbol=str(request_payload["symbol"]),
            start_date=date.fromisoformat(str(request_payload["start_date"])),
            end_date=date.fromisoformat(str(request_payload["end_date"])),
            frequency=str(request_payload["frequency"]),
            price_field_used=PriceField(str(request_payload["price_field_used"])),
            schema_version=str(request_payload["schema_version"]),
        )
        return cls(
            request=request,
            points=tuple(MarketDataPoint.from_dict(item) for item in points_payload),
            source=DataSource(str(payload["source"])),
        )
