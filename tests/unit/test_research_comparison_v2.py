from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from time import perf_counter
from types import MappingProxyType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from analytics.models import DrawdownPoint, MetricValue, WealthPoint
from backend.app import backtest_lab
from backend.app.main import app
from backend.app.research import ComparisonInclude, compare_records
from backtest.models import (
    ContributionEvent,
    ContributionFrequency,
    EquityPoint,
    ExternalCashFlow,
)
from data.models import PriceField
from tests.unit.test_backtest_lab_api import _record


def _payload(*records, include: ComparisonInclude | None = None) -> dict[str, object]:
    return compare_records(tuple(records), include=include).to_dict()


def _replace_config(record, **updates):
    snapshot = dict(record.run.backtest_result.configuration_snapshot)
    snapshot.update(updates)
    result = replace(
        record.run.backtest_result,
        configuration_snapshot=MappingProxyType(snapshot),
    )
    analysis = record.run.performance_analysis
    if "price_field_used" in updates:
        analysis = replace(analysis, price_field_used=PriceField(updates["price_field_used"]))
    return replace(
        record,
        run=replace(record.run, backtest_result=result, performance_analysis=analysis),
    )


def _replace_result(record, *, analysis_updates=None, **result_updates):
    result = replace(record.run.backtest_result, **result_updates)
    analysis = replace(record.run.performance_analysis, **(analysis_updates or {}))
    return replace(
        record,
        run=replace(record.run, backtest_result=result, performance_analysis=analysis),
    )


def _with_dataset_version(record, version: str = "dataset-v1"):
    references = {
        symbol: {**dict(payload), "content_hash": version}
        for symbol, payload in record.run.backtest_result.data_snapshot_reference.items()
    }
    return _replace_result(
        record,
        data_snapshot_reference=MappingProxyType(references),
    )


def _with_symbols(record, symbols: tuple[str, ...]):
    source = next(iter(record.run.backtest_result.data_snapshot_reference.values()))
    return _replace_result(
        record,
        data_snapshot_reference=MappingProxyType({symbol: dict(source) for symbol in symbols}),
    )


def _with_contribution(record, *, amount: str = "100", effective_day: int = 3):
    requested = date(2026, 1, 2)
    effective = date(2026, 1, effective_day)
    event = ContributionEvent(
        frequency=ContributionFrequency.ONE_TIME,
        amount=Decimal(amount),
        requested_date=requested,
        effective_date=effective,
    )
    flow = ExternalCashFlow(date=effective, amount=Decimal(amount))
    result = record.run.backtest_result
    updated = _replace_result(
        record,
        contribution_events=(event,),
        external_cash_flows=(flow,),
        cumulative_contributions=float(amount),
        total_capital_invested=None,
        investment_profit=None,
    )
    return _replace_config(
        updated,
        contribution_schedule={
            "frequency": "one_time",
            "amount": amount,
            "requested_date": requested.isoformat(),
            "currency": "USD",
        },
        initial_capital=result.initial_capital,
    )


def _reason_codes(payload, dimension: str) -> set[str]:
    return set(payload["compatibility"][dimension]["reason_codes"])


def test_two_run_happy_path_is_versioned_and_preserves_request_order() -> None:
    first = _with_dataset_version(_record("run-b"))
    second = _with_dataset_version(_record("run-a"))

    payload = _payload(first, second)

    assert payload["comparison_schema_version"] == "2.1"
    assert payload["ordering"] == "request_order"
    assert [item["backtest_run_id"] for item in payload["runs"]] == ["run-b", "run-a"]
    assert [item["backtest_run_id"] for item in payload["series"]] == ["run-b", "run-a"]
    assert payload["compatibility"]["twr"]["status"] == "COMPARABLE"


def test_strategy_version_metadata_enriches_identity_without_replacing_run_identity() -> None:
    records = (_record("run-a"), _record("run-b"))
    version_id = records[0].run.strategy_version_id

    payload = compare_records(
        records,
        strategy_metadata={
            version_id: {"strategy_name": "Canonical Strategy", "version_number": 7}
        },
    ).to_dict()

    assert payload["runs"][0]["backtest_run_id"] == "run-a"
    assert payload["runs"][0]["strategy_name"] == "Canonical Strategy"
    assert payload["runs"][0]["strategy_version"] == "v7"
    assert payload["runs"][0]["strategy_metadata_status"] == "available"
    assert payload["runs"][0]["short_display_label"].endswith("run-a")


