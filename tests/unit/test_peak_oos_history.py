from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from backend.app import oos_workflow_api
from backend.app.backtest_service import _warmup_start as backtest_warmup_start
from backtest.integration import run_strategy_backtest
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators.peak_drawdown import peak_drawdown
from indicators.preparation import prepare_strategy_inputs
from research.data_availability import history_requirement_for_strategy
from research.execution_service import _warmup_start as experiment_warmup_start
from research.oos_execution_service import OosExecutionService
from strategies import EvaluationContext, evaluate_strategy
from tests.unit.test_oos_domain import _protocol, _version
from tests.unit.test_peak_drawdown import _entry_version

IS_START = date(2007, 1, 1)
OOS_START = date(2021, 1, 4)
OOS_END = date(2021, 1, 6)
HISTORY_START = date(1900, 1, 1)


def _point(day: date, close: float) -> MarketDataPoint:
    return MarketDataPoint(
        date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        adj_open=close,
        adj_high=close,
        adj_low=close,
        adj_close=close,
        adj_volume=100.0,
        div_cash=0.0,
        split_factor=1.0,
    )


class HistoricalFixture:
    def __init__(self) -> None:
        self.requests: list[HistoricalDataRequest] = []

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        closes = (
            (date(2005, 1, 3), 500.0),
            (date(2007, 1, 2), 450.0),
            (OOS_START, 350.0),
            (date(2021, 1, 5), 340.0),
            (OOS_END, 330.0),
            (date(2021, 1, 7), 600.0),
        )
        if request.symbol != "SPY":
            closes = tuple((day, 100.0) for day, _ in closes)
        points = tuple(
            _point(day, close)
            for day, close in closes
            if request.start_date <= day <= request.end_date
        )
        return HistoricalDataSet(request, points, DataSource.CACHE)


def _spec(
    monkeypatch: pytest.MonkeyPatch,
    version,
    *,
    is_start: date = IS_START,
    oos_start: date = OOS_START,
):
    base = _protocol()
    protocol = replace(
        base,
        is_start_date=is_start,
        is_end_date=date(2020, 12, 31),
        oos_start_date=oos_start,
        oos_end_date=OOS_END,
        evaluation_config=replace(
            base.evaluation_config,
            rebalance_policy={
                "frequency": version.configuration.rebalance_policy.frequency.value,
                "threshold": version.configuration.rebalance_policy.threshold,
            },
        ),
    )
    monkeypatch.setattr(oos_workflow_api, "_protocol_selection_id", lambda _: "selection-test")
    return oos_workflow_api._spec(protocol, SimpleNamespace(freeze_id="freeze-test"), version)


