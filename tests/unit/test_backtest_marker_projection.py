from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from backend.app.backtest_marker_projection import (
    MARKER_SCHEMA_VERSION,
    BacktestMarkerProjectionError,
    BacktestMarkerProjectionService,
)
from backend.app.backtest_report_projection import BacktestReportProjectionService
from backtest.models import (
    ContributionEvent,
    ContributionFrequency,
    ExternalCashFlow,
    OrderSide,
    RebalanceCause,
    RebalanceDecision,
    RebalanceDecisionType,
    RebalanceSuppressionReason,
)
from tests.unit.test_backtest_repository import _run


def _transition(
    event_date: str,
    transition_id: str,
    from_state: str,
    to_state: str,
    *,
    zone_id: str | None = None,
) -> dict[str, object]:
    value_zone = None
    if zone_id is not None:
        value_zone = {
            "matched_zone_id": zone_id,
            "exited_zone_id": None,
            "evidence": [
                {
                    "zone_id": zone_id,
                    "asset": "QQQ",
                    "timeframe": "weekly",
                    "reference_price": 90.0,
                    "indicator_kind": "SMA",
                    "period": 200,
                    "indicator_value": 100.0,
                    "distance": -0.1,
                    "entry_threshold": -0.08,
                    "exit_threshold": -0.03,
                    "source_date": event_date,
                    "entry_matches": True,
                    "exit_matches": False,
                }
            ],
        }
    return {
        "evaluation_date": event_date,
        "transition_id": transition_id,
        "from_state": from_state,
        "to_state": to_state,
        "state_entry_date": event_date,
        "evaluated_evidence": [
            {"transition_id": transition_id, "passed": True, "value_zone_match": True}
        ],
        "target_allocation": {
            "allocations": [{"symbol": "QQQ", "target_weight": 0.6}],
        },
        "strategy_version_id": "repo-v1",
        "strategy_version_hash": "hash-repo-v1",
        "transition_definition_hash": f"hash-{transition_id}",
        "value_zone": value_zone,
    }


def _provenance() -> MappingProxyType:
    records = (
        {
            "signal_date": "2026-01-02",
            "matched_rule_id": "value",
            "allocation_source": "rule_match",
            "target_allocation": {"QQQ": 0.6, "SGOV": 0.2},
            "execution_date": "2026-01-03",
            "execution_status": "submitted",
            "omission_reason": None,
            "regime_provenance": {
                "active_state_id": "VALUE",
                "transition_event": _transition(
                    "2026-01-02", "normal-to-value", "NORMAL", "VALUE", zone_id="value"
                ),
            },
        },
        {
            "signal_date": "2026-01-03",
            "matched_rule_id": None,
            "allocation_source": "hold_previous",
            "target_allocation": {"QQQ": 0.6, "SGOV": 0.2},
            "execution_date": "2026-01-04",
            "execution_status": "submitted",
            "omission_reason": None,
            "regime_provenance": {
                "active_state_id": "VALUE",
                "transition_event": None,
            },
        },
        {
            "signal_date": "2026-01-04",
            "matched_rule_id": "recovery-hold",
            "allocation_source": "rule_match",
            "target_allocation": {"QQQ": 0.6, "SGOV": 0.2},
            "execution_date": None,
            "execution_status": "omitted",
            "omission_reason": "no next trading day exists within the backtest data",
            "regime_provenance": {
                "active_state_id": "RECOVERY_HOLD",
                "transition_event": _transition(
                    "2026-01-04",
                    "value-to-recovery",
                    "DEEP_VALUE",
                    "RECOVERY_HOLD",
                ),
            },
        },
    )
    return MappingProxyType({"source": "StrategyBacktestResult.signal_records", "records": records})


