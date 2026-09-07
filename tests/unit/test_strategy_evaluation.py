from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import exponential_moving_average, moving_average
from indicators.models import IndicatorSeries
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
    StrategyEvaluationResult,
    StrategyEvaluationStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
    evaluate_strategy,
)
from strategies.exceptions import StrategyEvaluationInputError

START = date(2026, 1, 5)


def _dataset(
    symbol: str,
    prices: tuple[float, ...],
    *,
    days: tuple[int, ...] | None = None,
    price_field: PriceField = PriceField.RAW_CLOSE,
) -> HistoricalDataSet:
    offsets = days or tuple(range(len(prices)))
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=offset),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            adj_open=price * 2,
            adj_high=price * 2,
            adj_low=price * 2,
            adj_close=price * 2,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for offset, price in zip(offsets, prices, strict=True)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol,
            points[0].date,
            points[-1].date,
            price_field_used=price_field,
        ),
        points,
        DataSource.API_FRESH,
    )


def _price(symbol: str, field: PriceField = PriceField.RAW_CLOSE) -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=field)


def _ma(symbol: str, period: int, field: PriceField = PriceField.RAW_CLOSE) -> Operand:
    return Operand(symbol, OperandType.MA, period=period, price_field=field)


def _ema(symbol: str, period: int, field: PriceField = PriceField.RAW_CLOSE) -> Operand:
    return Operand(symbol, OperandType.EMA, period=period, price_field=field)


def _rule(
    rule_id: str,
    priority: int,
    symbol: str,
    condition: Condition | RuleGroup | None,
) -> AllocationRule:
    return AllocationRule(rule_id, rule_id, priority, (Allocation(symbol, 1.0),), condition)


def _version(
    *,
    assets: tuple[str, ...] = ("QQQ", "TQQQ", "SGOV"),
    rules: tuple[AllocationRule, ...],
    price_field: PriceField = PriceField.RAW_CLOSE,
) -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="evaluation-test",
        name="Evaluation Test",
        description="PHASE 4G test fixture",
        assets=tuple(AssetReference(symbol) for symbol in assets),
        price_field=price_field,
        rules=rules,
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),), name="risk-off"),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="evaluation-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )


def _context(
    *datasets: HistoricalDataSet,
    period: int | None = None,
    field: PriceField = PriceField.RAW_CLOSE,
    include_ema: bool = False,
) -> EvaluationContext:
    indicators = []
    if period is not None:
        for dataset in datasets:
            indicators.append(
                (dataset.request.symbol, moving_average(dataset, period=period, price_field=field))
            )
            if include_ema:
                indicators.append(
                    (
                        dataset.request.symbol,
                        exponential_moving_average(dataset, period=period, price_field=field),
                    )
                )
    return EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets}, indicators
    )


def test_single_and_multi_date_timeline_keeps_rule_changes_and_fallback() -> None:
    datasets = tuple(
        _dataset(symbol, (10.0, 20.0, 30.0, 5.0)) for symbol in ("QQQ", "TQQQ", "SGOV")
    )
    risk_on = _rule(
        "risk-on",
        100,
        "TQQQ",
        RuleGroup(
            LogicalOperator.AND,
            (Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 2)),),
        ),
    )
    defensive = _rule(
        "defensive",
        90,
        "QQQ",
        Condition(_price("QQQ"), ComparisonOperator.LESS_THAN, _ma("QQQ", 2)),
    )
    timeline = evaluate_strategy(
        _version(rules=(risk_on, defensive)),
        _context(*datasets, period=2),
        START,
        START + timedelta(days=3),
        source_data_reference="unit-fixture",
    )

    assert [item.status for item in timeline.evaluations] == [
        StrategyEvaluationStatus.NOT_EVALUABLE,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
    ]
    assert [item.matched_rule_id for item in timeline.evaluations] == [
        None,
        "risk-on",
        "risk-on",
        "defensive",
    ]
    assert all(len(item.rule_group_results) == 2 for item in timeline.evaluations[1:])
    assert timeline.evaluations[1].signal is not None
    assert timeline.evaluations[1].signal.condition_results is not None
    assert len(timeline.evaluations[1].signal.condition_results.child_results) == 2
    assert timeline.to_dict()["evaluations"][1]["source_data_reference"] == "unit-fixture"


