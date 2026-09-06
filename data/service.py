"""Orchestration layer for cached, normalized, validated Tiingo EOD data."""

from __future__ import annotations

import logging

from data.cache import CacheBackend
from data.exceptions import CacheCorruptionError, DataValidationError
from data.models import HistoricalDataRequest, HistoricalDataSet
from data.normalization import normalize_tiingo_eod_response
from data.tiingo import TiingoClient
from data.validation import validate_historical_data


class HistoricalDataService:
    """Resolve a daily history request from cache or Tiingo, then validate it."""

    def __init__(
        self,
        client: TiingoClient,
        cache: CacheBackend,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._logger = logger or logging.getLogger(__name__)

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        """Return validated data and clearly label its API or cache provenance."""
        cached = self._read_cache(request)
        if cached is not None:
            self._logger.info(
                "market_data_cache_hit symbol=%s start_date=%s end_date=%s",
                request.symbol,
                request.start_date,
                request.end_date,
            )
            return cached

        self._logger.info(
            "market_data_request symbol=%s start_date=%s end_date=%s",
            request.symbol,
            request.start_date,
            request.end_date,
        )
        rows = self._client.fetch_eod_prices(request)
        data = normalize_tiingo_eod_response(rows, request)
        validate_historical_data(data)
        self._logger.info(
            "market_data_validation_passed symbol=%s source=%s rows=%s",
            request.symbol,
            data.source,
            len(data.points),
        )
        self._cache.set(data)
        self._logger.info(
            "market_data_cache_write symbol=%s rows=%s",
            request.symbol,
            len(data.points),
        )
        return data

    def _read_cache(self, request: HistoricalDataRequest) -> HistoricalDataSet | None:
        try:
            cached = self._cache.get(request)
        except CacheCorruptionError:
            self._logger.warning(
                "market_data_cache_corrupt symbol=%s; fetching fresh data",
                request.symbol,
            )
            return None
        if cached is None:
            self._logger.info("market_data_cache_miss symbol=%s", request.symbol)
            return None
        try:
            validate_historical_data(cached)
        except DataValidationError:
            self._logger.warning(
                "market_data_cache_failed_validation symbol=%s; fetching fresh data",
                request.symbol,
            )
            return None
        return cached