def test_api_accepts_ten_runs_and_preserves_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = tuple(_record(f"run-{index}") for index in range(10))
    monkeypatch.setattr(
        backtest_lab,
        "backtest_repository",
        SimpleNamespace(get_records=lambda run_ids: records),
    )
    monkeypatch.setattr(backtest_lab, "strategy_repository", SimpleNamespace())

    response = TestClient(app).post(
        "/research/comparisons",
        json={"backtest_run_ids": [record.run.backtest_run_id for record in records]},
    )

    assert response.status_code == 200
    assert [item["backtest_run_id"] for item in response.json()["runs"]] == [
        record.run.backtest_run_id for record in records
    ]


def test_twr_is_rebased_from_the_canonical_curve_without_using_equity() -> None:
    curve = (
        WealthPoint(date(2026, 1, 2), 2.0),
        WealthPoint(date(2026, 1, 4), 2.5),
    )
    first = _record("run-a")
    second = _record("run-b")
    first = _replace_result(
        first,
        analysis_updates={"twr_wealth_curve": curve},
        final_equity=9_999_999.0,
    )

    payload = _payload(first, second)
    twr = payload["series"][0]["twr"]

    assert twr["normalization"] == {
        "base_value": 100.0,
        "source": "twr_wealth_curve",
        "method": "rebase_first_valid_point",
        "first_valid_date": "2026-01-02",
        "first_valid_value": 2.0,
    }
    assert twr["points"] == [
        {"date": "2026-01-02", "value": 100.0},
        {"date": "2026-01-04", "value": 125.0},
    ]


def test_drawdown_is_projected_verbatim_from_canonical_analytics() -> None:
    curve = (
        DrawdownPoint(date(2026, 1, 2), 0.0),
        DrawdownPoint(date(2026, 1, 4), -0.125),
    )
    first = _replace_result(
        _record("run-a"),
        analysis_updates={"drawdown_curve": curve},
    )

    payload = _payload(first, _record("run-b"))

    assert payload["series"][0]["drawdown"]["points"] == [
        {"date": "2026-01-02", "value": 0.0},
        {"date": "2026-01-04", "value": -0.125},
    ]


def test_different_timelines_remain_sparse_without_forward_fill() -> None:
    first = _record("run-a")
    second_curve = first.run.performance_analysis.twr_wealth_curve[1:]
    second_drawdown = first.run.performance_analysis.drawdown_curve[1:]
    second = _replace_result(
        _record("run-b"),
        analysis_updates={
            "start_date": second_curve[0].date,
            "twr_wealth_curve": second_curve,
            "drawdown_curve": second_drawdown,
        },
        start_date=second_curve[0].date,
        equity_curve=_record("unused").run.backtest_result.equity_curve[1:],
    )

    payload = _payload(first, second)

    assert len(payload["series"][0]["twr"]["points"]) == 3
    assert len(payload["series"][1]["twr"]["points"]) == 2
    assert payload["series"][1]["twr"]["points"][0]["date"] == "2026-01-03"
    assert "DIFFERENT_DATE_RANGES" in _reason_codes(payload, "twr")


@pytest.mark.parametrize(
    "change, expected_code, expected_status",
    [
        ({"price_field_used": "raw_close"}, "DIFFERENT_PRICE_FIELDS", "INCOMPATIBLE"),
        ({"commission": {"rate": 0.01, "per_order": 0.0}}, "DIFFERENT_COMMISSION", "INCOMPATIBLE"),
        ({"slippage": 0.01}, "DIFFERENT_SLIPPAGE", "INCOMPATIBLE"),
        ({"execution_rule": "same_day_close"}, "DIFFERENT_EXECUTION_SEMANTICS", "INCOMPATIBLE"),
        ({"fractional_shares": True}, "DIFFERENT_FRACTIONAL_SHARE_POLICY", "INCOMPATIBLE"),
        (
            {"rebalance_policy": {"frequency": "monthly", "threshold": None}},
            "DIFFERENT_REBALANCE_CONFIG",
            "WARNING",
        ),
    ],
)
def test_twr_compatibility_reports_execution_context_mismatches(
    change: dict[str, object], expected_code: str, expected_status: str
) -> None:
    payload = _payload(_record("run-a"), _replace_config(_record("run-b"), **change))

    assert expected_code in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == expected_status


