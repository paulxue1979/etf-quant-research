"""Explicit errors raised by the PHASE 3 backtest engine."""


class BacktestError(Exception):
    """Base class for backtest domain errors."""


class BacktestConfigurationError(BacktestError):
    """Raised when the backtest configuration is invalid."""


class InvalidTargetAllocationError(BacktestError):
    """Raised when target weights are invalid or incomplete."""


class MissingMarketDataError(BacktestError):
    """Raised when required asset data is unavailable."""


class InvalidPriceError(BacktestError):
    """Raised when an execution or valuation price is invalid."""


class InvalidQuantityError(BacktestError):
    """Raised when an order quantity is invalid."""


class ExecutionError(BacktestError):
    """Raised when the requested execution rule cannot be applied."""


class InsufficientCashError(BacktestError):
    """Raised when a planned order cannot be funded without borrowing."""
