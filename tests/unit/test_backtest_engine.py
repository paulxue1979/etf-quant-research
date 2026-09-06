from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest import (
    BacktestConfig,
    BacktestEngine,
    CommissionPolicy,
    RebalanceFrequency,
    RebalancePolicy,
    TargetAllocation,
)
from backtest.exceptions import (
    BacktestConfigurationError,
    ExecutionError,
    MissingMarketDataError,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)


def _dataset(
    symbol: str,
    opens: list[float],
    closes: list[float] | None = None,
    *,
    start: date = date(2024, 1, 2),
) -> HistoricalDataSet:
    closes = closes if closes is not None else opens
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
            open=open_price,
            high=max(open_price, close_price),
            low=min(open_price, close_price),
            close=close_price,
            volume=100.0,
            adj_open=open_price,
            adj_high=max(open_price, close_price),
            adj_low=min(open_price, close_price),
            adj_close=close_price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, (open_price, close_price) in enumerate(zip(opens, closes, strict=True))
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol=symbol,
            start_date=start,
            end_date=start + timedelta(days=len(points) - 1),
        ),
        points=points,
        source=DataSource.API_FRESH,
    )


def _config(
    *,
    start: date = date(2024, 1, 2),
    end: date = date(2024, 1, 5),
    capital: float = 1_000.0,
    commission: CommissionPolicy | None = None,
    slippage: float = 0.0,
    frequency: RebalanceFrequency = RebalanceFrequency.DAILY,
) -> BacktestConfig:
    return BacktestConfig(
        strategy_version_id="test-v1",
        start_date=start,
        end_date=end,
        initial_capital=capital,
        price_field_used=PriceField.RAW_CLOSE,
        commission=commission or CommissionPolicy(),
        slippage=slippage,
        rebalance_policy=RebalancePolicy(frequency=frequency),
    )


def test_single_asset_buy_sell_and_trade_accounting() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 11, 12], [10, 11, 11, 12])}
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
        TargetAllocation.from_weights(date(2024, 1, 4), {"QQQ": 0.0}),
    ]

    result = BacktestEngine().run(data, allocations, _config())

    assert [(fill.side.value, fill.quantity, fill.price) for fill in result.fills] == [
        ("BUY", 100, 10.0),
        ("SELL", 100, 12.0),
    ]
    assert result.final_equity == pytest.approx(1_200.0)
    assert result.trades[0].pnl == pytest.approx(200.0)
    assert result.trades[0].holding_period == 2
    assert result.positions[1].positions[0].quantity == 100
    assert result.positions[-1].positions == ()


def test_multi_asset_switch_sells_before_buying() -> None:
    data = {
        "QQQ": _dataset("QQQ", [10, 10, 20, 20]),
        "TQQQ": _dataset("TQQQ", [10, 10, 5, 5]),
    }
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 0.5, "TQQQ": 0.5}),
        TargetAllocation.from_weights(date(2024, 1, 4), {"TQQQ": 1.0}),
    ]

    result = BacktestEngine().run(data, allocations, _config())

    assert [fill.side.value for fill in result.fills] == ["BUY", "BUY", "SELL", "BUY"]
    assert result.fills[2].symbol == "QQQ"
    assert result.fills[3].symbol == "TQQQ"
    assert result.positions[-1].positions[0].symbol == "TQQQ"
    assert result.positions[-1].positions[0].quantity == 250
    assert result.equity_curve[-1].total_equity == pytest.approx(1_250.0)


def test_commission_slippage_and_residual_cash_are_deterministic() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 10, 10])}
    allocations = [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})]
    config = _config(
        commission=CommissionPolicy(rate=0.01, per_order=2.0),
        slippage=0.1,
    )

    result = BacktestEngine().run(data, allocations, config)

    assert result.fills[0].price == pytest.approx(11.0)
    assert result.fills[0].quantity == 89
    assert result.fills[0].commission == pytest.approx(2.0 + 89 * 11 * 0.01)
    assert result.cash_history[1][1] == pytest.approx(1_000 - (89 * 11 + 11.79))
    assert result.equity_curve[1].total_equity == pytest.approx(
        1_000 - (89 * 11 + 11.79) + 89 * 10
    )


def test_cash_shortfall_does_not_create_negative_cash() -> None:
    data = {"QQQ": _dataset("QQQ", [60, 60, 60, 60])}
    allocations = [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})]

    result = BacktestEngine().run(data, allocations, _config())

    assert result.fills[0].quantity == 16
    assert result.cash_history[1][1] == pytest.approx(40.0)
    assert all(point.cash >= 0 for point in result.equity_curve)


