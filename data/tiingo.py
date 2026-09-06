"""Reliable Tiingo EOD HTTP client with bounded retries and safe errors."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from data.config import TiingoSettings
from data.exceptions import (
    TiingoAuthenticationError,
    TiingoNetworkError,
    TiingoRateLimitError,
    TiingoRequestError,
    TiingoResponseError,
    TiingoServiceError,
    TiingoTimeoutError,
)
from data.models import HistoricalDataRequest

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class TiingoClient:
    """Fetch Tiingo daily EOD JSON without exposing credentials in URLs or logs."""

    def __init__(
        self,
        settings: TiingoSettings,
        *,
        timeout_seconds: float = 15.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        max_retry_delay_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")
        self._settings = settings
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._transport = transport
        self._sleeper = sleeper
        self._logger = logger or logging.getLogger(__name__)

    def fetch_eod_prices(self, request: HistoricalDataRequest) -> list[dict[str, Any]]:
        """Return raw Tiingo EOD rows for one bounded, retried request."""
        url = f"{self._settings.base_url.rstrip('/')}/tiingo/daily/{request.symbol}/prices"
        params = {
            "startDate": request.start_date.isoformat(),
            "endDate": request.end_date.isoformat(),
        }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Token {self._settings.api_key}",
        }

        for attempt in range(1, self._max_attempts + 1):
            try:
                with httpx.Client(
                    timeout=self._timeout,
                    transport=self._transport,
                    headers=headers,
                ) as client:
                    response = client.get(url, params=params)
            except httpx.TimeoutException as exc:
                if attempt == self._max_attempts:
                    raise TiingoTimeoutError(
                        f"Tiingo request timed out after {self._max_attempts} attempts."
                    ) from exc
                self._retry(attempt, "timeout")
                continue
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    raise TiingoNetworkError(
                        f"Unable to reach Tiingo after {self._max_attempts} attempts."
                    ) from exc
                self._retry(attempt, "network error")
                continue

            if response.status_code == 200:
                return self._parse_rows(response)
            if response.status_code in (401, 403):
                raise TiingoAuthenticationError(
                    "Tiingo rejected TIINGO_API_KEY. Check the configured credential."
                )
            if response.status_code == 404:
                raise TiingoRequestError(
                    f"Tiingo has no daily EOD data for symbol {request.symbol}."
                )
            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt == self._max_attempts:
                    self._raise_retry_exhausted(response.status_code)
                self._retry(attempt, f"HTTP {response.status_code}", response)
                continue
            raise TiingoRequestError(
                f"Tiingo request failed with HTTP status {response.status_code}."
            )

        raise AssertionError("retry loop exited unexpectedly")

    def _parse_rows(self, response: httpx.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise TiingoResponseError("Tiingo returned malformed JSON.") from exc
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise TiingoResponseError("Tiingo returned an unexpected EOD response structure.")
        return payload

    def _retry(
        self,
        attempt: int,
        reason: str,
        response: httpx.Response | None = None,
    ) -> None:
        delay = self._retry_delay(attempt, response)
        self._logger.warning(
            "tiingo_request_retry attempt=%s reason=%s delay_seconds=%.3f",
            attempt,
            reason,
            delay,
        )
        self._sleeper(delay)

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            parsed = _parse_retry_after(retry_after)
            if parsed is not None:
                return min(parsed, self._max_retry_delay_seconds)
        return min(
            self._backoff_seconds * (2 ** (attempt - 1)),
            self._max_retry_delay_seconds,
        )

    def _raise_retry_exhausted(self, status_code: int) -> None:
        if status_code == 429:
            raise TiingoRateLimitError(
                f"Tiingo rate limit remained in effect after {self._max_attempts} attempts."
            )
        raise TiingoServiceError(
            f"Tiingo service remained unavailable after {self._max_attempts} attempts."
        )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)
