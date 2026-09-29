"""Canonical requested/effective trading-date boundary contract."""

from __future__ import annotations

from datetime import date


class EffectiveDateRangeError(ValueError):
    """Raised when effective evidence is outside or empty within a request."""


def validate_effective_range_within_requested(
    requested_start: date,
    requested_end: date,
    effective_start: date | None,
    effective_end: date | None,
) -> None:
    """Validate an inclusive effective range inside a requested range."""
    for label, value in (
        ("requested_start", requested_start),
        ("requested_end", requested_end),
        ("effective_start", effective_start),
        ("effective_end", effective_end),
    ):
        if not isinstance(value, date):
            raise EffectiveDateRangeError(f"{label} must be a date")
    assert effective_start is not None
    assert effective_end is not None
    if requested_start > requested_end:
        raise EffectiveDateRangeError("requested date range is invalid")
    if effective_start > effective_end:
        raise EffectiveDateRangeError("effective date range is empty")
    if effective_start < requested_start or effective_end > requested_end:
        raise EffectiveDateRangeError("effective date range is outside requested range")


__all__ = ["EffectiveDateRangeError", "validate_effective_range_within_requested"]