def test_peak_oos_matches_pre_is_history_and_isolates_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _entry_version()
    spec = _spec(monkeypatch, version)
    assert history_requirement_for_strategy(version).value == "ALL_TIME_TO_DATE"
    assert backtest_warmup_start(IS_START, version) == HISTORY_START
    assert experiment_warmup_start(IS_START, version) == HISTORY_START
    assert spec.evaluation_range.warmup_start == HISTORY_START
    assert spec.evaluation_range.oos_start == OOS_START
    assert spec.evaluation_range.evaluation_start == OOS_START
    assert spec.identity.strategy_version_id == version.version_id
    assert spec.identity.strategy_content_hash == version.content_hash

    fixture = HistoricalFixture()
    service = OosExecutionService(
        protocol_repository=None,
        execution_repository=None,
        strategy_repository=None,
        data_service=fixture,
    )
    data, provenance = service._load_data(version, spec)
    assert all(request.start_date == HISTORY_START for request in fixture.requests)
    assert all(request.end_date == OOS_END for request in fixture.requests)
    assert data["SPY"].points[0].date == date(2005, 1, 3)
    assert provenance.data_reference["assets"]["SPY"]["first_available_date"] == "2005-01-03"
    assert provenance.data_reference["history_requirement"] == "ALL_TIME_TO_DATE"

    direct = peak_drawdown(data["SPY"], price_field=PriceField.ADJUSTED_CLOSE)
    prepared = prepare_strategy_inputs(version, data, price_field=PriceField.ADJUSTED_CLOSE)
    oos_prepared = service._prepare_inputs(version, data, PriceField.ADJUSTED_CLOSE, OOS_END)
    for preparation_start in (
        backtest_warmup_start(IS_START, version),
        experiment_warmup_start(IS_START, version),
    ):
        prepared_data = dict(data)
        prepared_data["SPY"] = fixture.get_history(
            HistoricalDataRequest(
                "SPY", preparation_start, OOS_END, price_field_used=PriceField.ADJUSTED_CLOSE
            )
        )
        context_result = prepare_strategy_inputs(
            version, prepared_data, price_field=PriceField.ADJUSTED_CLOSE
        )
        assert context_result.indicators == oos_prepared.indicators
    assert direct.points[2].value == pytest.approx(-0.3)
    assert direct.points[2].value != pytest.approx(350 / 450 - 1)
    assert prepared.indicators == oos_prepared.indicators

    context = EvaluationContext.from_components(data, oos_prepared.indicators)
    timeline = evaluate_strategy(version, context, OOS_START, OOS_END)
    assert timeline.evaluations[0].regime_provenance["active_state_id"] == "VALUE"
    result = run_strategy_backtest(
        version, timeline, data, service._backtest_config(version.version_id, spec)
    ).backtest_result
    assert result is not None
    assert result.start_date == OOS_START
    assert result.end_date == OOS_END
    assert all(OOS_START <= point.date <= OOS_END for point in result.equity_curve)


def test_non_peak_oos_keeps_frozen_is_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    version = _version()
    spec = _spec(monkeypatch, version)
    assert history_requirement_for_strategy(version).value == "FIXED_WARMUP"
    assert spec.evaluation_range.warmup_start == IS_START
    assert "history_requirement" not in spec.data_provenance.data_reference


def test_peak_history_is_independent_of_is_oos_start_and_future_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _entry_version()
    fixture = HistoricalFixture()
    service = OosExecutionService(
        protocol_repository=None,
        execution_repository=None,
        strategy_repository=None,
        data_service=fixture,
    )
    values = []
    for is_start, oos_start in (
        (date(2007, 1, 1), OOS_START),
        (date(2010, 1, 1), date(2021, 1, 5)),
    ):
        spec = _spec(monkeypatch, version, is_start=is_start, oos_start=oos_start)
        data, _ = service._load_data(version, spec)
        series = peak_drawdown(data["SPY"], price_field=PriceField.ADJUSTED_CLOSE)
        values.append(series.points[3].value)
        assert spec.evaluation_range.warmup_start == HISTORY_START
    assert values == pytest.approx([-0.32, -0.32])
    assert all(request.end_date == OOS_END for request in fixture.requests)

    with_future = fixture.get_history(
        HistoricalDataRequest(
            "SPY", HISTORY_START, date(2021, 1, 7), price_field_used=PriceField.ADJUSTED_CLOSE
        )
    )
    assert peak_drawdown(with_future, price_field=PriceField.ADJUSTED_CLOSE).points[
        3
    ].value == pytest.approx(-0.32)


@pytest.mark.parametrize("price_field", [PriceField.ADJUSTED_CLOSE, PriceField.RAW_CLOSE])
def test_peak_oos_requests_frozen_price_field(
    monkeypatch: pytest.MonkeyPatch, price_field: PriceField
) -> None:
    version = _entry_version()
    spec = _spec(monkeypatch, version)
    config = replace(spec.configuration, price_field_used=price_field)
    provenance = replace(spec.data_provenance, price_field=price_field)
    spec = replace(spec, configuration=config, data_provenance=provenance)
    fixture = HistoricalFixture()
    service = OosExecutionService(
        protocol_repository=None,
        execution_repository=None,
        strategy_repository=None,
        data_service=fixture,
    )
    data, _ = service._load_data(version, spec)
    assert all(request.price_field_used is price_field for request in fixture.requests)
    assert peak_drawdown(data["SPY"], price_field=price_field).points[2].value == pytest.approx(
        -0.3
    )
