"""Errors raised by the pure backtest performance analytics layer."""

from __future__ import annotations


class AnalyticsError(Exception):
    """Base class for performance analytics errors."""


class AnalyticsInputError(AnalyticsError):
    """Raised when a BacktestResult cannot be safely analyzed."""
