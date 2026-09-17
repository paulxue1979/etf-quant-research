"""Internal, non-official outcome produced by controlled OOS execution.

The object is deliberately not an ``OosEvaluationResult``.  PHASE 8F-3 may
calculate a backtest and analytics, but only PHASE 8F-4 may publish an official
observation or change the research protocol.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from analytics.models import PerformanceAnalysisResult
from backtest.models import BacktestResult
from data.models import PriceField
from research.canonical import sha256_hash
from research.oos import OosDataProvenance

_HASH = re.compile(r"^[0-9a-f]{64}$")


def _canonical_backtest_payload(value: object) -> object:
    """Normalize frozen dataclass fields without deepcopying mapping proxies."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_backtest_payload(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {key: _canonical_backtest_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_backtest_payload(item) for item in value]
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _hash(value: object, label: str) -> str:
    result = _text(value, label).lower()
    if not _HASH.fullmatch(result):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return result


@dataclass(frozen=True, repr=False)
class OosExecutionOutcome:
    """Immutable calculation output retained in memory until finalization."""

    execution_id: str
    protocol_id: str
    oos_spec_hash: str
    strategy_version_id: str
    strategy_content_hash: str
    backtest_result: BacktestResult
    performance_analysis: PerformanceAnalysisResult
    data_provenance: OosDataProvenance
    warmup_request_start: date
    oos_start: date
    oos_end: date
    engine_version: str
    analytics_version: str
    outcome_hash: str | None = None
    strategy_provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for label in (
            "execution_id",
            "protocol_id",
            "strategy_version_id",
            "engine_version",
            "analytics_version",
        ):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        for label in ("oos_spec_hash", "strategy_content_hash"):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        for label in ("warmup_request_start", "oos_start", "oos_end"):
            value = getattr(self, label)
            if not isinstance(value, date):
                raise ValueError(f"{label} must be a date")
        if self.warmup_request_start > self.oos_start or self.oos_start > self.oos_end:
            raise ValueError("OOS outcome date range is invalid")
        if not isinstance(self.backtest_result, BacktestResult):
            raise TypeError("backtest_result must be a BacktestResult")
        if not isinstance(self.performance_analysis, PerformanceAnalysisResult):
            raise TypeError("performance_analysis must be a PerformanceAnalysisResult")
        if not isinstance(self.data_provenance, OosDataProvenance):
            raise TypeError("data_provenance must be OosDataProvenance")
        if self.strategy_provenance is not None and not isinstance(
            self.strategy_provenance, Mapping
        ):
            raise TypeError("strategy_provenance must be a mapping or None")
        if self.backtest_result.strategy_version_id != self.strategy_version_id:
            raise ValueError("backtest result strategy version does not match outcome")
        if self.performance_analysis.strategy_version_id != self.strategy_version_id:
            raise ValueError("analytics strategy version does not match outcome")
        if (
            self.backtest_result.start_date != self.oos_start
            or self.backtest_result.end_date != self.oos_end
        ):
            raise ValueError("backtest result must cover exactly the OOS range")
        if (
            self.performance_analysis.start_date != self.oos_start
            or self.performance_analysis.end_date != self.oos_end
        ):
            raise ValueError("performance analysis must cover exactly the OOS range")
        if self.backtest_result.engine_version != self.engine_version:
            raise ValueError("backtest engine version does not match outcome")
        try:
            price_field = PriceField(
                self.backtest_result.configuration_snapshot["price_field_used"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("backtest result price field is invalid") from exc
        if self.performance_analysis.price_field_used is not price_field:
            raise ValueError("analytics price field does not match backtest result")
        if self.data_provenance.price_field is not price_field:
            raise ValueError("data provenance price field does not match backtest result")
        if self.data_provenance.requested_start != self.oos_start:
            raise ValueError("data provenance requested_start must equal OOS start")
        if self.data_provenance.requested_end != self.oos_end:
            raise ValueError("data provenance requested_end must equal OOS end")
        if self.data_provenance.warmup_start != self.warmup_request_start:
            raise ValueError("data provenance warmup_start must match data request")
        if self.strategy_provenance is not None:
            object.__setattr__(
                self,
                "strategy_provenance",
                MappingProxyType(dict(self.strategy_provenance)),
            )
        expected = sha256_hash(self.semantic_payload())
        if self.outcome_hash is not None and _hash(self.outcome_hash, "outcome_hash") != expected:
            raise ValueError("outcome_hash does not match semantic outcome")
        object.__setattr__(self, "outcome_hash", expected)

    def semantic_payload(self) -> dict[str, Any]:
        """Return hash input without exposing the full calculation in logs."""
        return {
            "execution_id": self.execution_id,
            "protocol_id": self.protocol_id,
            "oos_spec_hash": self.oos_spec_hash,
            "strategy_version_id": self.strategy_version_id,
            "strategy_content_hash": self.strategy_content_hash,
            "backtest_result_hash": sha256_hash(_canonical_backtest_payload(self.backtest_result)),
            "performance_analysis_hash": sha256_hash(self.performance_analysis),
            "data_provenance": self.data_provenance.to_dict(),
            "warmup_request_start": self.warmup_request_start.isoformat(),
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "engine_version": self.engine_version,
            "analytics_version": self.analytics_version,
            "strategy_provenance": self.strategy_provenance,
        }

    def __repr__(self) -> str:
        """Keep metrics, trades, prices, and equity out of diagnostics."""
        return (
            "OosExecutionOutcome("
            f"execution_id={self.execution_id!r}, protocol_id={self.protocol_id!r}, "
            f"strategy_version_id={self.strategy_version_id!r}, outcome_hash={self.outcome_hash!r})"
        )


__all__ = ["OosExecutionOutcome"]