def _marker_run():
    base = _run()
    result = base.backtest_result
    contribution = ContributionEvent(
        frequency=ContributionFrequency.ONE_TIME,
        amount=Decimal("500"),
        requested_date=date(2026, 1, 1),
        effective_date=date(2026, 1, 2),
    )
    decisions = (
        RebalanceDecision(
            evaluation_date=date(2026, 1, 2),
            target_allocation={"QQQ": 0.6, "SGOV": 0.2, "CASH": 0.2},
            actual_allocation={"CASH": 1.0},
            previous_target_allocation={"CASH": 1.0},
            decision=RebalanceDecisionType.EXECUTE,
            reasons=("contribution", "target_change", "drift_threshold"),
            target_change_metric=0.8,
            drift_metric=0.8,
            turnover_estimate=0.8,
            minimum_cash_reserve=0.1,
            execution_date=date(2026, 1, 3),
            contribution_amount=500.0,
        ),
        RebalanceDecision(
            evaluation_date=date(2026, 1, 4),
            target_allocation={"QQQ": 0.6, "SGOV": 0.2, "CASH": 0.2},
            actual_allocation={"QQQ": 0.59, "SGOV": 0.19, "CASH": 0.22},
            previous_target_allocation={"QQQ": 0.6, "SGOV": 0.2, "CASH": 0.2},
            decision=RebalanceDecisionType.SUPPRESS,
            reasons=("target_change",),
            target_change_metric=0.0,
            drift_metric=0.02,
            turnover_estimate=0.0,
            minimum_cash_reserve=0.1,
            suppression_reason=RebalanceSuppressionReason.NO_MATERIAL_CHANGE,
        ),
    )
    changed = replace(
        result,
        contribution_events=(contribution,),
        external_cash_flows=(ExternalCashFlow(date=date(2026, 1, 2), amount=Decimal("500")),),
        cumulative_contributions=500.0,
        total_capital_invested=result.initial_capital + 500.0,
        investment_profit=result.final_equity - result.initial_capital - 500.0,
        rebalance_decisions=decisions,
    )
    return replace(base, backtest_result=changed, strategy_provenance=_provenance())


def test_marker_projection_maps_canonical_events_without_fake_hold_or_execution() -> None:
    payload = BacktestMarkerProjectionService().project(_marker_run())
    markers = payload["markers"]

    assert payload["marker_schema_version"] == MARKER_SCHEMA_VERSION
    assert payload["persisted"] is False
    assert {item["marker_type"] for item in markers} == {
        "SIGNAL",
        "EXECUTION",
        "REBALANCE_DECISION",
        "CONTRIBUTION",
        "TARGET_ALLOCATION_TRANSITION",
        "ACTUAL_ALLOCATION_TRANSITION",
        "REGIME_TRANSITION",
    }
    signals = [item for item in markers if item["marker_type"] == "SIGNAL"]
    assert [item["event_date"] for item in signals] == ["2026-01-02", "2026-01-04"]
    assert signals[-1]["details"]["execution_status"] == "NO_EXECUTION_SESSION"
    assert not any(
        item["marker_type"] == "EXECUTION" and item["event_date"] == "2026-01-04"
        for item in markers
    )

    contribution = next(item for item in markers if item["marker_type"] == "CONTRIBUTION")
    assert contribution["event_date"] == "2026-01-02"
    assert contribution["details"]["requested_date"] == "2026-01-01"
    assert contribution["details"]["strategy_signal"] is False

    execution = next(item for item in markers if item["marker_type"] == "EXECUTION")
    assert execution["event_date"] == "2026-01-03"
    assert execution["details"]["fills"][0]["side"] == "BUY"
    assert execution["details"]["fills"][0]["notional"] > 0
    assert execution["details"]["related_signal_date"] == "2026-01-02"


