from __future__ import annotations

from datetime import date

import pytest

from data.cache import DiskCache
from data.exceptions import CacheCorruptionError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from data.service import HistoricalDataService


def _request(price_field: PriceField = PriceField.ADJUSTED_CLOSE) -> HistoricalDataRequest:
    return HistoricalDataRequest(
        symbol="QQQ",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        price_field_used=price_field,
    )


def _row() -> dict[str, float | str]:
    return {
        "date": "2024-01-02T00:00:00.000Z",
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


def _data(request: HistoricalDataRequest) -> HistoricalDataSet:
    point = MarketDataPoint(
        date=date(2024, 1, 2),
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
    return HistoricalDataSet(request=request, points=(point,), source=DataSource.API_FRESH)


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_eod_prices(self, request: HistoricalDataRequest) -> list[dict[str, float | str]]:
        self.calls += 1
        return [_row()]


def test_disk_cache_write_read_and_cache_key_dimensions(tmp_path) -> None:
    cache = DiskCache(tmp_path)
    request = _request()
    cache.set(_data(request))

    cached = cache.get(request)

    assert cached is not None
    assert cached.source is DataSource.CACHE
    assert cached.points == _data(request).points
    assert cache.get(_request(PriceField.RAW_CLOSE)) is None
    assert cache.path_for(request).exists()


@pytest.mark.parametrize(
    "contents",
    ["{not-json", '{"cache_format_version": 1, "request": {}, "data": {}}'],
)
def test_disk_cache_rejects_corrupted_entry(tmp_path, contents: str) -> None:
    cache = DiskCache(tmp_path)
    request = _request()
    path = cache.path_for(request)
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(CacheCorruptionError, match="cache entry is invalid"):
        cache.get(request)


def test_service_cache_miss_fetches_then_cache_hit_avoids_duplicate_api_call(tmp_path) -> None:
    client = _FakeClient()
    service = HistoricalDataService(client, DiskCache(tmp_path))  # type: ignore[arg-type]
    request = _request()

    fresh = service.get_history(request)
    cached = service.get_history(request)

    assert fresh.source is DataSource.API_FRESH
    assert cached.source is DataSource.CACHE
    assert client.calls == 1


def test_service_rebuilds_corrupted_cache_from_fresh_api_data(tmp_path) -> None:
    cache = DiskCache(tmp_path)
    request = _request()
    path = cache.path_for(request)
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    client = _FakeClient()
    service = HistoricalDataService(client, cache)  # type: ignore[arg-type]

    result = service.get_history(request)

    assert result.source is DataSource.API_FRESH
    assert client.calls == 1
    assert cache.get(request) is not None
