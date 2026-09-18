from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from backtest import (
    BacktestEngine,
    CommissionPolicy,
    ContributionFrequency,
    ContributionSchedule,
    TargetAllocation,
)
from tests.unit.test_backtest_engine import _config, _dataset


def test_partial_sells_create_distinct_closed_segments_from_one_fifo_lot() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 10, 10])}
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
        TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 0.6}),
        TargetAllocation.from_weights(date(2024, 1, 4), {}),
    ]

    first = BacktestEngine().run(data, allocations, _config())
    second = BacktestEngine().run(data, allocations, _config())

    assert [(item.status.value, item.quantity) for item in first.holding_segments] == [
        ("CLOSED", 40),
        ("CLOSED", 60),
    ]
    assert len({item.lot_id for item in first.holding_segments}) == 1
    assert len({item.holding_id for item in first.holding_segments}) == 2
    assert [item.holding_id for item in first.holding_segments] == [
        item.holding_id for item in second.holding_segments
    ]
    assert [item.entry_execution_date for item in first.holding_segments] == [
        date(2024, 1, 3),
        date(2024, 1, 3),
    ]
    assert [item.exit_execution_date for item in first.holding_segments] == [
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    assert [item.holding_days for item in first.holding_segments] == [1, 2]
    assert [item.realized_pnl for item in first.holding_segments] == pytest.approx(
        [item.pnl for item in first.trades]
    )


def test_multiple_buys_and_fifo_sell_preserve_remaining_open_lot() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10])},
        [
            TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 0.5}),
            TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 1.0}),
            TargetAllocation.from_weights(date(2024, 1, 4), {"QQQ": 0.25}),
        ],
        _config(),
    )

    closed = [item for item in result.holding_segments if item.status.value == "CLOSED"]
    opened = [item for item in result.holding_segments if item.status.value == "OPEN"]

    assert [(item.quantity, item.entry_execution_date) for item in closed] == [
        (50, date(2024, 1, 3)),
        (25, date(2024, 1, 4)),
    ]
    assert [(item.quantity, item.entry_execution_date) for item in opened] == [
        (25, date(2024, 1, 4))
    ]
    assert closed[1].lot_id == opened[0].lot_id
    assert opened[0].exit_execution_date is None
    assert opened[0].report_end_date == date(2024, 1, 5)
    assert opened[0].ending_price == pytest.approx(10.0)
    assert opened[0].market_value == pytest.approx(250.0)
    assert opened[0].unrealized_pnl == pytest.approx(0.0)


def test_contribution_buy_creates_open_lot_without_fake_strategy_signal() -> None:
    config = replace(
        _config(),
        contribution_schedule=ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            "500",
            requested_date=date(2024, 1, 4),
        ),
    )
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        config,
    )

    contribution = next(
        item
        for item in result.holding_segments
        if item.entry_execution_cause.value == "contribution"
    )
    assert contribution.status.value == "OPEN"
    assert contribution.quantity == 50
    assert contribution.entry_signal_date is None
    assert contribution.entry_execution_date == date(2024, 1, 4)


def test_multi_asset_holdings_exclude_cash_and_keep_sgov_as_security() -> None:
    result = BacktestEngine().run(
        {
            "QQQ": _dataset("QQQ", [10, 10, 10, 10]),
            "SGOV": _dataset("SGOV", [20, 20, 20, 20]),
        },
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 0.5, "SGOV": 0.4})],
        _config(),
    )

    assert {item.symbol for item in result.holding_segments} == {"QQQ", "SGOV"}
    assert all(item.quantity > 0 for item in result.holding_segments)
    assert "CASH" not in {item.symbol for item in result.holding_segments}


def test_holding_pnl_and_return_reuse_trade_commission_and_slippage_semantics() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 11, 12])},
        [
            TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
            TargetAllocation.from_weights(date(2024, 1, 4), {}),
        ],
        _config(
            commission=CommissionPolicy(rate=0.01, per_order=2.0),
            slippage=0.01,
        ),
    )

    holding = result.holding_segments[0]
    trade = result.trades[0]
    buy_fill, sell_fill = result.fills

    assert holding.status.value == "CLOSED"
    assert holding.entry_price == pytest.approx(
        buy_fill.price + buy_fill.commission / buy_fill.quantity
    )
    assert holding.exit_price == pytest.approx(sell_fill.price)
    assert holding.realized_pnl == pytest.approx(trade.pnl)
    assert holding.holding_return == pytest.approx(trade.pnl_pct)


def test_holding_segment_rejects_non_finite_report_values() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        _config(),
    )

    with pytest.raises(ValueError, match="holding_return must be finite"):
        replace(result.holding_segments[0], holding_return=float("nan"))
