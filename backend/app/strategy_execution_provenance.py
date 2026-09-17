"""Compact, persisted strategy-to-execution provenance for report consumers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from backtest.integration import StrategyBacktestAllocation


def strategy_execution_provenance(
    signal_records: Iterable[StrategyBacktestAllocation],
) -> Mapping[str, Any]:
    """Return immutable JSON-safe execution provenance without accounting data.

    A record documents an evaluated strategy intent separately from the later
    execution decision.  In particular, ``hold_previous`` remains continuity
    provenance; it is never promoted into a synthetic order or trade.
    """
    records = tuple(signal_records)
    if not all(isinstance(item, StrategyBacktestAllocation) for item in records):
        raise TypeError("signal_records must contain StrategyBacktestAllocation values")

    payload = tuple(
        MappingProxyType(
            {
                "signal_date": item.signal.date.isoformat(),
                "matched_rule_id": item.signal.matched_rule_id,
                "allocation_source": item.signal.allocation_source.value,
                "target_allocation": dict(sorted(item.target_allocation.as_mapping().items())),
                "execution_date": (
                    item.execution_date.isoformat() if item.execution_date is not None else None
                ),
                "execution_status": "submitted" if item.submitted_to_backtest else "omitted",
                "omission_reason": item.omission_reason,
            }
        )
        for item in records
    )
    return MappingProxyType(
        {
            "source": "StrategyBacktestResult.signal_records",
            "records": payload,
        }
    )


__all__ = ["strategy_execution_provenance"]