def test_no_match_uses_valid_fallback_not_error() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule(
        "risk-on",
        100,
        "TQQQ",
        Condition(_price("QQQ"), ComparisonOperator.LESS_THAN, _ma("QQQ", 2)),
    )

    result = evaluate_strategy(
        _version(rules=(rule,)),
        _context(*datasets, period=2),
        START + timedelta(days=1),
        START + timedelta(days=1),
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.EVALUATED
    assert result.signal is not None
    assert result.signal.matched_rule_id is None
    assert result.target_allocation is not None
    assert result.target_allocation.used_fallback
    assert result.target_allocation.weights == {"SGOV": 1.0}


def test_multi_asset_dates_use_strict_intersection_without_filling() -> None:
    qqq = _dataset("QQQ", (10.0, 20.0, 30.0), days=(0, 1, 2))
    tqqq = _dataset("TQQQ", (5.0, 10.0), days=(0, 2))
    sgov = _dataset("SGOV", (100.0, 100.0, 100.0), days=(0, 1, 2))
    rule = _rule(
        "cross-asset",
        100,
        "TQQQ",
        Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _price("TQQQ")),
    )

    timeline = evaluate_strategy(
        _version(rules=(rule,)), _context(qqq, tqqq, sgov), START, START + timedelta(days=2)
    )

    assert [item.date for item in timeline.evaluations] == [START, START + timedelta(days=2)]
    assert all(item.status is StrategyEvaluationStatus.EVALUATED for item in timeline.evaluations)


def test_missing_indicator_is_an_error_and_never_becomes_fallback() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0, 30.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule(
        "ma-200",
        100,
        "TQQQ",
        Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 200)),
    )
    context = _context(*datasets)
    qqq_ma = moving_average(datasets[0], period=3, price_field=PriceField.RAW_CLOSE)
    context = EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets}, (("QQQ", qqq_ma),)
    )

    result = evaluate_strategy(
        _version(rules=(rule,)), context, START, START + timedelta(days=2)
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.ERROR
    assert result.failure is not None
    assert result.failure.code == "MISSING_INDICATOR"
    assert result.signal is None


def test_ma200_warmup_is_not_evaluable_and_never_becomes_fallback() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0, 30.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule(
        "ma-200",
        100,
        "TQQQ",
        Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 200)),
    )
    context = EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets},
        (("QQQ", moving_average(datasets[0], period=200, price_field=PriceField.RAW_CLOSE)),),
    )

    timeline = evaluate_strategy(_version(rules=(rule,)), context, START, START + timedelta(days=2))

    assert all(
        item.status is StrategyEvaluationStatus.NOT_EVALUABLE for item in timeline.evaluations
    )
    assert all(
        item.failure is not None and item.failure.code == "INDICATOR_WARMUP"
        for item in timeline.evaluations
    )
    assert all(
        item.target_allocation is None and item.signal is None for item in timeline.evaluations
    )


def test_existing_indicator_warmup_produces_not_evaluable_for_ma_and_ema() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0, 30.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule(
        "trend",
        100,
        "TQQQ",
        RuleGroup(
            LogicalOperator.AND,
            (
                Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 3)),
                Condition(_ema("QQQ", 3), ComparisonOperator.GREATER_THAN, _ma("QQQ", 3)),
            ),
        ),
    )
    timeline = evaluate_strategy(
        _version(rules=(rule,)),
        _context(*datasets, period=3, include_ema=True),
        START,
        START + timedelta(days=2),
    )

    assert [item.status for item in timeline.evaluations] == [
        StrategyEvaluationStatus.NOT_EVALUABLE,
        StrategyEvaluationStatus.NOT_EVALUABLE,
        StrategyEvaluationStatus.EVALUATED,
    ]
    assert all(item.signal is None for item in timeline.evaluations[:2])


