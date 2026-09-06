"""Normalization of Tiingo EOD responses into the internal schema."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime

from data.exceptions import TiingoResponseError
from data.models import DataSource, HistoricalDataRequest, HistoricalDataSet, MarketDataPoint

_REQUIRED_TIINGO_FIELDS = {
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adjOpen",
    "adjHigh",
    "adjLow",
    "adjClose",
    "adjVolume",
    "divCash",
    "splitFactor",
}


def normalize_tiingo_eod_response(
    payload: object,
    request: HistoricalDataRequest,
) -> HistoricalDataSet:
    """Map Tiingo's verified EOD fields into the internal snake_case model."""
    if not isinstance(payload, list):
        raise TiingoResponseError("Tiingo EOD response must be a JSON array.")
    if not payload:
        raise TiingoResponseError("Tiingo EOD response contains no data rows.")

    points: list[MarketDataPoint] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise TiingoResponseError(f"Tiingo EOD row {index} is not an object.")
        missing_fields = _REQUIRED_TIINGO_FIELDS.difference(row)
        if missing_fields:
            names = ", ".join(sorted(missing_fields))
            raise TiingoResponseError(f"Tiingo EOD row {index} is missing fields: {names}.")
        points.append(
            MarketDataPoint(
                date=_parse_date(row["date"], index),
                open=_parse_number(row["open"], "open", index),
                high=_parse_number(row["high"], "high", index),
                low=_parse_number(row["low"], "low", index),
                close=_parse_number(row["close"], "close", index),
                volume=_parse_number(row["volume"], "volume", index),
                adj_open=_parse_number(row["adjOpen"], "adjOpen", index),
                adj_high=_parse_number(row["adjHigh"], "adjHigh", index),
                adj_low=_parse_number(row["adjLow"], "adjLow", index),
                adj_close=_parse_number(row["adjClose"], "adjClose", index),
                adj_volume=_parse_number(row["adjVolume"], "adjVolume", index),
                div_cash=_parse_number(row["divCash"], "divCash", index),
                split_factor=_parse_number(row["splitFactor"], "splitFactor", index),
            )
        )
    return HistoricalDataSet(request=request, points=tuple(points), source=DataSource.API_FRESH)


def _parse_date(value: object, row_index: int) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TiingoResponseError(f"Tiingo EOD row {row_index} has an invalid date.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise TiingoResponseError(f"Tiingo EOD row {row_index} has an invalid date.") from exc


def _parse_number(value: object, field_name: str, row_index: int) -> float:
    if value is None or isinstance(value, bool):
        raise TiingoResponseError(f"Tiingo EOD row {row_index} has invalid {field_name}.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TiingoResponseError(f"Tiingo EOD row {row_index} has invalid {field_name}.") from exc
    if not math.isfinite(result):
        raise TiingoResponseError(f"Tiingo EOD row {row_index} has invalid {field_name}.")
    return result