def test_initial_capital_mismatch_warns_twr_and_blocks_portfolio_value() -> None:
    second = _record("run-b", initial_capital=25_000.0)

    payload = _payload(_record("run-a"), second)

    assert "DIFFERENT_INITIAL_CAPITAL" in _reason_codes(payload, "twr")
    assert "INTEGER_SHARE_PATH_DEPENDENCY" in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == "WARNING"
    assert payload["compatibility"]["portfolio_value"]["status"] == "INCOMPATIBLE"


def test_contribution_schedule_mismatch_warns_twr_and_investor_experience() -> None:
    second = _with_contribution(_record("run-b"))

    payload = _payload(_record("run-a"), second)

    assert "DIFFERENT_EXTERNAL_CASH_FLOWS" in _reason_codes(payload, "twr")
    assert "INTEGER_SHARE_PATH_DEPENDENCY" in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == "WARNING"
    assert payload["compatibility"]["portfolio_value"]["status"] == "INCOMPATIBLE"
    assert payload["compatibility"]["investor_experience"]["status"] == "WARNING"


def test_effective_contribution_date_mismatch_is_distinct_from_requested_schedule() -> None:
    first = _with_contribution(_record("run-a"), effective_day=3)
    second = _with_contribution(_record("run-b"), effective_day=4)

    payload = _payload(first, second)

    assert "DIFFERENT_EFFECTIVE_CONTRIBUTION_DATES" in _reason_codes(payload, "portfolio_value")
    assert (
        payload["compatibility"]["portfolio_value"]["dimensions"]["requested_contribution_dates"][
            "status"
        ]
        == "MATCH"
    )


def test_asset_universe_mismatch_is_visible_without_pretending_same_context() -> None:
    second = _record("run-b")
    references = dict(second.run.backtest_result.data_snapshot_reference)
    references["SPY"] = dict(references["QQQ"])
    second = _replace_result(
        second,
        data_snapshot_reference=MappingProxyType(references),
    )

    payload = _payload(_record("run-a"), second)

    assert "DIFFERENT_ASSET_UNIVERSE" in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == "WARNING"


def test_engine_and_analytics_version_mismatches_are_explicit_warnings() -> None:
    second = _replace_result(
        _record("run-b"),
        engine_version="phase-3.future",
    )
    second = replace(second, analysis_version="phase-4.future")

    payload = _payload(_record("run-a"), second)

    assert "DIFFERENT_ENGINE_VERSIONS" in _reason_codes(payload, "twr")
    assert "DIFFERENT_ANALYTICS_VERSIONS" in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == "WARNING"


def test_unknown_dataset_and_legacy_contribution_provenance_are_not_comparable() -> None:
    legacy = replace(
        _record("legacy"),
        run=replace(_record("legacy").run, contribution_provenance_available=False),
    )

    payload = _payload(_record("run-a"), legacy)

    assert "DATASET_VERSION_UNAVAILABLE" in _reason_codes(payload, "twr")
    assert "CONTRIBUTION_PROVENANCE_UNAVAILABLE" in _reason_codes(payload, "twr")
    assert payload["compatibility"]["twr"]["status"] == "WARNING"


def test_missing_benchmark_is_an_explicit_optional_capability() -> None:
    payload = _payload(_record("run-a"), _record("run-b"))

    assert payload["runs"][0]["comparison_provenance"]["benchmark"] == {
        "status": "not_available",
        "symbol": None,
    }
    assert payload["compatibility"]["twr"]["dimensions"]["benchmark"]["status"] == "MATCH"