def test_no_lookahead_and_prefix_consistency() -> None:
    rules = (
        _rule(
            "risk-on",
            100,
            "TQQQ",
            Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 2)),
        ),
    )
    prefix = tuple(_dataset(symbol, (10.0, 20.0, 30.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    full = tuple(
        _dataset(symbol, (10.0, 20.0, 30.0, 1_000_000.0)) for symbol in ("QQQ", "TQQQ", "SGOV")
    )
    version = _version(rules=rules)
    prefix_timeline = evaluate_strategy(
        version, _context(*prefix, period=2), START, START + timedelta(days=2)
    )
    full_timeline = evaluate_strategy(
        version, _context(*full, period=2), START, START + timedelta(days=3)
    )

    assert full_timeline.evaluations[:3] == prefix_timeline.evaluations
    assert full_timeline.evaluations[2].signal == prefix_timeline.evaluations[2].signal


def test_price_field_mismatch_is_an_error_not_a_conversion() -> None:
    datasets = tuple(
        _dataset(symbol, (10.0, 20.0), price_field=PriceField.RAW_CLOSE)
        for symbol in ("QQQ", "TQQQ", "SGOV")
    )
    field = PriceField.ADJUSTED_CLOSE
    rule = _rule(
        "adjusted",
        100,
        "TQQQ",
        Condition(_price("QQQ", field), ComparisonOperator.GREATER_THAN, _price("TQQQ", field)),
    )

    timeline = evaluate_strategy(
        _version(rules=(rule,), price_field=field),
        _context(*datasets, field=field),
        START,
        START + timedelta(days=1),
    )

    assert all(item.status is StrategyEvaluationStatus.ERROR for item in timeline.evaluations)
    assert all(
        item.failure is not None and item.failure.code == "PRICE_FIELD_MISMATCH"
        for item in timeline.evaluations
    )
    assert all(item.signal is None for item in timeline.evaluations)


def test_indicator_price_field_mismatch_is_an_error_not_a_missing_indicator() -> None:
    field = PriceField.ADJUSTED_CLOSE
    datasets = tuple(
        _dataset(symbol, (10.0, 20.0), price_field=field) for symbol in ("QQQ", "TQQQ", "SGOV")
    )
    rule = _rule(
        "adjusted-ma",
        100,
        "TQQQ",
        Condition(_price("QQQ", field), ComparisonOperator.GREATER_THAN, _ma("QQQ", 2, field)),
    )

    result = evaluate_strategy(
        _version(rules=(rule,), price_field=field),
        _context(*datasets, period=2, field=PriceField.RAW_CLOSE),
        START,
        START,
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.ERROR
    assert result.failure is not None
    assert result.failure.code == "PRICE_FIELD_MISMATCH"
    assert result.signal is None


def test_upstream_condition_error_is_not_converted_to_fallback() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    bad = Condition(
        _price("QQQ"),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.CONSTANT, value=0),
        Threshold(ThresholdType.RELATIVE, 0.01),
    )
    result = evaluate_strategy(
        _version(rules=(_rule("bad", 100, "TQQQ", bad),)),
        _context(*datasets),
        START,
        START,
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.ERROR
    assert result.failure is not None
    assert result.failure.code == "RULE_GROUP_EVALUATION_ERROR"
    assert result.target_allocation is None
    assert result.signal is None


def test_indicator_date_gap_is_error_and_unsorted_market_data_is_rejected() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0, 30.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule(
        "ma",
        100,
        "TQQQ",
        Condition(_price("QQQ"), ComparisonOperator.GREATER_THAN, _ma("QQQ", 2)),
    )
    complete = moving_average(datasets[0], period=2, price_field=PriceField.RAW_CLOSE)
    gapped = IndicatorSeries(
        complete.kind,
        complete.period,
        complete.price_field_used,
        complete.points[:2],
    )
    gapped_context = EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets}, (("QQQ", gapped),)
    )

    gap_result = evaluate_strategy(
        _version(rules=(rule,)),
        gapped_context,
        START + timedelta(days=2),
        START + timedelta(days=2),
    ).evaluations[0]

    assert gap_result.status is StrategyEvaluationStatus.ERROR
    assert gap_result.failure is not None
    assert gap_result.failure.code == "MISSING_INDICATOR_DATE"

    unsorted = HistoricalDataSet(
        datasets[0].request,
        tuple(reversed(datasets[0].points)),
        datasets[0].source,
    )
    malformed_context = EvaluationContext.from_components(
        {"QQQ": unsorted, "TQQQ": datasets[1], "SGOV": datasets[2]},
        (),
    )
    with pytest.raises(StrategyEvaluationInputError, match="dates are not strictly ascending"):
        evaluate_strategy(
            _version(rules=(_rule("always", 100, "QQQ", None),)),
            malformed_context,
            START,
            START,
        )


def test_unconditional_strategy_is_evaluable_without_inventing_condition_results() -> None:
    datasets = tuple(_dataset(symbol, (10.0,)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    result = evaluate_strategy(
        _version(rules=(_rule("always", 100, "QQQ", None),)), _context(*datasets), START, START
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.EVALUATED
    assert result.rule_group_results == ()
    assert result.signal is not None
    assert result.signal.condition_results is None
    assert result.signal.matched_rule_id == "always"


def test_invalid_range_missing_asset_determinism_and_immutability() -> None:
    datasets = tuple(_dataset(symbol, (10.0, 20.0)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    rule = _rule("always", 100, "QQQ", None)
    version = _version(rules=(rule,))
    context = _context(*datasets)
    before = (version, context)

    first = evaluate_strategy(version, context, START, START + timedelta(days=1))
    second = evaluate_strategy(version, context, START, START + timedelta(days=1))

    assert first == second
    assert (version, context) == before
    with pytest.raises(FrozenInstanceError):
        first.evaluations = ()  # type: ignore[misc]
    with pytest.raises(StrategyEvaluationInputError, match="start_date"):
        evaluate_strategy(version, context, START + timedelta(days=1), START)
    missing_context = EvaluationContext.from_components({"QQQ": datasets[0]}, ())
    with pytest.raises(StrategyEvaluationInputError, match="declared asset TQQQ"):
        evaluate_strategy(version, missing_context, START, START)


def test_evaluation_result_rejects_signal_from_another_strategy_version() -> None:
    datasets = tuple(_dataset(symbol, (10.0,)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    evaluated = evaluate_strategy(
        _version(rules=(_rule("always", 100, "QQQ", None),)), _context(*datasets), START, START
    ).evaluations[0]
    assert evaluated.signal is not None

    with pytest.raises(ValueError, match="strategy version"):
        StrategyEvaluationResult(
            date=evaluated.date,
            strategy_version_id=evaluated.strategy_version_id,
            status=evaluated.status,
            rule_group_results=evaluated.rule_group_results,
            target_allocation=evaluated.target_allocation,
            signal=replace(evaluated.signal, strategy_version_id="other-version"),
            explanation=evaluated.explanation,
        )


def test_evaluation_result_rejects_signal_with_a_different_allocation() -> None:
    datasets = tuple(_dataset(symbol, (10.0,)) for symbol in ("QQQ", "TQQQ", "SGOV"))
    evaluated = evaluate_strategy(
        _version(rules=(_rule("always", 100, "QQQ", None),)), _context(*datasets), START, START
    ).evaluations[0]
    assert evaluated.signal is not None
    assert evaluated.target_allocation is not None

    with pytest.raises(ValueError, match="target_allocation must match"):
        StrategyEvaluationResult(
            date=evaluated.date,
            strategy_version_id=evaluated.strategy_version_id,
            status=evaluated.status,
            rule_group_results=evaluated.rule_group_results,
            target_allocation=replace(
                evaluated.target_allocation,
                allocations=(Allocation("TQQQ", 1.0),),
                explanation="Different allocation",
            ),
            signal=evaluated.signal,
            explanation=evaluated.explanation,
        )
