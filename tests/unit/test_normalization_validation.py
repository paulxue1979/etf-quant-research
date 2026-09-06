from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from data.exceptions import DataValidationError, TiingoResponseError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from data.normalization import normalize_tiingo_eod_response
from data.validation import validate_historical_data


def _request(price_field: PriceField = PriceField.ADJUSTED_CLOSE) -> HistoricalDataRequest:
    return HistoricalDataRequest(
        symbol="QQQ",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        price_field_used=price_field,
    )


def _row(day: str = "2024-01-02T00:00:00.000Z") -> dict[str, float | str]:
    return {
        "date": day,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 1000.0,
        "adjOpen": 90.0,
        "adjHigh": 93.0,
        "adjLow": 89.0,
        "adjClose": 92.0,
        "adjVolume": 1100.0,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }


def _point(day: date = date(2024, 1, 2)) -> MarketDataPoint:
    return MarketDataPoint(
        date=day,
        open=100.0,
        high=103.0,
        low=99.0,
        close=102.0,
        volume=1000.0,
        adj_open=90.0,
        adj_high=93.0,
        adj_low=89.0,
        adj_close=92.0,
        adj_volume=1100.0,
        div_cash=0.0,
        split_factor=1.0,
    )


def _data(points: tuple[MarketDataPoint, ...]) -> HistoricalDataSet:
    return HistoricalDataSet(request=_request(), points=points, source=DataSource.API_FRESH)


def test_normalizes_verified_tiingo_fields_and_keeps_price_conventions_distinct() -> None:
    data = normalize_tiingo_eod_response([_row()], _request())
    point = data.points[0]

    assert point.date == date(2024, 1, 2)
    assert point.close == 102.0
    assert point.adj_close == 92.0
    assert point.price_for(PriceField.RAW_CLOSE) == 102.0
    assert point.price_for(PriceField.ADJUSTED_CLOSE) == 92.0
    assert data.price_field_used is PriceField.ADJUSTED_CLOSE
    assert data.source is DataSource.API_FRESH


def test_normalization_rejects_missing_or_malformed_required_values() -> None:
    malformed = _row()
    del malformed["adjClose"]

    with pytest.raises(TiingoResponseError, match="adjClose"):
        normalize_tiingo_eod_response([malformed], _request())


def test_validation_accepts_strictly_ascending_valid_data() -> None:
    validate_historical_data(
        _data((_point(date(2024, 1, 2)), _point(date(2024, 1, 3))))
    )


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ((), "data set is empty"),
        ((_point(date(2024, 1, 3)), _point(date(2024, 1, 2))), "dates are not strictly ascending"),
        ((_point(), _point()), "duplicate date"),
        ((replace(_point(), high=98.0),), "raw high is below low"),
        ((replace(_point(), volume=-1.0),), "volume must not be negative"),
        ((replace(_point(), adj_close=float("nan")),), "adjusted OHLCV contains an invalid value"),
    ],
)
def test_validation_rejects_invalid_market_data(
    points: tuple[MarketDataPoint, ...], message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        validate_historical_data(_data(points))