def test_allocation_regime_rebalance_and_same_day_groups_preserve_truth() -> None:
    payload = BacktestMarkerProjectionService().project(_marker_run())
    markers = payload["markers"]
    target = [item for item in markers if item["marker_type"] == "TARGET_ALLOCATION_TRANSITION"]
    actual = [item for item in markers if item["marker_type"] == "ACTUAL_ALLOCATION_TRANSITION"]
    regimes = [item for item in markers if item["marker_type"] == "REGIME_TRANSITION"]
    decisions = [item for item in markers if item["marker_type"] == "REBALANCE_DECISION"]

    assert len(target) == 1
    assert target[0]["details"]["before"] == {"CASH": 1.0}
    assert target[0]["details"]["after"] == {
        "QQQ": 0.6,
        "SGOV": 0.2,
        "CASH": pytest.approx(0.2),
    }
    assert actual[0]["source_event_type"] == "BacktestResult.allocation_history"
    assert "SGOV" not in actual[0]["details"]["after"]
    assert regimes[-1]["summary"] == "DEEP_VALUE → RECOVERY_HOLD"
    assert regimes[0]["details"]["value_zone"]["matched_zone_id"] == "value"
    assert regimes[0]["details"]["value_zone"]["evidence"][0]["source_date"] == ("2026-01-02")
    assert decisions[-1]["summary"] == "SUPPRESS"
    assert decisions[-1]["details"]["suppression_reason"] == "no_material_change"

    first_group = next(item for item in payload["groups"] if item["date"] == "2026-01-02")
    assert first_group["marker_types"] == [
        "CONTRIBUTION",
        "SIGNAL",
        "REGIME_TRANSITION",
        "TARGET_ALLOCATION_TRANSITION",
        "REBALANCE_DECISION",
    ]
    assert len(first_group["marker_ids"]) == first_group["marker_count"]
    assert set(first_group["marker_ids"]) <= {item["marker_id"] for item in markers}
    execution_group = next(item for item in payload["groups"] if item["date"] == "2026-01-03")
    assert "EXECUTION" in execution_group["marker_types"]


def test_marker_filters_major_mode_determinism_and_old_payload_separation() -> None:
    service = BacktestMarkerProjectionService()
    run = _marker_run()
    first = service.project(run, marker_types=("REGIME_TRANSITION", "EXECUTION"))
    second = service.project(run, marker_types=("EXECUTION", "REGIME_TRANSITION"))

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert {item["marker_type"] for item in first["markers"]} == {
        "EXECUTION",
        "REGIME_TRANSITION",
    }
    major = service.project(run, major_only=True, major_threshold=0.5)
    assert all(item["significance"]["is_major"] for item in major["markers"])
    assert {item["marker_type"] for item in major["markers"]} >= {
        "CONTRIBUTION",
        "REGIME_TRANSITION",
    }
    assert "markers" not in BacktestReportProjectionService().project(run)


def test_multiple_buy_sell_fills_on_one_date_remain_one_execution_group() -> None:
    run = _marker_run()
    first_order = run.backtest_result.orders[0]
    first_fill = run.backtest_result.fills[0]
    second_order = replace(
        first_order,
        order_id="second-order",
        symbol="TQQQ",
        side=OrderSide.SELL,
        quantity=2,
    )
    second_fill = replace(
        first_fill,
        order_id="second-order",
        symbol="TQQQ",
        side=OrderSide.SELL,
        quantity=2,
    )
    result = replace(
        run.backtest_result,
        orders=(*run.backtest_result.orders, second_order),
        fills=(*run.backtest_result.fills, second_fill),
    )

    markers = BacktestMarkerProjectionService().project(
        replace(run, backtest_result=result), marker_types=("EXECUTION",)
    )["markers"]

    assert len(markers) == 1
    assert [(item["side"], item["symbol"]) for item in markers[0]["details"]["fills"]] == [
        ("BUY", "QQQ"),
        ("SELL", "TQQQ"),
    ]
    assert markers[0]["summary"] == "2 executions"


def test_contribution_execution_does_not_claim_a_strategy_signal() -> None:
    run = _marker_run()
    contribution_order = replace(
        run.backtest_result.orders[0],
        rebalance_cause=RebalanceCause.CONTRIBUTION,
    )
    result = replace(run.backtest_result, orders=(contribution_order,))

    execution = BacktestMarkerProjectionService().project(
        replace(run, backtest_result=result), marker_types=("EXECUTION",)
    )["markers"][0]

    assert execution["details"]["rebalance_cause"] == "contribution"
    assert execution["details"]["related_signal_date"] is None


