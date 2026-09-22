"""Deterministic target-allocation backtest orchestration."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
from types import MappingProxyType

from backtest.contributions import normalize_contribution_schedule
from backtest.exceptions import (
    BacktestConfigurationError,
    ExecutionError,
    InsufficientCashError,
    InvalidPriceError,
    InvalidQuantityError,
    InvalidTargetAllocationError,
    MissingMarketDataError,
)
from backtest.models import (
    AllocationPoint,
    BacktestConfig,
    BacktestResult,
    ContributionEvent,
    EquityPoint,
    ExternalCashFlow,
    Fill,
    HoldingSegment,
    HoldingStatus,
    Order,
    OrderSide,
    OrderStatus,
    RebalanceCause,
    RebalanceDecision,
    RebalanceDecisionType,
    TargetAllocation,
    Trade,
)
from backtest.rebalance import actual_allocation, evaluate_rebalance_decision
from data.exceptions import DataValidationError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from data.validation import validate_historical_data
from portfolio.models import PortfolioSnapshot, Position

_EPSILON = 1e-10


def _stable_identity(kind: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{kind}-{digest}"


@dataclass
class _Lot:
    lot_id: str
    entry_order_id: str
    entry_signal_date: date | None
    entry_date: date
    entry_price: float
    entry_cause: RebalanceCause
    quantity: int
    closed_segment_count: int = 0


@dataclass
class _Ledger:
    cash: float
    quantities: dict[str, int]
    lots: dict[str, list[_Lot]]

    @classmethod
    def create(cls, initial_capital: float) -> _Ledger:
        return cls(cash=initial_capital, quantities=defaultdict(int), lots=defaultdict(list))


class BacktestEngine:
    """Run a target-allocation backtest using next-trading-day open execution.

    The engine deliberately accepts target allocations rather than strategy code. This keeps
    strategy evaluation outside the execution and portfolio-accounting boundary.
    """

    def run(
        self,
        data: Mapping[str, HistoricalDataSet],
        target_allocations: Sequence[TargetAllocation],
        config: BacktestConfig,
    ) -> BacktestResult:
        """Execute a deterministic backtest over the supplied normalized data sets."""
        self._validate_config(config)
        normalized_data = self._prepare_data(data, config)
        symbols = tuple(sorted(normalized_data))
        trading_dates = self._trading_dates(normalized_data, config)
        effective_start_date = trading_dates[0]
        effective_end_date = trading_dates[-1]
        allocations = self._prepare_allocations(target_allocations, symbols, config)
        next_date = {
            trading_dates[index]: trading_dates[index + 1]
            for index in range(len(trading_dates) - 1)
        }
        scheduled = self._scheduled_allocations(allocations, trading_dates, symbols, config)
        contribution_events = (
            normalize_contribution_schedule(
                config.contribution_schedule,
                trading_dates,
                investment_start=effective_start_date,
                investment_end=effective_end_date,
            )
            if config.contribution_schedule is not None
            else ()
        )
        contributions_by_date: dict[date, list[ContributionEvent]] = defaultdict(list)
        for event in contribution_events:
            contributions_by_date[event.effective_date].append(event)

        ledger = _Ledger.create(config.initial_capital)
        pending: (
            tuple[
                TargetAllocation,
                date,
                RebalanceDecision | None,
                RebalanceCause,
                date,
            ]
            | None
        ) = None
        active_target = TargetAllocation.from_weights(trading_dates[0], {})
        canonical_target = active_target
        position_policy = config.position_rebalance_policy
        orders: list[Order] = []
        fills: list[Fill] = []
        trades: list[Trade] = []
        closed_holdings: list[HoldingSegment] = []
        equity_curve: list[EquityPoint] = []
        snapshots: list[PortfolioSnapshot] = []
        allocation_history: list[AllocationPoint] = []
        cash_history: list[tuple[date, float]] = []
        external_cash_flows: list[ExternalCashFlow] = []
        rebalance_decisions: list[RebalanceDecision] = []

        def apply_contributions(events: Sequence[ContributionEvent], as_of_date: date) -> None:
            for event in events:
                ledger.cash += float(event.amount)
                external_cash_flows.append(
                    ExternalCashFlow(
                        date=as_of_date,
                        amount=event.amount,
                        currency=event.currency,
                    )
                )

        for current_date in trading_dates:
            daily_contributions = contributions_by_date.get(current_date, [])
            pending_executed = False
            if position_policy.is_legacy_compatible:
                apply_contributions(daily_contributions, current_date)
            if pending is not None and pending[1] == current_date:
                pending_target, _, _, pending_cause, pending_signal_date = pending
                active_target = pending_target
                created_orders, created_fills, created_trades, created_holdings = self._rebalance(
                    ledger=ledger,
                    target=active_target,
                    signal_date=pending_signal_date,
                    execution_date=current_date,
                    data=normalized_data,
                    symbols=symbols,
                    config=config,
                    cause=pending_cause,
                )
                orders.extend(created_orders)
                fills.extend(created_fills)
                trades.extend(created_trades)
                closed_holdings.extend(created_holdings)
                pending = None
                pending_executed = True

            if not position_policy.is_legacy_compatible:
                apply_contributions(daily_contributions, current_date)

            if (
                daily_contributions
                and not pending_executed
                and position_policy.is_legacy_compatible
            ):
                created_orders, created_fills, created_trades, created_holdings = self._rebalance(
                    ledger=ledger,
                    target=active_target,
                    signal_date=active_target.date,
                    execution_date=current_date,
                    data=normalized_data,
                    symbols=symbols,
                    config=config,
                    cause=RebalanceCause.CONTRIBUTION,
                )
                orders.extend(created_orders)
                fills.extend(created_fills)
                trades.extend(created_trades)
                closed_holdings.extend(created_holdings)

            point_by_symbol = {
                symbol: normalized_data[symbol].points_by_date[current_date] for symbol in symbols
            }
            equity_point, snapshot = self._mark_to_market(
                ledger, current_date, point_by_symbol, config.price_field_used
            )
            equity_curve.append(equity_point)
            snapshots.append(snapshot)
            cash_history.append((current_date, ledger.cash))
            history_target = scheduled.get(
                current_date,
                canonical_target if not position_policy.is_legacy_compatible else active_target,
            )
            allocation_history.extend(
                self._allocation_points(
                    current_date,
                    history_target,
                    ledger,
                    point_by_symbol,
                    symbols,
                    config.price_field_used,
                    equity_point.total_equity,
                )
            )

            scheduled_allocation = scheduled.get(current_date)
            if position_policy.is_legacy_compatible:
                if scheduled_allocation is not None:
                    if current_date not in next_date:
                        raise ExecutionError(
                            f"No next trading day exists for signal date {current_date.isoformat()}"
                        )
                    pending = (
                        scheduled_allocation,
                        next_date[current_date],
                        None,
                        RebalanceCause.TARGET,
                        scheduled_allocation.date,
                    )
            elif (
                scheduled_allocation is not None
                or daily_contributions
                or (position_policy.drift_threshold is not None)
            ):
                if scheduled_allocation is not None:
                    if current_date not in next_date:
                        raise ExecutionError(
                            f"No next trading day exists for signal date {current_date.isoformat()}"
                        )
                    previous_target = canonical_target.as_mapping()
                    canonical_target = scheduled_allocation
                else:
                    previous_target = canonical_target.as_mapping()
                close_prices = {
                    symbol: self._valid_price(
                        normalized_data[symbol]
                        .points_by_date[current_date]
                        .close_for(config.price_field_used)
                    )
                    for symbol in symbols
                }
                contribution_amount = sum(float(item.amount) for item in daily_contributions)
                decision = evaluate_rebalance_decision(
                    evaluation_date=current_date,
                    target=canonical_target,
                    actual=actual_allocation(
                        cash=ledger.cash,
                        quantities=ledger.quantities,
                        prices=close_prices,
                    ),
                    previous_target=previous_target,
                    policy=position_policy,
                    contribution_amount=contribution_amount,
                    schedule_eligible=scheduled_allocation is not None,
                )
                execution_date = next_date.get(current_date)
                decision = replace(decision, execution_date=execution_date)
                rebalance_decisions.append(decision)
                if decision.decision is RebalanceDecisionType.EXECUTE:
                    if execution_date is None:
                        raise ExecutionError(
                            f"No next trading day exists for signal date {current_date.isoformat()}"
                        )
                    cause = (
                        RebalanceCause.TARGET
                        if scheduled_allocation is not None
                        or decision.target_change_metric > _EPSILON
                        else RebalanceCause.CONTRIBUTION
                    )
                    pending = (
                        canonical_target,
                        execution_date,
                        decision,
                        cause,
                        canonical_target.date if cause is RebalanceCause.TARGET else current_date,
                    )
        if pending is not None:
            raise ExecutionError("A target allocation remained unexecuted at the end of the run")

        data_reference = {
            symbol: {
                "source": normalized_data[symbol].source.value,
                "request": normalized_data[symbol].request.cache_identity(),
                "rows": len(normalized_data[symbol].points),
            }
            for symbol in symbols
        }
        open_holdings = self._open_holding_segments(
            ledger,
            normalized_data,
            symbols,
            effective_end_date,
            config.price_field_used,
        )
        return BacktestResult(
            start_date=effective_start_date,
            end_date=effective_end_date,
            initial_capital=config.initial_capital,
            final_equity=equity_curve[-1].total_equity,
            equity_curve=tuple(equity_curve),
            orders=tuple(orders),
            fills=tuple(fills),
            trades=tuple(trades),
            positions=tuple(snapshots),
            allocation_history=tuple(allocation_history),
            cash_history=tuple(cash_history),
            strategy_version_id=config.strategy_version_id,
            configuration_snapshot=MappingProxyType(
                config.snapshot(
                    data_reference,
                    effective_start_date=effective_start_date,
                    effective_end_date=effective_end_date,
                )
            ),
            data_snapshot_reference=MappingProxyType(data_reference),
            requested_start_date=config.start_date,
            requested_end_date=config.end_date,
            effective_start_date=effective_start_date,
            effective_end_date=effective_end_date,
            contribution_events=contribution_events,
            external_cash_flows=tuple(external_cash_flows),
            cumulative_contributions=sum(float(item.amount) for item in contribution_events),
            holding_segments=tuple((*closed_holdings, *open_holdings)),
            rebalance_decisions=tuple(rebalance_decisions),
        )

    def _validate_config(self, config: BacktestConfig) -> None:
        if not isinstance(config.price_field_used, PriceField):
            raise BacktestConfigurationError("price_field_used must be a PriceField value")
        if not hasattr(config.execution_rule, "value"):
            raise BacktestConfigurationError("execution_rule must be an ExecutionRule value")
        if config.execution_rule.value != "next_trading_day_open":
            raise BacktestConfigurationError("only next_trading_day_open is supported")
        if not hasattr(config.rebalance_policy.frequency, "value"):
            raise BacktestConfigurationError(
                "rebalance frequency must be a RebalanceFrequency value"
            )

    def _prepare_data(
        self, data: Mapping[str, HistoricalDataSet], config: BacktestConfig
    ) -> dict[str, _DataView]:
        if not data:
            raise MissingMarketDataError("at least one market data set is required")
        result: dict[str, _DataView] = {}
        for supplied_symbol, dataset in data.items():
            symbol = supplied_symbol.strip().upper()
            if symbol != dataset.request.symbol:
                raise MissingMarketDataError(
                    f"data key {supplied_symbol!r} does not match {dataset.request.symbol!r}"
                )
            try:
                validate_historical_data(dataset)
            except DataValidationError as exc:
                raise BacktestConfigurationError(
                    f"invalid market data for {symbol}: {exc}"
                ) from exc
            points = tuple(
                point
                for point in dataset.points
                if config.start_date <= point.date <= config.end_date
            )
            result[symbol] = _DataView(
                request=dataset.request,
                points=points,
                source=dataset.source,
                points_by_date={point.date: point for point in points},
            )
        return result

    def _trading_dates(
        self, data: Mapping[str, _DataView], config: BacktestConfig
    ) -> tuple[date, ...]:
        date_sets = [set(dataset.points_by_date) for dataset in data.values()]
        common_dates = set.intersection(*date_sets)
        dates = tuple(sorted(common_dates))
        if not dates:
            raise MissingMarketDataError(
                "effective trading range is empty: no common trading dates"
            )
        if len(dates) < 2:
            raise ExecutionError("at least two trading dates are required for next-open execution")
        return dates

    def _prepare_allocations(
        self,
        allocations: Sequence[TargetAllocation],
        symbols: Sequence[str],
        config: BacktestConfig,
    ) -> tuple[TargetAllocation, ...]:
        result = tuple(allocations)
        previous_date: date | None = None
        symbol_set = set(symbols)
        for allocation in result:
            if not config.start_date <= allocation.date <= config.end_date:
                raise InvalidTargetAllocationError(
                    f"target date {allocation.date.isoformat()} is outside the backtest range"
                )
            if previous_date is not None and allocation.date <= previous_date:
                raise InvalidTargetAllocationError(
                    "target allocations must be strictly date-ordered"
                )
            unknown = {item.symbol for item in allocation.weights} - symbol_set
            if unknown:
                raise MissingMarketDataError(
                    "target allocation references missing market data: "
                    + ", ".join(sorted(unknown))
                )
            previous_date = allocation.date
        return result

    def _scheduled_allocations(
        self,
        allocations: Sequence[TargetAllocation],
        trading_dates: Sequence[date],
        symbols: Sequence[str],
        config: BacktestConfig,
    ) -> dict[date, TargetAllocation]:
        frequency = config.rebalance_policy.frequency.value
        if frequency == "daily":
            return {allocation.date: allocation for allocation in allocations}
        scheduled: dict[date, TargetAllocation] = {}
        last_weights: tuple[float, ...] | None = None
        seen_periods: set[tuple[int, int]] = set()
        for allocation in allocations:
            if frequency == "on_signal_change":
                weights = tuple(allocation.weight_for(symbol) for symbol in sorted(symbols))
                if weights == last_weights:
                    continue
                last_weights = weights
                scheduled[allocation.date] = allocation
                continue
            period = (
                (
                    allocation.date.isocalendar().year,
                    allocation.date.isocalendar().week,
                )
                if frequency == "weekly"
                else (allocation.date.year, allocation.date.month)
            )
            if period not in seen_periods:
                scheduled[allocation.date] = allocation
                seen_periods.add(period)
        return scheduled

    def _rebalance(
        self,
        *,
        ledger: _Ledger,
        target: TargetAllocation,
        signal_date: date,
        execution_date: date,
        data: Mapping[str, _DataView],
        symbols: Sequence[str],
        config: BacktestConfig,
        cause: RebalanceCause,
    ) -> tuple[list[Order], list[Fill], list[Trade], list[HoldingSegment]]:
        prices = {
            symbol: self._valid_price(
                data[symbol].points_by_date[execution_date].open_for(config.price_field_used)
            )
            for symbol in symbols
        }
        equity_at_open = ledger.cash + sum(
            ledger.quantities[symbol] * prices[symbol] for symbol in symbols
        )
        threshold = config.rebalance_policy.threshold
        if threshold is not None and equity_at_open > _EPSILON:
            current_weights = {
                symbol: ledger.quantities[symbol] * prices[symbol] / equity_at_open
                for symbol in symbols
            }
            if all(
                abs(target.weight_for(symbol) - current_weights[symbol]) < threshold
                for symbol in symbols
            ):
                return [], [], [], []
        desired = {
            symbol: math.floor(equity_at_open * target.weight_for(symbol) / prices[symbol])
            for symbol in symbols
        }
        current = {symbol: ledger.quantities[symbol] for symbol in symbols}
        sell_orders = [
            (symbol, current[symbol] - desired[symbol])
            for symbol in symbols
            if current[symbol] > desired[symbol]
        ]
        buy_orders = [
            (symbol, desired[symbol] - current[symbol])
            for symbol in symbols
            if desired[symbol] > current[symbol]
        ]
        result_orders: list[Order] = []
        result_fills: list[Fill] = []
        result_trades: list[Trade] = []
        result_holdings: list[HoldingSegment] = []
        order_number = 0
        for symbol, quantity in sell_orders:
            order_number += 1
            order, fill, closed, holdings = self._execute_order(
                ledger=ledger,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                signal_date=signal_date,
                execution_date=execution_date,
                market_price=prices[symbol],
                target_weight=target.weight_for(symbol),
                config=config,
                order_number=order_number,
                cause=cause,
            )
            result_orders.append(order)
            result_fills.append(fill)
            result_trades.extend(closed)
            result_holdings.extend(holdings)
        for symbol, quantity in buy_orders:
            order_number += 1
            affordable = self._affordable_quantity(
                cash=ledger.cash,
                market_price=prices[symbol],
                requested_quantity=quantity,
                config=config,
                minimum_cash_reserve=(
                    equity_at_open * config.position_rebalance_policy.minimum_cash_reserve
                ),
            )
            if affordable <= 0:
                if (
                    quantity > 0
                    and cause is not RebalanceCause.CONTRIBUTION
                    and config.position_rebalance_policy.minimum_cash_reserve == 0.0
                ):
                    raise InsufficientCashError(
                        f"insufficient cash for BUY {symbol} at {execution_date.isoformat()}"
                    )
                continue
            order, fill, closed, holdings = self._execute_order(
                ledger=ledger,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=affordable,
                signal_date=signal_date,
                execution_date=execution_date,
                market_price=prices[symbol],
                target_weight=target.weight_for(symbol),
                config=config,
                order_number=order_number,
                cause=cause,
            )
            result_orders.append(order)
            result_fills.append(fill)
            result_trades.extend(closed)
            result_holdings.extend(holdings)
        return result_orders, result_fills, result_trades, result_holdings

    def _execute_order(
        self,
        *,
        ledger: _Ledger,
        symbol: str,
        side: OrderSide,
        quantity: int,
        signal_date: date,
        execution_date: date,
        market_price: float,
        target_weight: float,
        config: BacktestConfig,
        order_number: int,
        cause: RebalanceCause,
    ) -> tuple[Order, Fill, list[Trade], list[HoldingSegment]]:
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise InvalidQuantityError("order quantity must be a positive integer")
        if side is OrderSide.SELL and quantity > ledger.quantities[symbol]:
            raise InvalidQuantityError(f"cannot sell more {symbol} shares than are held")
        execution_price = (
            market_price * (1 + config.slippage)
            if side is OrderSide.BUY
            else market_price * (1 - config.slippage)
        )
        self._valid_price(execution_price)
        notional = quantity * execution_price
        commission = notional * config.commission.rate + config.commission.per_order
        slippage_amount = quantity * abs(execution_price - market_price)
        order_id = f"{signal_date.isoformat()}-{execution_date.isoformat()}-{order_number:04d}"
        if side is OrderSide.BUY:
            cash_effect = -(notional + commission)
            if ledger.cash + cash_effect < -_EPSILON:
                raise InsufficientCashError(f"insufficient cash for BUY {symbol}")
            ledger.cash += cash_effect
            ledger.quantities[symbol] += quantity
            ledger.lots[symbol].append(
                _Lot(
                    lot_id=_stable_identity("lot", symbol, order_id),
                    entry_order_id=order_id,
                    entry_signal_date=(signal_date if cause is RebalanceCause.TARGET else None),
                    entry_date=execution_date,
                    entry_price=execution_price + commission / quantity,
                    entry_cause=cause,
                    quantity=quantity,
                )
            )
        else:
            cash_effect = notional - commission
            ledger.cash += cash_effect
            ledger.quantities[symbol] -= quantity
        order = Order(
            order_id=order_id,
            signal_date=signal_date,
            date=execution_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            requested_price=market_price,
            execution_price=execution_price,
            status=OrderStatus.FILLED,
            commission=commission,
            slippage=slippage_amount,
            target_weight=target_weight,
            rebalance_cause=cause,
        )
        fill = Fill(
            order_id=order_id,
            date=execution_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=execution_price,
            commission=commission,
            slippage=slippage_amount,
            cash_effect=cash_effect,
        )
        closed, holdings = (
            self._close_trades(
                ledger,
                symbol,
                quantity,
                signal_date,
                execution_date,
                execution_price,
                commission,
                order_id,
                cause,
            )
            if side is OrderSide.SELL
            else ([], [])
        )
        return order, fill, closed, holdings

    def _close_trades(
        self,
        ledger: _Ledger,
        symbol: str,
        quantity: int,
        exit_signal_date: date,
        exit_date: date,
        exit_price: float,
        exit_commission: float,
        exit_order_id: str,
        exit_cause: RebalanceCause,
    ) -> tuple[list[Trade], list[HoldingSegment]]:
        remaining = quantity
        trades: list[Trade] = []
        holdings: list[HoldingSegment] = []
        lots = ledger.lots[symbol]
        while remaining:
            if not lots:
                raise InvalidQuantityError(f"no lots available for {symbol}")
            lot = lots[0]
            closed_quantity = min(remaining, lot.quantity)
            entry_cost = lot.entry_price * closed_quantity
            exit_proceeds = (
                exit_price * closed_quantity - exit_commission * closed_quantity / quantity
            )
            pnl = exit_proceeds - entry_cost
            holding_return = pnl / entry_cost if entry_cost else 0.0
            holding_days = (exit_date - lot.entry_date).days
            trade = Trade(
                symbol=symbol,
                entry_date=lot.entry_date,
                exit_date=exit_date,
                entry_price=lot.entry_price,
                exit_price=exit_price,
                quantity=closed_quantity,
                pnl=pnl,
                pnl_pct=holding_return,
                holding_period=holding_days,
            )
            lot.closed_segment_count += 1
            trades.append(trade)
            holdings.append(
                HoldingSegment(
                    holding_id=_stable_identity(
                        "holding",
                        lot.lot_id,
                        exit_order_id,
                        str(lot.closed_segment_count),
                    ),
                    lot_id=lot.lot_id,
                    symbol=symbol,
                    quantity=closed_quantity,
                    entry_order_id=lot.entry_order_id,
                    entry_signal_date=lot.entry_signal_date,
                    entry_execution_date=lot.entry_date,
                    entry_price=lot.entry_price,
                    entry_execution_cause=lot.entry_cause,
                    status=HoldingStatus.CLOSED,
                    holding_days=holding_days,
                    exit_order_id=exit_order_id,
                    exit_signal_date=(
                        exit_signal_date if exit_cause is RebalanceCause.TARGET else None
                    ),
                    exit_execution_date=exit_date,
                    exit_price=exit_price,
                    exit_execution_cause=exit_cause,
                    realized_pnl=pnl,
                    holding_return=holding_return,
                )
            )
            lot.quantity -= closed_quantity
            remaining -= closed_quantity
            if lot.quantity == 0:
                lots.pop(0)
        return trades, holdings

    def _open_holding_segments(
        self,
        ledger: _Ledger,
        data: Mapping[str, _DataView],
        symbols: Sequence[str],
        report_end_date: date,
        price_field: PriceField,
    ) -> list[HoldingSegment]:
        result: list[HoldingSegment] = []
        for symbol in symbols:
            ending_price = self._valid_price(
                data[symbol].points_by_date[report_end_date].close_for(price_field)
            )
            for lot in ledger.lots[symbol]:
                market_value = lot.quantity * ending_price
                basis = lot.quantity * lot.entry_price
                unrealized_pnl = market_value - basis
                result.append(
                    HoldingSegment(
                        holding_id=_stable_identity("holding", lot.lot_id, "OPEN"),
                        lot_id=lot.lot_id,
                        symbol=symbol,
                        quantity=lot.quantity,
                        entry_order_id=lot.entry_order_id,
                        entry_signal_date=lot.entry_signal_date,
                        entry_execution_date=lot.entry_date,
                        entry_price=lot.entry_price,
                        entry_execution_cause=lot.entry_cause,
                        status=HoldingStatus.OPEN,
                        holding_days=(report_end_date - lot.entry_date).days,
                        report_end_date=report_end_date,
                        ending_price=ending_price,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                        holding_return=unrealized_pnl / basis if basis else 0.0,
                    )
                )
        return result

    def _affordable_quantity(
        self,
        *,
        cash: float,
        market_price: float,
        requested_quantity: int,
        config: BacktestConfig,
        minimum_cash_reserve: float = 0.0,
    ) -> int:
        unit_cost = market_price * (1 + config.slippage) * (1 + config.commission.rate)
        fixed_cost = config.commission.per_order
        available_cash = cash - max(0.0, minimum_cash_reserve)
        if available_cash < fixed_cost + unit_cost - _EPSILON:
            return 0
        return min(
            requested_quantity, max(0, math.floor((available_cash - fixed_cost) / unit_cost))
        )

    def _mark_to_market(
        self,
        ledger: _Ledger,
        as_of_date: date,
        points: Mapping[str, MarketDataPoint],
        price_field: PriceField,
    ) -> tuple[EquityPoint, PortfolioSnapshot]:
        asset_values: dict[str, float] = {}
        positions: list[Position] = []
        for symbol in sorted(points):
            price = self._valid_price(points[symbol].close_for(price_field))
            quantity = ledger.quantities[symbol]
            if quantity <= 0:
                continue
            market_value = quantity * price
            average_cost = (
                sum(lot.entry_price * lot.quantity for lot in ledger.lots[symbol]) / quantity
            )
            asset_values[symbol] = market_value
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=average_cost,
                    market_price=price,
                    market_value=market_value,
                    unrealized_pnl=market_value - average_cost * quantity,
                    as_of_date=as_of_date,
                )
            )
        total_equity = ledger.cash + sum(asset_values.values())
        equity = EquityPoint(
            date=as_of_date,
            cash=ledger.cash,
            asset_values=MappingProxyType(asset_values),
            total_equity=total_equity,
        )
        return equity, PortfolioSnapshot(
            as_of_date=as_of_date,
            cash=ledger.cash,
            positions=tuple(positions),
            total_equity=total_equity,
        )

    def _allocation_points(
        self,
        as_of_date: date,
        target: TargetAllocation,
        ledger: _Ledger,
        points: Mapping[str, MarketDataPoint],
        symbols: Sequence[str],
        price_field: PriceField,
        total_equity: float,
    ) -> list[AllocationPoint]:
        result: list[AllocationPoint] = []
        for symbol in symbols:
            value = ledger.quantities[symbol] * points[symbol].close_for(price_field)
            actual_weight = value / total_equity if total_equity > _EPSILON else 0.0
            result.append(
                AllocationPoint(
                    date=as_of_date,
                    symbol=symbol,
                    target_weight=target.weight_for(symbol),
                    actual_weight=actual_weight,
                )
            )
        return result

    @staticmethod
    def _valid_price(price: float) -> float:
        if (
            not isinstance(price, (int, float))
            or isinstance(price, bool)
            or not math.isfinite(price)
            or price <= 0
        ):
            raise InvalidPriceError("market price must be finite and positive")
        return float(price)


@dataclass(frozen=True)
class _DataView:
    """Indexed view of a validated data set limited to the run date range."""

    request: HistoricalDataRequest
    points: tuple[MarketDataPoint, ...]
    source: DataSource
    points_by_date: Mapping[date, MarketDataPoint]
