"""Versioned, deterministic marker projections over immutable backtest events."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from backend.app.backtest_models import BacktestRun
from backtest.models import canonical_decimal

MARKER_SCHEMA_VERSION = "1.0"
MARKER_TYPES = frozenset(
    {
        "SIGNAL",
        "EXECUTION",
        "REBALANCE_DECISION",
        "CONTRIBUTION",
        "TARGET_ALLOCATION_TRANSITION",
        "ACTUAL_ALLOCATION_TRANSITION",
        "REGIME_TRANSITION",
    }
)
_EVENT_ORDER = {
    "CONTRIBUTION": 0,
    "SIGNAL": 1,
    "REGIME_TRANSITION": 2,
    "TARGET_ALLOCATION_TRANSITION": 3,
    "ACTUAL_ALLOCATION_TRANSITION": 4,
    "REBALANCE_DECISION": 5,
    "EXECUTION": 6,
}


class BacktestMarkerProjectionError(ValueError):
    """Raised when a marker projection request is invalid."""


class BacktestMarkerProjectionService:
    """Project canonical events without creating a second source of financial truth."""

    def project(
        self,
        run: BacktestRun,
        *,
        marker_types: Iterable[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        group_same_day: bool = True,
        major_only: bool = False,
        major_threshold: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(run, BacktestRun):
            raise TypeError("run must be a BacktestRun")
        if start is not None and end is not None and start > end:
            raise BacktestMarkerProjectionError("marker start date is after end date")
        selected_types = _normalized_marker_types(marker_types)
        threshold = _validated_major_threshold(major_only, major_threshold)

        all_markers = [
            *self._signal_markers(run),
            *self._execution_markers(run),
            *self._rebalance_markers(run),
            *self._contribution_markers(run),
            *self._target_allocation_markers(run),
            *self._actual_allocation_markers(run),
            *self._regime_markers(run),
        ]
        all_markers.sort(key=_marker_sort_key)
        markers = [
            item
            for item in all_markers
            if item["marker_type"] in selected_types
            and _inside_window(item["event_date"], start, end)
            and (not major_only or _is_major(item, threshold))
        ]
        for marker in markers:
            marker["significance"]["is_major"] = _is_major(marker, threshold)

        groups = self._groups(run, markers) if group_same_day else []
        counts = Counter(item["marker_type"] for item in markers)
        return {
            "marker_schema_version": MARKER_SCHEMA_VERSION,
            "identity": {
                "backtest_run_id": run.backtest_run_id,
                "strategy_version_id": run.strategy_version_id,
            },
            "source": "derived_from_immutable_backtest_run",
            "persisted": False,
            "filters": {
                "types": sorted(selected_types),
                "from": start.isoformat() if start is not None else None,
                "to": end.isoformat() if end is not None else None,
                "group_same_day": group_same_day,
                "major_only": major_only,
                "major_threshold": threshold,
            },
            "counts": {
                "markers": len(markers),
                "groups": len(groups),
                "by_type": {name: counts.get(name, 0) for name in sorted(MARKER_TYPES)},
                "hidden": len(all_markers) - len(markers),
                "truncated": False,
            },
            "markers": markers,
            "groups": groups,
        }

    def _signal_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        records = _strategy_records(run)
        markers: list[dict[str, Any]] = []
        previous_decision: tuple[Any, ...] | None = None
        previous_allocation = _cash_allocation({})
        for index, record in enumerate(records):
            allocation = _allocation(record.get("target_allocation"))
            decision = (
                record.get("allocation_source"),
                record.get("matched_rule_id"),
                tuple(allocation.items()),
            )
            regime = record.get("regime_provenance")
            transition = regime.get("transition_event") if isinstance(regime, Mapping) else None
            is_new_decision = (
                record.get("allocation_source") in {"rule_match", "fallback"}
                and decision != previous_decision
            )
            if record.get("allocation_source") in {"rule_match", "fallback"}:
                previous_decision = decision
            if not is_new_decision and not isinstance(transition, Mapping):
                previous_allocation = _cash_allocation(allocation)
                continue
            signal_date = _iso_date(record.get("signal_date"))
            if signal_date is None:
                continue
            execution_date = _iso_date(record.get("execution_date"))
            execution_status = str(record.get("execution_status") or "unknown")
            if execution_date is None and execution_status == "omitted":
                execution_status = "NO_EXECUTION_SESSION"
            current_allocation = _cash_allocation(allocation)
            metric = _allocation_change(previous_allocation, current_allocation)
            source_reference = f"strategy-provenance:{index}:{signal_date}"
            markers.append(
                self._marker(
                    run,
                    marker_type="SIGNAL",
                    event_date=signal_date,
                    source_event_type="StrategyBacktestResult.signal_records",
                    source_reference=source_reference,
                    title="Strategy Signal",
                    short_label="S",
                    summary=(
                        str(record.get("matched_rule_id") or "Fallback")
                        + (" · unexecuted" if execution_date is None else "")
                    ),
                    details={
                        "signal_date": signal_date,
                        "strategy_source": record.get("allocation_source"),
                        "matched_rule_id": record.get("matched_rule_id"),
                        "target_allocation": current_allocation,
                        "expected_execution_date": execution_date,
                        "execution_status": execution_status,
                        "omission_reason": record.get("omission_reason"),
                        "regime_transition_id": (
                            transition.get("transition_id")
                            if isinstance(transition, Mapping)
                            else None
                        ),
                    },
                    metric_name="target_allocation_one_way_change",
                    metric_value=metric,
                )
            )
            previous_allocation = current_allocation
        return markers

    def _execution_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        orders = {item.order_id: item for item in run.backtest_result.orders}
        grouped: dict[tuple[str, str | None, str], list[Any]] = {}
        for fill in run.backtest_result.fills:
            order = orders.get(fill.order_id)
            cause = order.rebalance_cause.value if order is not None else "unknown"
            key = (
                fill.date.isoformat(),
                (
                    order.signal_date.isoformat()
                    if order is not None and cause == "target"
                    else None
                ),
                cause,
            )
            grouped.setdefault(key, []).append(fill)
        equity = {
            item.date.isoformat(): item.total_equity for item in run.backtest_result.equity_curve
        }
        markers: list[dict[str, Any]] = []
        for (execution_date, signal_date, cause), fills in sorted(grouped.items()):
            ordered_fills = sorted(
                fills,
                key=lambda item: (item.symbol, item.side.value, item.order_id),
            )
            gross_notional = sum(item.quantity * item.price for item in ordered_fills)
            denominator = equity.get(execution_date)
            metric = gross_notional / denominator if denominator and denominator > 0 else None
            fill_details = [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side.value,
                    "quantity": item.quantity,
                    "fill_price": item.price,
                    "notional": item.quantity * item.price,
                    "commission": item.commission,
                    "slippage": item.slippage,
                }
                for item in ordered_fills
            ]
            markers.append(
                self._marker(
                    run,
                    marker_type="EXECUTION",
                    event_date=execution_date,
                    source_event_type="BacktestResult.fills",
                    source_reference=(
                        f"fills:{execution_date}:{signal_date or 'none'}:{cause}:"
                        + ",".join(item.order_id for item in ordered_fills)
                    ),
                    title="Execution",
                    short_label="E",
                    summary=(
                        f"{len(ordered_fills)} execution{'s' if len(ordered_fills) != 1 else ''}"
                    ),
                    details={
                        "execution_date": execution_date,
                        "related_signal_date": signal_date,
                        "rebalance_cause": cause,
                        "gross_one_way_notional": gross_notional,
                        "portfolio_value_denominator": denominator,
                        "fills": fill_details,
                    },
                    metric_name="gross_one_way_notional_over_portfolio_value",
                    metric_value=metric,
                )
            )
        return markers

    def _rebalance_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        markers = []
        for index, decision in enumerate(run.backtest_result.rebalance_decisions):
            payload = decision.to_dict()
            evaluation_date = decision.evaluation_date.isoformat()
            markers.append(
                self._marker(
                    run,
                    marker_type="REBALANCE_DECISION",
                    event_date=evaluation_date,
                    source_event_type="BacktestResult.rebalance_decisions",
                    source_reference=f"rebalance-decision:{index}:{evaluation_date}",
                    title="Rebalance Decision",
                    short_label="R",
                    summary=decision.decision.value.upper(),
                    details=payload,
                    metric_name="target_allocation_one_way_change",
                    metric_value=decision.target_change_metric,
                )
            )
        return markers

    def _contribution_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        markers = []
        cumulative = Decimal("0")
        events = sorted(
            enumerate(run.backtest_result.contribution_events),
            key=lambda pair: (pair[1].effective_date, pair[1].requested_date, pair[0]),
        )
        for sequence, (_, event) in enumerate(events, start=1):
            cumulative += event.amount
            effective_date = event.effective_date.isoformat()
            markers.append(
                self._marker(
                    run,
                    marker_type="CONTRIBUTION",
                    event_date=effective_date,
                    source_event_type="BacktestResult.contribution_events",
                    source_reference=f"contribution:{sequence}:{effective_date}",
                    title="External Contribution",
                    short_label="C",
                    summary=f"USD {canonical_decimal(event.amount)}",
                    details={
                        "requested_date": event.requested_date.isoformat(),
                        "effective_date": effective_date,
                        "amount": canonical_decimal(event.amount),
                        "cumulative_invested_capital": run.backtest_result.initial_capital
                        + float(cumulative),
                        "frequency": event.frequency.value,
                        "currency": event.currency,
                        "strategy_signal": False,
                    },
                    metric_name=None,
                    metric_value=None,
                    always_major=True,
                )
            )
        return markers

    def _target_allocation_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        markers = []
        before = _cash_allocation({})
        for index, record in enumerate(_strategy_records(run)):
            signal_date = _iso_date(record.get("signal_date"))
            if signal_date is None:
                continue
            after = _cash_allocation(_allocation(record.get("target_allocation")))
            metric = _allocation_change(before, after)
            if metric <= 1e-12:
                before = after
                continue
            markers.append(
                self._marker(
                    run,
                    marker_type="TARGET_ALLOCATION_TRANSITION",
                    event_date=signal_date,
                    source_event_type="StrategyBacktestResult.signal_records",
                    source_reference=f"target-allocation:{index}:{signal_date}",
                    title="Target Allocation",
                    short_label="T",
                    summary="Strategy target changed",
                    details={
                        "before": before,
                        "after": after,
                        "allocation_source": record.get("allocation_source"),
                        "matched_rule_id": record.get("matched_rule_id"),
                    },
                    metric_name="target_allocation_one_way_change",
                    metric_value=metric,
                )
            )
            before = after
        return markers

    def _actual_allocation_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        by_date: dict[str, dict[str, float]] = {}
        for point in run.backtest_result.allocation_history:
            by_date.setdefault(point.date.isoformat(), {})[point.symbol] = point.actual_weight
        equity = {item.date.isoformat(): item for item in run.backtest_result.equity_curve}
        fill_dates = {item.date.isoformat() for item in run.backtest_result.fills}
        before = _cash_allocation({})
        markers = []
        for index, event_date in enumerate(sorted(by_date)):
            point = equity.get(event_date)
            weights = dict(by_date[event_date])
            weights["CASH"] = (
                point.cash / point.total_equity
                if point is not None and point.total_equity > 0
                else max(0.0, 1.0 - sum(weights.values()))
            )
            after = dict(sorted(weights.items()))
            metric = _allocation_change(before, after)
            if event_date in fill_dates and metric > 1e-12:
                markers.append(
                    self._marker(
                        run,
                        marker_type="ACTUAL_ALLOCATION_TRANSITION",
                        event_date=event_date,
                        source_event_type="BacktestResult.allocation_history",
                        source_reference=f"actual-allocation:{index}:{event_date}",
                        title="Actual Allocation",
                        short_label="A",
                        summary="Post-execution allocation changed",
                        details={"before": before, "after": after, "cause": "execution"},
                        metric_name="actual_allocation_one_way_change",
                        metric_value=metric,
                    )
                )
            before = after
        return markers

    def _regime_markers(self, run: BacktestRun) -> list[dict[str, Any]]:
        markers = []
        for index, record in enumerate(_strategy_records(run)):
            regime = record.get("regime_provenance")
            transition = regime.get("transition_event") if isinstance(regime, Mapping) else None
            if not isinstance(transition, Mapping):
                continue
            event_date = _iso_date(transition.get("evaluation_date"))
            if event_date is None:
                continue
            value_zone = transition.get("value_zone")
            details = {
                "evaluation_date": event_date,
                "transition_id": transition.get("transition_id"),
                "from_state": transition.get("from_state"),
                "to_state": transition.get("to_state"),
                "state_entry_date": transition.get("state_entry_date"),
                "target_allocation": _target_from_transition(transition),
                "strategy_version_id": transition.get("strategy_version_id"),
                "transition_definition_hash": transition.get("transition_definition_hash"),
                "evidence": _compact_transition_evidence(transition.get("evaluated_evidence")),
                "value_zone": _compact_value_zone(value_zone),
            }
            markers.append(
                self._marker(
                    run,
                    marker_type="REGIME_TRANSITION",
                    event_date=event_date,
                    source_event_type="RegimeTransitionEvent",
                    source_reference=(
                        f"regime-transition:{index}:{transition.get('transition_id')}:{event_date}"
                    ),
                    title="Regime Transition",
                    short_label="G",
                    summary=f"{transition.get('from_state')} → {transition.get('to_state')}",
                    details=details,
                    metric_name=None,
                    metric_value=None,
                    always_major=True,
                )
            )
        return markers

    @staticmethod
    def _marker(
        run: BacktestRun,
        *,
        marker_type: str,
        event_date: str,
        source_event_type: str,
        source_reference: str,
        title: str,
        short_label: str,
        summary: str,
        details: Mapping[str, Any],
        metric_name: str | None,
        metric_value: float | None,
        always_major: bool = False,
    ) -> dict[str, Any]:
        identity = {
            "run_id": run.backtest_run_id,
            "marker_type": marker_type,
            "event_date": event_date,
            "source_reference": source_reference,
        }
        return {
            "marker_id": f"marker-{_digest(identity)}",
            "marker_type": marker_type,
            "event_date": event_date,
            "display_date": event_date,
            "strategy_version_id": run.strategy_version_id,
            "run_id": run.backtest_run_id,
            "title": title,
            "short_label": short_label,
            "summary": summary,
            "significance": {
                "always_major": always_major,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "is_major": always_major,
            },
            "source_event_type": source_event_type,
            "source_event_reference": source_reference,
            "details": dict(details),
            "group_key": f"{run.backtest_run_id}:{event_date}",
        }

    @staticmethod
    def _groups(run: BacktestRun, markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_date: dict[str, list[dict[str, Any]]] = {}
        for marker in markers:
            by_date.setdefault(marker["event_date"], []).append(marker)
        groups = []
        for event_date, items in sorted(by_date.items()):
            ordered = sorted(items, key=_marker_sort_key)
            identity = {
                "run_id": run.backtest_run_id,
                "event_date": event_date,
                "marker_ids": [item["marker_id"] for item in ordered],
            }
            groups.append(
                {
                    "group_id": f"marker-group-{_digest(identity)}",
                    "date": event_date,
                    "marker_count": len(ordered),
                    "marker_types": [item["marker_type"] for item in ordered],
                    "summary": f"{len(ordered)} event{'s' if len(ordered) != 1 else ''}",
                    "marker_ids": [item["marker_id"] for item in ordered],
                }
            )
        return groups


def _strategy_records(run: BacktestRun) -> list[Mapping[str, Any]]:
    provenance = run.strategy_provenance
    records = provenance.get("records") if isinstance(provenance, Mapping) else None
    if not isinstance(records, (tuple, list)):
        return []
    return [item for item in records if isinstance(item, Mapping)]


def _normalized_marker_types(marker_types: Iterable[str] | None) -> frozenset[str]:
    if marker_types is None:
        return MARKER_TYPES
    normalized = frozenset(str(item).strip().upper() for item in marker_types if str(item).strip())
    unknown = normalized - MARKER_TYPES
    if unknown:
        raise BacktestMarkerProjectionError("unknown marker types: " + ", ".join(sorted(unknown)))
    return normalized


def _validated_major_threshold(major_only: bool, threshold: float | None) -> float | None:
    if threshold is not None and (not math.isfinite(threshold) or not 0 <= threshold <= 1):
        raise BacktestMarkerProjectionError("major threshold must be finite and in [0, 1]")
    if major_only and threshold is None:
        raise BacktestMarkerProjectionError("major_only requires an explicit major_threshold")
    return threshold


def _is_major(marker: Mapping[str, Any], threshold: float | None) -> bool:
    significance = marker.get("significance")
    if not isinstance(significance, Mapping):
        return False
    if significance.get("always_major") is True:
        return True
    metric = significance.get("metric_value")
    return threshold is not None and isinstance(metric, (int, float)) and metric >= threshold


def _allocation(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result = {
        str(symbol).strip().upper(): float(weight)
        for symbol, weight in value.items()
        if str(symbol).strip() and str(symbol).strip().upper() != "CASH"
    }
    return dict(sorted(result.items()))


def _cash_allocation(assets: Mapping[str, float]) -> dict[str, float]:
    result = dict(sorted(assets.items()))
    result["CASH"] = max(0.0, 1.0 - sum(result.values()))
    return dict(sorted(result.items(), key=lambda item: (item[0] == "CASH", item[0])))


def _allocation_change(before: Mapping[str, float], after: Mapping[str, float]) -> float:
    symbols = set(before) | set(after)
    return 0.5 * sum(abs(after.get(symbol, 0.0) - before.get(symbol, 0.0)) for symbol in symbols)


def _target_from_transition(transition: Mapping[str, Any]) -> dict[str, float]:
    target = transition.get("target_allocation")
    if not isinstance(target, Mapping):
        return {}
    allocations = target.get("allocations")
    if not isinstance(allocations, (tuple, list)):
        return {}
    weights = {
        str(item.get("symbol")): float(item.get("target_weight"))
        for item in allocations
        if isinstance(item, Mapping)
        and item.get("symbol") is not None
        and item.get("target_weight") is not None
    }
    return _cash_allocation(weights)


def _compact_transition_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (tuple, list)):
        return []
    return [
        {
            "transition_id": item.get("transition_id"),
            "passed": item.get("passed"),
            "value_zone_match": item.get("value_zone_match"),
        }
        for item in value
        if isinstance(item, Mapping)
    ]


def _compact_value_zone(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    evidence = value.get("evidence")
    compact_evidence = []
    if isinstance(evidence, (tuple, list)):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            compact_evidence.append(
                {
                    key: item.get(key)
                    for key in (
                        "zone_id",
                        "asset",
                        "timeframe",
                        "reference_price",
                        "indicator_kind",
                        "period",
                        "indicator_value",
                        "distance",
                        "entry_threshold",
                        "exit_threshold",
                        "entry_operator",
                        "exit_operator",
                        "entry_matches",
                        "exit_matches",
                        "price_source_date",
                        "indicator_source_date",
                        "source_date",
                    )
                    if key in item
                }
            )
    return {
        "matched_zone_id": value.get("matched_zone_id"),
        "exited_zone_id": value.get("exited_zone_id"),
        "evidence": compact_evidence,
    }


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _inside_window(value: str, start: date | None, end: date | None) -> bool:
    parsed = date.fromisoformat(value)
    return (start is None or parsed >= start) and (end is None or parsed <= end)


def _marker_sort_key(marker: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(marker["event_date"]),
        _EVENT_ORDER[str(marker["marker_type"])],
        str(marker["source_event_reference"]),
    )


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "MARKER_SCHEMA_VERSION",
    "MARKER_TYPES",
    "BacktestMarkerProjectionError",
    "BacktestMarkerProjectionService",
]