def test_raw_portfolio_value_is_excluded_by_default_and_included_explicitly() -> None:
    records = (_record("run-a"), _record("run-b"))

    default_payload = _payload(*records)
    included_payload = _payload(
        *records,
        include=ComparisonInclude(portfolio_value=True),
    )

    assert default_payload["series"][0]["portfolio_value"] == {
        "status": "excluded",
        "points": [],
    }
    assert default_payload["series"][0]["capital_invested"] == {
        "status": "excluded",
        "points": [],
    }
    assert default_payload["series"][0]["investment_profit"] == {
        "status": "excluded",
        "points": [],
    }
    assert default_payload["series"][0]["equity_curve"] == []
    assert included_payload["series"][0]["portfolio_value"]["points"]
    assert included_payload["series"][0]["equity_curve"]


def test_wealth_series_are_opt_in_canonical_and_match_final_scalars() -> None:
    first = _with_contribution(_record("run-a"), amount="100", effective_day=3)
    payload = _payload(
        first,
        _record("run-b"),
        include=ComparisonInclude(
            portfolio_value=True,
            capital_invested=True,
            investment_profit=True,
        ),
    )
    series = payload["series"][0]
    result = first.run.backtest_result

    assert series["portfolio_value"]["points"] == [
        {"date": point.date.isoformat(), "value": point.total_equity}
        for point in result.equity_curve
    ]
    assert [point["value"] for point in series["capital_invested"]["points"]] == [
        10_000,
        10_100,
        10_100,
    ]
    assert series["capital_invested"]["points"][-1]["value"] == result.total_capital_invested
    assert series["investment_profit"]["points"][-1]["value"] == result.investment_profit
    assert "normalization" not in series["portfolio_value"]
    assert "normalization" not in series["capital_invested"]
    assert "normalization" not in series["investment_profit"]
    assert payload["runs"][0]["total_capital_invested"] == result.total_capital_invested
    assert payload["runs"][0]["investment_profit"] == result.investment_profit


def test_equal_total_capital_with_different_timing_has_deterministic_reason() -> None:
    lump_sum = _record("lump-sum", initial_capital=10_000)
    dca = _with_contribution(
        _record("dca", initial_capital=9_000),
        amount="1000",
        effective_day=3,
    )

    include = ComparisonInclude(
        portfolio_value=True,
        capital_invested=True,
        investment_profit=True,
    )
    first = _payload(lump_sum, dca, include=include)
    second = _payload(lump_sum, dca)

    portfolio = first["compatibility"]["portfolio_value"]
    investor = first["compatibility"]["investor_experience"]
    assert portfolio["dimensions"]["total_capital_invested"]["status"] == "MATCH"
    assert portfolio["dimensions"]["effective_cash_flow_sequence"]["status"] == "MISMATCH"
    assert "TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS" in portfolio["reason_codes"]
    assert "TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS" in investor["reason_codes"]
    assert portfolio["status"] == "INCOMPATIBLE"
    assert investor["status"] == "WARNING"
    assert first["compatibility"] == second["compatibility"]
    assert first["series"][0]["twr"]["status"] == "available"
    for capability in ("portfolio_value", "capital_invested", "investment_profit"):
        assert first["series"][0][capability]["status"] == "available"
        assert first["series"][1][capability]["status"] == "available"


@pytest.mark.parametrize(
    "symbols",
    [
        ("QQQ",),
        ("QQQ", "TQQQ"),
        ("SPY", "UPRO", "SGOV"),
    ],
)
def test_wealth_projection_is_strategy_and_asset_universe_agnostic(
    symbols: tuple[str, ...],
) -> None:
    records = tuple(_with_symbols(_record(f"run-{index}"), symbols) for index in range(2))

    payload = _payload(
        *records,
        include=ComparisonInclude(
            portfolio_value=True,
            capital_invested=True,
            investment_profit=True,
        ),
    )

    assert all(item["asset_universe"] == sorted(symbols) for item in payload["runs"])
    assert all(series["investment_profit"]["status"] == "available" for series in payload["series"])


def test_different_final_capital_is_explicitly_distinct_from_timing() -> None:
    payload = _payload(
        _record("run-a", initial_capital=10_000),
        _with_contribution(_record("run-b", initial_capital=10_000), amount="1000"),
    )

    reasons = _reason_codes(payload, "portfolio_value")
    assert "DIFFERENT_TOTAL_CAPITAL_INVESTED" in reasons
    assert "TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS" not in reasons


