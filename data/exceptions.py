"""Explicit errors raised by the data engine.

Messages intentionally contain no credential values or raw API response bodies.
"""


class DataEngineError(Exception):
    """Base error for the ETF market data engine."""


class TiingoConfigurationError(DataEngineError):
    """Raised when Tiingo configuration is incomplete."""


class TiingoAuthenticationError(DataEngineError):
    """Raised when Tiingo rejects the configured credential."""


class TiingoRateLimitError(DataEngineError):
    """Raised after bounded retries cannot recover from HTTP 429."""


class TiingoServiceError(DataEngineError):
    """Raised when Tiingo remains unavailable after bounded retries."""


class TiingoNetworkError(DataEngineError):
    """Raised when a network transport failure remains after bounded retries."""


class TiingoTimeoutError(DataEngineError):
    """Raised when Tiingo requests continue to time out."""


class TiingoRequestError(DataEngineError):
    """Raised for a non-retryable Tiingo request failure."""


class TiingoResponseError(DataEngineError):
    """Raised when Tiingo returns malformed or unexpected data."""


class DataValidationError(DataEngineError):
    """Raised when normalized market data violates required quality rules."""


class CacheError(DataEngineError):
    """Base error for local market data cache failures."""


class CacheCorruptionError(CacheError):
    """Raised when a cache entry cannot be safely used."""


class CacheWriteError(CacheError):
    """Raised when a validated data set cannot be written to disk cache."""
