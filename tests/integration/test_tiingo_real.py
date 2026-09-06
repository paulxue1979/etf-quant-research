from __future__ import annotations

from datetime import date

import pytest

from data.cache import DiskCache
from data.config import TiingoSettings
from data.exceptions import TiingoConfigurationError
from data.models import DataSource, HistoricalDataRequest, PriceField
from data.service import HistoricalDataService
from data.tiingo import TiingoClient


@pytest.fixture(scope="module")
def settings() -> TiingoSettings:
    try:
        return TiingoSettings.from_environment()
    except TiingoConfigurationError as exc:
        pytest.skip(f"Real Tiingo integration tests not run: {exc}")


@pytest.mark.integration
@pytest.mark.parametrize("symbol", ["QQQ", "TQQQ", "SPY"])
def test_real_tiingo_daily_history_and_disk_cache(
    symbol: str,
    settings: TiingoSettings,
    tmp_path,
) -> None:
    request = HistoricalDataRequest(
        symbol=symbol,
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 10),
        price_field_used=PriceField.ADJUSTED_CLOSE,
    )
    service = HistoricalDataService(TiingoClient(settings), DiskCache(tmp_path))

    fresh = service.get_history(request)
    cached = service.get_history(request)

    assert fresh.source is DataSource.API_FRESH
    assert cached.source is DataSource.CACHE
    assert fresh.points
    assert fresh.price_field_used is PriceField.ADJUSTED_CLOSE
    assert all(point.high >= point.low for point in fresh.points)
    assert all(point.adj_high >= point.adj_low for point in fresh.points)