def test_legacy_run_missing_canonical_capabilities_does_not_crash() -> None:
    analysis = replace(
        _record("legacy").run.performance_analysis,
        backtest_run_id="legacy",
        twr_wealth_curve=(),
        exposure_summary=MappingProxyType({}),
        turnover=MetricValue.not_evaluable("legacy turnover unavailable"),
        xirr=MetricValue.not_evaluable("legacy XIRR unavailable"),
    )
    source = _record("legacy").run
    legacy = replace(
        _record("legacy"),
        run=replace(
            source,
            performance_analysis=analysis,
            contribution_provenance_available=False,
            backtest_result=replace(source.backtest_result, holding_segments=None),
        ),
    )

    payload = _payload(_record("run-a"), legacy)

    assert payload["series"][1]["twr"]["status"] == "not_available"
    assert payload["runs"][1]["metrics"]["xirr"]["value"] is None
    assert payload["runs"][1]["metrics"]["exposure"]["status"] == "not_available"
    assert payload["runs"][1]["metrics"]["portfolio_turnover"]["value"] is None
    assert payload["runs"][1]["metrics"]["holding_period_count"]["value"] is None
    assert "XIRR_UNAVAILABLE" in _reason_codes(payload, "investor_experience")


def test_metrics_can_be_excluded_without_removing_identity_or_provenance() -> None:
    payload = _payload(
        _record("run-a"),
        _record("run-b"),
        include=ComparisonInclude(metrics=False),
    )

    assert payload["runs"][0]["metrics"] == {}
    assert payload["runs"][0]["metrics_status"] == "excluded"
    assert payload["runs"][0]["strategy_version_id"]
    assert payload["runs"][0]["comparison_provenance"]


def test_compatibility_statuses_serialize_as_contract_enum_values() -> None:
    payload = _payload(
        _record("run-a"),
        _replace_config(_record("run-b"), price_field_used=PriceField.RAW_CLOSE.value),
    )

    statuses = {item["status"] for item in payload["compatibility"].values()}
    assert statuses <= {"COMPARABLE", "WARNING", "INCOMPATIBLE", "UNKNOWN"}
    assert "INCOMPATIBLE" in statuses


def test_ten_long_histories_have_stable_order_and_bounded_projection_cost() -> None:
    start = date(2000, 1, 1)
    wealth = tuple(
        WealthPoint(start + timedelta(days=index), 1.0 + index / 100_000) for index in range(6_000)
    )
    drawdown = tuple(
        DrawdownPoint(start + timedelta(days=index), -(index % 100) / 10_000)
        for index in range(6_000)
    )
    equity = tuple(
        EquityPoint(
            date=start + timedelta(days=index),
            cash=0.0,
            asset_values={"QQQ": 100_000.0 + index},
            total_equity=100_000.0 + index,
        )
        for index in range(6_000)
    )
    records = []
    for index in range(10):
        source = _record(f"long-{index}")
        records.append(
            _replace_result(
                source,
                analysis_updates={
                    "start_date": start,
                    "end_date": wealth[-1].date,
                    "final_equity": equity[-1].total_equity,
                    "twr_wealth_curve": wealth,
                    "drawdown_curve": drawdown,
                },
                start_date=start,
                end_date=wealth[-1].date,
                final_equity=equity[-1].total_equity,
                equity_curve=equity,
            )
        )

    include = ComparisonInclude(
        portfolio_value=True,
        capital_invested=True,
        investment_profit=True,
        metrics=False,
    )
    for run_count in (2, 5, 10):
        started = perf_counter()
        payload = _payload(*records[:run_count], include=include)
        elapsed = perf_counter() - started
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_points = run_count * 6_000

        assert [item["backtest_run_id"] for item in payload["runs"]] == [
            f"long-{index}" for index in range(run_count)
        ]
        for capability in (
            "twr",
            "drawdown",
            "portfolio_value",
            "capital_invested",
            "investment_profit",
        ):
            assert (
                sum(len(item[capability]["points"]) for item in payload["series"])
                == expected_points
            )
        assert len(serialized) < run_count * 4_000_000
        assert elapsed < 5.0
        assert payload == _payload(*records[:run_count], include=include)
