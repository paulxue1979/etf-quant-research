from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from data.config import TiingoSettings
from data.exceptions import (
    TiingoAuthenticationError,
    TiingoNetworkError,
    TiingoRateLimitError,
    TiingoResponseError,
    TiingoServiceError,
    TiingoTimeoutError,
)
from data.models import HistoricalDataRequest
from data.tiingo import TiingoClient


def _request() -> HistoricalDataRequest:
    return HistoricalDataRequest(
        symbol="QQQ", start_date=date(2024, 1, 2), end_date=date(2024, 1, 5)
    )


def _settings() -> TiingoSettings:
    return TiingoSettings(api_key="test-key")


def _response(
    status_code: int,
    *,
    json_body: object | None = None,
    text: str | None = None,
) -> httpx.Response:
    if text is not None:
        return httpx.Response(status_code, text=text)
    return httpx.Response(status_code, json=json_body)


def test_successful_http_response_returns_tiingo_rows() -> None:
    transport = httpx.MockTransport(lambda request: _response(200, json_body=[{"date": "x"}]))
    client = TiingoClient(_settings(), transport=transport, sleeper=lambda _: None)

    assert client.fetch_eod_prices(_request()) == [{"date": "x"}]


def test_invalid_api_key_raises_safe_authentication_error() -> None:
    transport = httpx.MockTransport(lambda request: _response(401, json_body={"detail": "no"}))
    client = TiingoClient(_settings(), transport=transport, sleeper=lambda _: None)

    with pytest.raises(TiingoAuthenticationError) as error:
        client.fetch_eod_prices(_request())

    assert "test-key" not in str(error.value)


def test_rate_limit_retries_with_retry_after_then_raises() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "2"})

    client = TiingoClient(
        _settings(),
        max_attempts=3,
        transport=httpx.MockTransport(handler),
        sleeper=delays.append,
    )

    with pytest.raises(TiingoRateLimitError):
        client.fetch_eod_prices(_request())

    assert calls == 3
    assert delays == [2.0, 2.0]


def test_service_error_retries_with_bounded_exponential_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(503, json_body={})

    client = TiingoClient(
        _settings(),
        max_attempts=3,
        backoff_seconds=0.25,
        transport=httpx.MockTransport(handler),
        sleeper=delays.append,
    )

    with pytest.raises(TiingoServiceError):
        client.fetch_eod_prices(_request())

    assert calls == 3
    assert delays == [0.25, 0.5]


def test_timeout_retries_then_raises_timeout_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout", request=request)

    client = TiingoClient(
        _settings(), max_attempts=2, transport=httpx.MockTransport(handler), sleeper=lambda _: None
    )

    with pytest.raises(TiingoTimeoutError):
        client.fetch_eod_prices(_request())

    assert attempts == 2


def test_network_error_retries_then_raises_network_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("network", request=request)

    client = TiingoClient(
        _settings(), max_attempts=2, transport=httpx.MockTransport(handler), sleeper=lambda _: None
    )

    with pytest.raises(TiingoNetworkError):
        client.fetch_eod_prices(_request())

    assert attempts == 2


def test_malformed_json_and_unexpected_structure_are_rejected() -> None:
    malformed = TiingoClient(
        _settings(),
        transport=httpx.MockTransport(lambda request: _response(200, text="{")),
        sleeper=lambda _: None,
    )
    wrong_shape = TiingoClient(
        _settings(),
        transport=httpx.MockTransport(lambda request: _response(200, json_body={"rows": []})),
        sleeper=lambda _: None,
    )

    with pytest.raises(TiingoResponseError, match="malformed JSON"):
        malformed.fetch_eod_prices(_request())
    with pytest.raises(TiingoResponseError, match="unexpected EOD response structure"):
        wrong_shape.fetch_eod_prices(_request())


def test_http_client_does_not_put_key_in_url_or_logs(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "test-key" not in str(request.url)
        assert request.headers["Authorization"] == "Token test-key"
        return _response(503, json_body={})

    client = TiingoClient(
        _settings(), max_attempts=1, transport=httpx.MockTransport(handler), sleeper=lambda _: None
    )

    with pytest.raises(TiingoServiceError):
        client.fetch_eod_prices(_request())

    assert "test-key" not in caplog.text


def test_retry_after_parser_accepts_seconds_and_rejects_invalid_values() -> None:
    from data.tiingo import _parse_retry_after

    assert _parse_retry_after("1.5") == 1.5
    assert _parse_retry_after("not-a-date") is None
    assert json.loads("{}") == {}
