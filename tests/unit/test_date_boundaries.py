from datetime import date

import pytest

from research.date_boundaries import (
    EffectiveDateRangeError,
    validate_effective_range_within_requested,
)


def test_non_trading_start_inside_requested_range_is_valid() -> None:
    validate_effective_range_within_requested(
        date(2007, 1, 1), date(2020, 12, 31), date(2007, 1, 3), date(2020, 12, 31)
    )


def test_non_trading_end_inside_requested_range_is_valid() -> None:
    validate_effective_range_within_requested(
        date(2021, 1, 1), date(2026, 9, 16), date(2021, 1, 4), date(2026, 9, 15)
    )


@pytest.mark.parametrize(
    ("effective_start", "effective_end", "message"),
    [
        (date(2006, 12, 29), date(2020, 12, 31), "outside"),
        (date(2007, 1, 3), date(2021, 1, 4), "outside"),
        (date(2021, 1, 5), date(2021, 1, 4), "empty"),
        (None, date(2021, 1, 4), "effective_start"),
    ],
)
def test_invalid_effective_ranges_are_rejected(
    effective_start: date | None, effective_end: date | None, message: str
) -> None:
    with pytest.raises(EffectiveDateRangeError, match=message):
        validate_effective_range_within_requested(
            date(2007, 1, 1), date(2020, 12, 31), effective_start, effective_end
        )


def test_requested_range_order_is_rejected() -> None:
    with pytest.raises(EffectiveDateRangeError, match="requested"):
        validate_effective_range_within_requested(
            date(2020, 12, 31), date(2007, 1, 1), date(2007, 1, 3), date(2020, 12, 31)
        )