def test_price_field_and_no_lookahead_are_explicit() -> None:
    data = {
        "QQQ": _dataset("QQQ", [10, 20, 20, 20], [10, 20, 20, 20]),
    }
    allocations = [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})]

    result = BacktestEngine().run(data, allocations, _config())

    assert result.orders[0].signal_date == date(2024, 1, 2)
    assert result.orders[0].date == date(2024, 1, 3)
    assert result.orders[0].requested_price == 20.0
    assert result.configuration_snapshot["price_field_used"] == "raw_close"


def test_adjusted_price_field_controls_execution_and_valuation() -> None:
    data = _dataset("QQQ", [10, 20, 20, 20], [10, 20, 20, 20])
    points = tuple(
        point.__class__(
            **{
                **point.__dict__,
                "adj_open": point.open * 2,
                "adj_high": point.high * 2,
                "adj_low": point.low * 2,
                "adj_close": point.close * 2,
            },
        )
        for point in data.points
    )
    data = data.__class__(request=data.request, points=points, source=data.source)
    config = _config()
    config = config.__class__(**{**config.__dict__, "price_field_used": PriceField.ADJUSTED_CLOSE})

    result = BacktestEngine().run(
        {"QQQ": data},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        config,
    )

    assert result.orders[0].requested_price == 40.0
    assert result.orders[0].quantity == 25
    assert result.positions[1].positions[0].market_price == 40.0


def test_rebalance_frequency_uses_first_signal_in_each_period() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10, 10])}
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
        TargetAllocation.from_weights(date(2024, 1, 3), {}),
        TargetAllocation.from_weights(date(2024, 1, 4), {}),
        TargetAllocation.from_weights(date(2024, 1, 5), {}),
        TargetAllocation.from_weights(date(2024, 1, 6), {}),
        TargetAllocation.from_weights(date(2024, 1, 7), {}),
    ]

    result = BacktestEngine().run(
        data,
        allocations,
        _config(end=date(2024, 1, 7), frequency=RebalanceFrequency.WEEKLY),
    )

    assert len(result.fills) == 1
    assert result.fills[0].side.value == "BUY"


def test_on_signal_change_normalizes_omitted_zero_weights() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])}
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {}),
        TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 0.0}),
        TargetAllocation.from_weights(date(2024, 1, 4), {"QQQ": 1.0}),
    ]

    result = BacktestEngine().run(
        data,
        allocations,
        _config(end=date(2024, 1, 6), frequency=RebalanceFrequency.ON_SIGNAL_CHANGE),
    )

    assert len(result.fills) == 1
    assert result.orders[0].signal_date == date(2024, 1, 4)


def test_rebalance_threshold_skips_small_weight_change() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])}
    allocations = [
        TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
        TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 0.99}),
    ]
    config = _config(end=date(2024, 1, 6))
    config = config.__class__(
        **{
            **config.__dict__,
            "rebalance_policy": RebalancePolicy(
                frequency=RebalanceFrequency.DAILY,
                threshold=0.02,
            ),
        },
    )

    result = BacktestEngine().run(data, allocations, config)

    assert [(fill.side.value, fill.quantity) for fill in result.fills] == [("BUY", 100)]


@pytest.mark.parametrize(
    "bad_data",
    [
        {},
        {"QQQ": _dataset("QQQ", [10])},
    ],
)
def test_missing_market_data_is_explicit(bad_data) -> None:
    with pytest.raises((MissingMarketDataError, ExecutionError)):
        BacktestEngine().run(
            bad_data,
            [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
            _config(end=date(2024, 1, 2)),
        )


def test_missing_target_symbol_and_unsupported_rule_are_rejected() -> None:
    data = {"QQQ": _dataset("QQQ", [10, 10])}
    with pytest.raises(MissingMarketDataError):
        BacktestEngine().run(
            data,
            [TargetAllocation.from_weights(date(2024, 1, 2), {"SPY": 1.0})],
            _config(end=date(2024, 1, 3)),
        )

    config = _config(end=date(2024, 1, 3))
    config = config.__class__(
        **{**config.__dict__, "price_field_used": "raw_close"},
    )
    with pytest.raises(BacktestConfigurationError):
        BacktestEngine().run(data, [], config)


def test_accounting_identity_holds_every_day() -> None:
    data = {
        "QQQ": _dataset("QQQ", [10, 10, 12, 12]),
        "TQQQ": _dataset("TQQQ", [20, 20, 18, 18]),
    }
    result = BacktestEngine().run(
        data,
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 0.5, "TQQQ": 0.5})],
        _config(),
    )

    for point in result.equity_curve:
        assert point.total_equity == pytest.approx(point.cash + sum(point.asset_values.values()))