def test_realistic_recovery_hold_path_is_projected_without_stay_or_same_day_cascade() -> None:
    run = _marker_run()
    path = (
        ("NORMAL", "APPROACHING_VALUE"),
        ("APPROACHING_VALUE", "VALUE"),
        ("VALUE", "DEEP_VALUE"),
        ("DEEP_VALUE", "RECOVERY_HOLD"),
        ("RECOVERY_HOLD", "FULL_RISK_ON"),
        ("FULL_RISK_ON", "RECOVERY_HOLD"),
    )
    records = []
    for index, (from_state, to_state) in enumerate(path):
        event_date = (date(2025, 1, 3) + timedelta(days=index * 7)).isoformat()
        target = (
            {"QQQ": 0.5, "TQQQ": 0.3}
            if to_state in {"RECOVERY_HOLD", "FULL_RISK_ON"}
            else {"SGOV": 1.0}
        )
        records.append(
            {
                "signal_date": event_date,
                "matched_rule_id": to_state.lower(),
                "allocation_source": "rule_match",
                "target_allocation": target,
                "execution_date": None,
                "execution_status": "omitted",
                "omission_reason": "projection fixture",
                "regime_provenance": {
                    "active_state_id": to_state,
                    "transition_event": _transition(
                        event_date,
                        f"{from_state.lower()}-to-{to_state.lower()}",
                        from_state,
                        to_state,
                        zone_id="deep" if to_state == "DEEP_VALUE" else None,
                    ),
                },
            }
        )
    provenance = MappingProxyType(
        {"source": "StrategyBacktestResult.signal_records", "records": tuple(records)}
    )

    markers = BacktestMarkerProjectionService().project(
        replace(run, strategy_provenance=provenance),
        marker_types=("REGIME_TRANSITION",),
    )["markers"]

    assert [item["summary"] for item in markers] == [
        f"{from_state} → {to_state}" for from_state, to_state in path
    ]
    assert len({item["event_date"] for item in markers}) == len(path)
    assert markers[3]["summary"] == "DEEP_VALUE → RECOVERY_HOLD"
    assert markers[4]["summary"] == "RECOVERY_HOLD → FULL_RISK_ON"


def test_marker_request_validation_and_empty_type_selection() -> None:
    service = BacktestMarkerProjectionService()
    run = _marker_run()

    assert service.project(run, marker_types=())["markers"] == []
    with pytest.raises(BacktestMarkerProjectionError, match="unknown marker types"):
        service.project(run, marker_types=("DROP_TABLE",))
    with pytest.raises(BacktestMarkerProjectionError, match="explicit major_threshold"):
        service.project(run, major_only=True)
    with pytest.raises(BacktestMarkerProjectionError, match="start date"):
        service.project(run, start=date(2026, 1, 4), end=date(2026, 1, 2))


def test_long_history_projection_has_no_silent_truncation_or_provenance_duplication() -> None:
    run = _marker_run()
    start = date(2000, 1, 3)
    records = []
    for index in range(1_000):
        event_date = (start + timedelta(days=index)).isoformat()
        records.append(
            {
                "signal_date": event_date,
                "matched_rule_id": f"rule-{index % 2}",
                "allocation_source": "rule_match",
                "target_allocation": {"QQQ": float(index % 2)},
                "execution_date": None,
                "execution_status": "omitted",
                "omission_reason": "fixture",
            }
        )
    provenance = MappingProxyType(
        {"source": "StrategyBacktestResult.signal_records", "records": tuple(records)}
    )

    payload = BacktestMarkerProjectionService().project(
        replace(run, strategy_provenance=provenance),
        marker_types=("SIGNAL", "TARGET_ALLOCATION_TRANSITION"),
    )

    assert payload["counts"]["markers"] == 1_999
    assert payload["counts"]["truncated"] is False
    assert payload["counts"]["hidden"] > 0
    assert len(payload["groups"]) == 1_000
    assert "regime_provenance" not in json.dumps(payload)
