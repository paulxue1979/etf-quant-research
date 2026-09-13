"""Pure domain contracts for controlled out-of-sample evaluation.

This module defines the legal boundary for PHASE 8F OOS work.  It intentionally
contains no persistence, network, threading, execution, backtest, or analytics
orchestration.  Later phases must use these immutable records rather than
reconstructing OOS identity or configuration at runtime.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from analytics.models import MetricValue, PerformanceAnalysisResult
from backend.app.research_protocol import (
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocol,
    SelectionDecision,
    StrategyFreezeRecord,
)
from backtest.models import BacktestConfig, ExecutionRule, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research.canonical import sha256_hash
from research.exceptions import (
    OosAlreadyObservedError,
    OosConfigurationMismatchError,
    OosDomainError,
    OosPreconditionError,
    OosProvenanceError,
    OosRangeMismatchError,
    OosResultIntegrityError,
    OosStrategyIdentityMismatchError,
)
from strategies.models import StrategyVersion

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_PARTS = ("secret", "api_key", "apikey", "token", "password", "credential")
_SECRET_SENTINELS = ("SECRET_SENTINEL_8F1",)
_ALLOWED_METRICS = (
    "total_return",
    "cagr",
    "volatility",
    "annualized_volatility",
    "sharpe",
    "sharpe_ratio",
    "sortino",
    "sortino_ratio",
    "max_drawdown",
    "calmar",
    "calmar_ratio",
    "win_rate",
    "closed_trade_win_rate",
    "profit_factor",
    "average_return",
    "average_trade_return",
    "best_return",
    "best_trade",
    "worst_return",
    "worst_trade",
    "average_holding_period",
    "turnover",
    "max_drawdown_duration",
    "recovery_duration",
)


class OosExecutionStatus(StrEnum):
    """Execution states reserved for the future OOS executor."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PortfolioInitializationMode(StrEnum):
    """Portfolio initialization allowed by the PHASE 8F V1 contract."""

    FRESH_CAPITAL = "fresh_capital"


class OosBoundarySignalPolicy(StrEnum):
    """Signal boundary semantics; execution remains owned by later phases."""

    OOS_SIGNALS_ONLY = "oos_signals_only"


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OosDomainError(f"{label} must be a non-empty string", code="OOS_INVALID_VALUE")
    return value.strip()


def _hash(value: object, label: str) -> str:
    candidate = _text(value, label).lower()
    if not _HASH_PATTERN.fullmatch(candidate):
        raise OosDomainError(f"{label} must be a SHA-256 hex digest", code="OOS_INVALID_HASH")
    return candidate


def _finite(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OosDomainError(f"{label} must be a finite number", code="OOS_INVALID_NUMERIC_VALUE")
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0):
        raise OosDomainError(f"{label} must be a finite number", code="OOS_INVALID_NUMERIC_VALUE")
    return numeric


def _date(value: object, label: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise OosDomainError(f"{label} must be a date", code="OOS_INVALID_DATE")
    return value


def _freeze(value: object) -> object:
    """Recursively freeze JSON-like nested values for safe domain immutability."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float) and not math.isfinite(value):
        raise OosDomainError(
            "nested numeric values must be finite", code="OOS_INVALID_NUMERIC_VALUE"
        )
    return value


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _safe_provenance_value(value: object, path: str = "provenance") -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in _SECRET_KEY_PARTS):
                raise OosProvenanceError(
                    f"{path} contains a forbidden credential field", code="OOS_PROVENANCE_SECRET"
                )
            safe[key_text] = _safe_provenance_value(item, f"{path}.{key_text}")
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_provenance_value(item, f"{path}[]") for item in value]
    if isinstance(value, str):
        if any(sentinel in value for sentinel in _SECRET_SENTINELS):
            raise OosProvenanceError(
                f"{path} contains a forbidden secret value", code="OOS_PROVENANCE_SECRET"
            )
        if value.startswith(("/", "~/")) or "\\" in value and ":" in value[:3]:
            raise OosProvenanceError(
                f"{path} must not contain a local filesystem path", code="OOS_PROVENANCE_PATH"
            )
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _finite(value, path)
        except OosDomainError as exc:
            raise OosProvenanceError(
                f"{path} must be a finite number", code="OOS_PROVENANCE_INVALID"
            ) from exc
    if value is None or isinstance(value, bool) or isinstance(value, (date, datetime)):
        return value
    raise OosProvenanceError(f"{path} contains an unsupported value", code="OOS_PROVENANCE_INVALID")


@dataclass(frozen=True)
class OosEvaluationIdentity:
    """The complete frozen identity of one official OOS evaluation."""

    protocol_id: str
    selection_decision_id: str
    strategy_freeze_id: str
    strategy_version_id: str
    strategy_content_hash: str

    def __post_init__(self) -> None:
        for field in (
            "protocol_id",
            "selection_decision_id",
            "strategy_freeze_id",
            "strategy_version_id",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "strategy_content_hash",
            _hash(self.strategy_content_hash, "strategy_content_hash"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "protocol_id": self.protocol_id,
            "selection_decision_id": self.selection_decision_id,
            "strategy_freeze_id": self.strategy_freeze_id,
            "strategy_version_id": self.strategy_version_id,
            "strategy_content_hash": self.strategy_content_hash,
        }

    @classmethod
    def from_dict(cls, payload: object) -> OosEvaluationIdentity:
        if not isinstance(payload, Mapping):
            raise OosDomainError("OOS identity must be an object", code="OOS_INVALID_IDENTITY")
        return cls(**{field: payload.get(field, "") for field in cls.__dataclass_fields__})


@dataclass(frozen=True)
class OosEvaluationRange:
    """Warm-up and inclusive OOS performance boundaries."""

    oos_start: date
    oos_end: date
    warmup_start: date
    evaluation_start: date
    evaluation_end: date

    def __post_init__(self) -> None:
        for field in ("oos_start", "oos_end", "warmup_start", "evaluation_start", "evaluation_end"):
            object.__setattr__(self, field, _date(getattr(self, field), field))
        if self.oos_start > self.oos_end:
            raise OosDomainError("OOS start must be on or before OOS end", code="OOS_RANGE_INVALID")
        if self.evaluation_start != self.oos_start or self.evaluation_end != self.oos_end:
            raise OosRangeMismatchError(
                "evaluation range must exactly equal the frozen OOS range",
                code="OOS_RANGE_MISMATCH",
            )
        if self.warmup_start > self.evaluation_start:
            raise OosRangeMismatchError(
                "warmup_start must be on or before OOS evaluation_start", code="OOS_RANGE_MISMATCH"
            )

    @classmethod
    def from_protocol(cls, protocol: ResearchProtocol, *, warmup_start: date) -> OosEvaluationRange:
        return cls(
            oos_start=protocol.oos_start_date,
            oos_end=protocol.oos_end_date,
            warmup_start=warmup_start,
            evaluation_start=protocol.oos_start_date,
            evaluation_end=protocol.oos_end_date,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "warmup_start": self.warmup_start.isoformat(),
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> OosEvaluationRange:
        if not isinstance(payload, Mapping):
            raise OosDomainError("OOS range must be an object", code="OOS_INVALID_RANGE")
        try:
            return cls(
                **{
                    field: date.fromisoformat(str(payload.get(field, "")))
                    for field in cls.__dataclass_fields__
                }
            )
        except (TypeError, ValueError, OosDomainError) as exc:
            if isinstance(exc, OosDomainError):
                raise
            raise OosDomainError("OOS range is malformed", code="OOS_INVALID_RANGE") from exc


@dataclass(frozen=True)
class OosEvaluationConfig:
    """Immutable result-affecting configuration copied from frozen BacktestConfig."""

    initial_capital: float
    commission: float
    commission_per_order: float
    slippage: float
    price_field_used: PriceField
    execution_rule: ExecutionRule
    fractional_shares: bool
    rebalance_policy: RebalancePolicy
    engine_version: str
    analytics_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "initial_capital", _finite(self.initial_capital, "initial_capital", positive=True)
        )
        for field in ("commission", "commission_per_order"):
            value = _finite(getattr(self, field), field)
            if value < 0:
                raise OosDomainError(
                    f"{field} must be non-negative", code="OOS_INVALID_CONFIGURATION"
                )
            object.__setattr__(self, field, value)
        slippage = _finite(self.slippage, "slippage")
        if not 0 <= slippage < 1:
            raise OosDomainError("slippage must be in [0, 1)", code="OOS_INVALID_CONFIGURATION")
        object.__setattr__(self, "slippage", slippage)
        try:
            price_field = (
                self.price_field_used
                if isinstance(self.price_field_used, PriceField)
                else PriceField(self.price_field_used)
            )
            execution_rule = (
                self.execution_rule
                if isinstance(self.execution_rule, ExecutionRule)
                else ExecutionRule(self.execution_rule)
            )
        except (TypeError, ValueError) as exc:
            raise OosDomainError(
                "price_field_used or execution_rule is invalid", code="OOS_INVALID_CONFIGURATION"
            ) from exc
        if not isinstance(self.fractional_shares, bool):
            raise OosDomainError(
                "fractional_shares must be boolean", code="OOS_INVALID_CONFIGURATION"
            )
        rebalance = self.rebalance_policy
        if isinstance(rebalance, Mapping):
            try:
                rebalance = RebalancePolicy(
                    frequency=RebalanceFrequency(rebalance.get("frequency", "")),
                    threshold=rebalance.get("threshold"),
                )
            except (TypeError, ValueError) as exc:
                raise OosDomainError(
                    "rebalance_policy is invalid", code="OOS_INVALID_CONFIGURATION"
                ) from exc
        if not isinstance(rebalance, RebalancePolicy):
            raise OosDomainError("rebalance_policy is invalid", code="OOS_INVALID_CONFIGURATION")
        object.__setattr__(self, "price_field_used", price_field)
        object.__setattr__(self, "execution_rule", execution_rule)
        object.__setattr__(self, "rebalance_policy", rebalance)
        object.__setattr__(self, "engine_version", _text(self.engine_version, "engine_version"))
        object.__setattr__(
            self, "analytics_version", _text(self.analytics_version, "analytics_version")
        )

    @classmethod
    def from_backtest_config(
        cls, config: BacktestConfig, *, analytics_version: str
    ) -> OosEvaluationConfig:
        if not isinstance(config, BacktestConfig):
            raise OosDomainError(
                "config must be a BacktestConfig", code="OOS_INVALID_CONFIGURATION"
            )
        return cls(
            initial_capital=config.initial_capital,
            commission=config.commission.rate,
            commission_per_order=config.commission.per_order,
            slippage=config.slippage,
            price_field_used=config.price_field_used,
            execution_rule=config.execution_rule,
            fractional_shares=config.fractional_shares,
            rebalance_policy=config.rebalance_policy,
            engine_version="phase-3.0",
            analytics_version=analytics_version,
        )

    @classmethod
    def from_research_evaluation_config(
        cls, config: ResearchEvaluationConfig, *, analytics_version: str
    ) -> OosEvaluationConfig:
        """Adapt the frozen PHASE 7 config without accepting caller overrides."""
        if not isinstance(config, ResearchEvaluationConfig):
            raise OosDomainError(
                "config must be a ResearchEvaluationConfig", code="OOS_INVALID_CONFIGURATION"
            )
        return cls(
            initial_capital=config.initial_capital,
            commission=config.commission["rate"],
            commission_per_order=config.commission["per_order"],
            slippage=config.slippage,
            price_field_used=config.price_field_used,
            execution_rule=config.execution_rule,
            fractional_shares=config.fractional_shares,
            rebalance_policy=config.rebalance_policy,
            engine_version=config.engine_version,
            analytics_version=analytics_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "commission": self.commission,
            "commission_per_order": self.commission_per_order,
            "slippage": self.slippage,
            "price_field_used": self.price_field_used.value,
            "execution_rule": self.execution_rule.value,
            "fractional_shares": self.fractional_shares,
            "rebalance_policy": {
                "frequency": self.rebalance_policy.frequency.value,
                "threshold": self.rebalance_policy.threshold,
            },
            "engine_version": self.engine_version,
            "analytics_version": self.analytics_version,
        }

    def canonical_json(self) -> str:
        from research.canonical import canonical_json

        return canonical_json(self.to_dict())

    @property
    def configuration_hash(self) -> str:
        return sha256_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> OosEvaluationConfig:
        if not isinstance(payload, Mapping):
            raise OosDomainError(
                "OOS configuration must be an object", code="OOS_INVALID_CONFIGURATION"
            )
        commission = payload.get("commission", 0.0)
        per_order = payload.get("commission_per_order", 0.0)
        if isinstance(commission, Mapping):
            per_order = commission.get("per_order", per_order)
            commission = commission.get("rate", 0.0)
        return cls(
            initial_capital=payload.get("initial_capital"),
            commission=commission,
            commission_per_order=per_order,
            slippage=payload.get("slippage"),
            price_field_used=payload.get("price_field_used", ""),
            execution_rule=payload.get("execution_rule", ""),
            fractional_shares=payload.get("fractional_shares"),
            rebalance_policy=payload.get("rebalance_policy", {}),
            engine_version=payload.get("engine_version", ""),
            analytics_version=payload.get("analytics_version", ""),
        )


@dataclass(frozen=True)
class OosDataProvenance:
    """Safe, minimal provenance; never carries credentials or local paths."""

    source: str
    data_reference: object
    requested_start: date
    requested_end: date
    warmup_start: date
    frequency: str = "daily"
    price_field: PriceField = PriceField.ADJUSTED_CLOSE
    cache_reference: object | None = None
    retrieved_at: datetime | None = None
    source_timestamp: datetime | None = None
    snapshot_reference: object | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _text(self.source, "source"))
        object.__setattr__(
            self, "data_reference", _freeze(_safe_provenance_value(self.data_reference))
        )
        for field in ("requested_start", "requested_end", "warmup_start"):
            object.__setattr__(self, field, _date(getattr(self, field), field))
        if self.requested_start > self.requested_end or self.warmup_start > self.requested_start:
            raise OosProvenanceError(
                "provenance date range is invalid", code="OOS_PROVENANCE_MISMATCH"
            )
        if not isinstance(self.frequency, str) or not self.frequency.strip():
            raise OosProvenanceError("frequency must be non-empty", code="OOS_PROVENANCE_INVALID")
        try:
            price_field = (
                self.price_field
                if isinstance(self.price_field, PriceField)
                else PriceField(self.price_field)
            )
        except (TypeError, ValueError) as exc:
            raise OosProvenanceError(
                "price_field is invalid", code="OOS_PROVENANCE_INVALID"
            ) from exc
        object.__setattr__(self, "frequency", self.frequency.strip())
        object.__setattr__(self, "price_field", price_field)
        for field in ("cache_reference", "snapshot_reference"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _freeze(_safe_provenance_value(value, field)))
        for field in ("retrieved_at", "source_timestamp"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, datetime):
                raise OosProvenanceError(
                    f"{field} must be a datetime", code="OOS_PROVENANCE_INVALID"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "data_reference": _plain(self.data_reference),
            "cache_reference": _plain(self.cache_reference),
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "warmup_start": self.warmup_start.isoformat(),
            "frequency": self.frequency,
            "price_field": self.price_field.value,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "source_timestamp": self.source_timestamp.isoformat()
            if self.source_timestamp
            else None,
            "snapshot_reference": _plain(self.snapshot_reference),
        }


@dataclass(frozen=True)
class OosEvaluationSpec:
    """Immutable specification consumed by a future OOS executor."""

    identity: OosEvaluationIdentity
    evaluation_range: OosEvaluationRange
    configuration: OosEvaluationConfig
    data_provenance: OosDataProvenance
    portfolio_initialization: PortfolioInitializationMode = (
        PortfolioInitializationMode.FRESH_CAPITAL
    )
    boundary_signal_policy: OosBoundarySignalPolicy = OosBoundarySignalPolicy.OOS_SIGNALS_ONLY
    one_shot: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.identity, OosEvaluationIdentity):
            raise OosDomainError("identity is invalid", code="OOS_INVALID_SPEC")
        if not isinstance(self.evaluation_range, OosEvaluationRange):
            raise OosDomainError("evaluation_range is invalid", code="OOS_INVALID_SPEC")
        if not isinstance(self.configuration, OosEvaluationConfig):
            raise OosDomainError("configuration is invalid", code="OOS_INVALID_SPEC")
        if not isinstance(self.data_provenance, OosDataProvenance):
            raise OosDomainError("data_provenance is invalid", code="OOS_INVALID_SPEC")
        try:
            init = (
                self.portfolio_initialization
                if isinstance(self.portfolio_initialization, PortfolioInitializationMode)
                else PortfolioInitializationMode(self.portfolio_initialization)
            )
            signal_policy = (
                self.boundary_signal_policy
                if isinstance(self.boundary_signal_policy, OosBoundarySignalPolicy)
                else OosBoundarySignalPolicy(self.boundary_signal_policy)
            )
        except (TypeError, ValueError) as exc:
            raise OosDomainError("OOS policies are invalid", code="OOS_INVALID_SPEC") from exc
        if not isinstance(self.one_shot, bool) or not self.one_shot:
            raise OosDomainError("OOS evaluation must be one-shot", code="OOS_INVALID_SPEC")
        object.__setattr__(self, "portfolio_initialization", init)
        object.__setattr__(self, "boundary_signal_policy", signal_policy)
        if self.data_provenance.requested_start != self.evaluation_range.oos_start:
            raise OosRangeMismatchError(
                "provenance requested_start must equal OOS start", code="OOS_RANGE_MISMATCH"
            )
        if self.data_provenance.requested_end != self.evaluation_range.oos_end:
            raise OosRangeMismatchError(
                "provenance requested_end must equal OOS end", code="OOS_RANGE_MISMATCH"
            )
        if self.data_provenance.warmup_start != self.evaluation_range.warmup_start:
            raise OosRangeMismatchError(
                "provenance warmup_start must equal evaluation warmup_start",
                code="OOS_RANGE_MISMATCH",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "evaluation_range": self.evaluation_range.to_dict(),
            "configuration": self.configuration.to_dict(),
            "data_provenance": self.data_provenance.to_dict(),
            "portfolio_initialization": self.portfolio_initialization.value,
            "boundary_signal_policy": self.boundary_signal_policy.value,
            "one_shot": self.one_shot,
        }

    def canonical_json(self) -> str:
        from research.canonical import canonical_json

        return canonical_json(self.to_dict())

    @property
    def spec_hash(self) -> str:
        return sha256_hash(self.to_dict())


@dataclass(frozen=True)
class OosPerformanceSummary:
    """Immutable metric container; analytics are produced by PHASE 4I."""

    metrics: Mapping[str, MetricValue]

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, Mapping) or not self.metrics:
            raise OosDomainError(
                "performance summary metrics must not be empty", code="OOS_INVALID_SUMMARY"
            )
        normalized: dict[str, MetricValue] = {}
        for key, value in self.metrics.items():
            key_text = _text(key, "metric name")
            if key_text not in _ALLOWED_METRICS:
                raise OosDomainError(
                    f"unsupported performance metric: {key_text}", code="OOS_INVALID_SUMMARY"
                )
            if not isinstance(value, MetricValue):
                raise OosDomainError(
                    "performance metrics must be MetricValue values", code="OOS_INVALID_SUMMARY"
                )
            normalized[key_text] = value
        object.__setattr__(self, "metrics", MappingProxyType(dict(sorted(normalized.items()))))

    @classmethod
    def from_analysis(cls, analysis: PerformanceAnalysisResult) -> OosPerformanceSummary:
        if not isinstance(analysis, PerformanceAnalysisResult):
            raise OosDomainError(
                "analysis must be PerformanceAnalysisResult", code="OOS_INVALID_SUMMARY"
            )
        metrics: dict[str, MetricValue] = {
            "total_return": analysis.total_return,
            "cagr": analysis.cagr,
            "annualized_volatility": analysis.annualized_volatility,
            "sharpe_ratio": analysis.sharpe_ratio,
            "sortino_ratio": analysis.sortino_ratio,
            "max_drawdown": analysis.max_drawdown,
            "max_drawdown_duration": analysis.max_drawdown_duration,
            "recovery_duration": analysis.recovery_duration,
            "calmar_ratio": analysis.calmar_ratio,
            "win_rate": analysis.trade_metrics.win_rate,
            "profit_factor": analysis.trade_metrics.profit_factor,
            "average_trade_return": analysis.trade_metrics.average_trade_return,
            "best_trade": analysis.trade_metrics.best_trade,
            "worst_trade": analysis.trade_metrics.worst_trade,
            "average_holding_period": analysis.trade_metrics.average_holding_period,
            "turnover": analysis.trade_metrics.turnover,
        }
        return cls(metrics=metrics)

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": {key: value.to_dict() for key, value in self.metrics.items()}}


@dataclass(frozen=True)
class OosEvaluationResult:
    """Immutable official OOS observation; persistence belongs to later phases."""

    oos_result_id: str
    protocol_id: str
    selection_decision_id: str
    strategy_freeze_id: str
    strategy_version_id: str
    strategy_content_hash: str
    backtest_run_id: str
    oos_start: date
    oos_end: date
    warmup_start: date
    price_field_used: PriceField
    configuration_hash: str
    engine_version: str
    analytics_version: str
    data_provenance: OosDataProvenance
    performance_summary: OosPerformanceSummary
    created_at: datetime
    result_hash: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "oos_result_id",
            "protocol_id",
            "selection_decision_id",
            "strategy_freeze_id",
            "strategy_version_id",
            "backtest_run_id",
            "engine_version",
            "analytics_version",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "strategy_content_hash",
            _hash(self.strategy_content_hash, "strategy_content_hash"),
        )
        object.__setattr__(
            self, "configuration_hash", _hash(self.configuration_hash, "configuration_hash")
        )
        for field in ("oos_start", "oos_end", "warmup_start"):
            object.__setattr__(self, field, _date(getattr(self, field), field))
        if self.oos_start > self.oos_end or self.warmup_start > self.oos_start:
            raise OosResultIntegrityError(
                "OOS result range is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        try:
            price_field = (
                self.price_field_used
                if isinstance(self.price_field_used, PriceField)
                else PriceField(self.price_field_used)
            )
        except (TypeError, ValueError) as exc:
            raise OosResultIntegrityError(
                "price_field_used is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            ) from exc
        if not isinstance(self.data_provenance, OosDataProvenance):
            raise OosResultIntegrityError(
                "data_provenance is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        if not isinstance(self.performance_summary, OosPerformanceSummary):
            raise OosResultIntegrityError(
                "performance_summary is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        if not isinstance(self.created_at, datetime):
            raise OosResultIntegrityError(
                "created_at must be a datetime", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        object.__setattr__(self, "price_field_used", price_field)
        if self.data_provenance.requested_start != self.oos_start:
            raise OosResultIntegrityError(
                "provenance requested_start does not match result",
                code="OOS_RESULT_INTEGRITY_ERROR",
            )
        if self.data_provenance.requested_end != self.oos_end:
            raise OosResultIntegrityError(
                "provenance requested_end does not match result", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        if self.data_provenance.warmup_start != self.warmup_start:
            raise OosResultIntegrityError(
                "provenance warmup_start does not match result", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        if self.data_provenance.price_field != price_field:
            raise OosResultIntegrityError(
                "provenance price_field does not match result", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        expected = sha256_hash(self.semantic_payload())
        if self.result_hash is not None and _hash(self.result_hash, "result_hash") != expected:
            raise OosResultIntegrityError(
                "result_hash does not match semantic result", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        object.__setattr__(self, "result_hash", expected)

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "oos_result_id": self.oos_result_id,
            "protocol_id": self.protocol_id,
            "selection_decision_id": self.selection_decision_id,
            "strategy_freeze_id": self.strategy_freeze_id,
            "strategy_version_id": self.strategy_version_id,
            "strategy_content_hash": self.strategy_content_hash,
            "backtest_run_id": self.backtest_run_id,
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "warmup_start": self.warmup_start.isoformat(),
            "price_field_used": self.price_field_used.value,
            "configuration_hash": self.configuration_hash,
            "engine_version": self.engine_version,
            "analytics_version": self.analytics_version,
            "data_provenance": self.data_provenance.to_dict(),
            "performance_summary": self.performance_summary.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_payload(),
            "created_at": self.created_at.isoformat(),
            "result_hash": self.result_hash,
        }

    @classmethod
    def from_dict(cls, payload: object) -> OosEvaluationResult:
        if not isinstance(payload, Mapping):
            raise OosResultIntegrityError(
                "OOS result must be an object", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        provenance_payload = payload.get("data_provenance")
        if not isinstance(provenance_payload, Mapping):
            raise OosResultIntegrityError(
                "data_provenance is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        summary_payload = payload.get("performance_summary")
        if not isinstance(summary_payload, Mapping):
            raise OosResultIntegrityError(
                "performance_summary is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        metrics_payload = summary_payload.get("metrics")
        if not isinstance(metrics_payload, Mapping):
            raise OosResultIntegrityError(
                "performance_summary is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
            )
        try:
            metrics: dict[str, MetricValue] = {}
            for key, item in metrics_payload.items():
                if not isinstance(item, Mapping):
                    raise OosResultIntegrityError(
                        "metric value is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
                    )
                metrics[key] = MetricValue(
                    value=item.get("value"),
                    status=item.get("status"),
                    reason=item.get("reason"),
                )
            provenance = OosDataProvenance(
                source=provenance_payload.get("source", ""),
                data_reference=provenance_payload.get("data_reference"),
                cache_reference=provenance_payload.get("cache_reference"),
                requested_start=date.fromisoformat(
                    str(provenance_payload.get("requested_start", ""))
                ),
                requested_end=date.fromisoformat(str(provenance_payload.get("requested_end", ""))),
                warmup_start=date.fromisoformat(str(provenance_payload.get("warmup_start", ""))),
                frequency=provenance_payload.get("frequency", "daily"),
                price_field=provenance_payload.get("price_field", ""),
                retrieved_at=(
                    datetime.fromisoformat(str(provenance_payload["retrieved_at"]))
                    if provenance_payload.get("retrieved_at")
                    else None
                ),
                source_timestamp=(
                    datetime.fromisoformat(str(provenance_payload["source_timestamp"]))
                    if provenance_payload.get("source_timestamp")
                    else None
                ),
                snapshot_reference=provenance_payload.get("snapshot_reference"),
            )
            return cls(
                oos_result_id=payload.get("oos_result_id", ""),
                protocol_id=payload.get("protocol_id", ""),
                selection_decision_id=payload.get("selection_decision_id", ""),
                strategy_freeze_id=payload.get("strategy_freeze_id", ""),
                strategy_version_id=payload.get("strategy_version_id", ""),
                strategy_content_hash=payload.get("strategy_content_hash", ""),
                backtest_run_id=payload.get("backtest_run_id", ""),
                oos_start=date.fromisoformat(str(payload.get("oos_start", ""))),
                oos_end=date.fromisoformat(str(payload.get("oos_end", ""))),
                warmup_start=date.fromisoformat(str(payload.get("warmup_start", ""))),
                price_field_used=payload.get("price_field_used", ""),
                configuration_hash=payload.get("configuration_hash", ""),
                engine_version=payload.get("engine_version", ""),
                analytics_version=payload.get("analytics_version", ""),
                data_provenance=provenance,
                performance_summary=OosPerformanceSummary(metrics=metrics),
                created_at=datetime.fromisoformat(str(payload.get("created_at", ""))),
                result_hash=payload.get("result_hash"),
            )
        except OosDomainError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise OosResultIntegrityError(
                "OOS result payload is malformed", code="OOS_RESULT_INTEGRITY_ERROR"
            ) from exc


def validate_oos_strategy_identity(
    selection_decision: SelectionDecision,
    strategy_freeze: StrategyFreezeRecord,
    strategy_version: StrategyVersion,
    identity: OosEvaluationIdentity,
) -> None:
    """Reject any identity that is not exactly bound to the frozen version."""
    if not all(
        isinstance(
            item, (SelectionDecision, StrategyFreezeRecord, StrategyVersion, OosEvaluationIdentity)
        )
        for item in (selection_decision, strategy_freeze, strategy_version, identity)
    ):
        raise OosStrategyIdentityMismatchError(
            "OOS strategy identity inputs are invalid", code="OOS_STRATEGY_IDENTITY_MISMATCH"
        )
    values = {
        "protocol_id": (
            selection_decision.protocol_id,
            strategy_freeze.protocol_id,
            identity.protocol_id,
        ),
        "selection_decision_id": (
            selection_decision.decision_id,
            strategy_freeze.selection_decision_id,
            identity.selection_decision_id,
        ),
        "strategy_freeze_id": (strategy_freeze.freeze_id, identity.strategy_freeze_id),
        "strategy_version_id": (
            selection_decision.selected_strategy_version_id,
            strategy_freeze.strategy_version_id,
            strategy_version.version_id,
            identity.strategy_version_id,
        ),
        "strategy_content_hash": (
            strategy_freeze.strategy_version_content_hash,
            strategy_version.content_hash,
            identity.strategy_content_hash,
        ),
    }
    if any(len(set(values_for_field)) != 1 for values_for_field in values.values()):
        raise OosStrategyIdentityMismatchError(
            "OOS strategy identity does not match the frozen strategy",
            code="OOS_STRATEGY_IDENTITY_MISMATCH",
        )


def validate_oos_preconditions(
    protocol: ResearchProtocol,
    selection_decision: SelectionDecision | None,
    strategy_freeze: StrategyFreezeRecord | None,
    strategy_version: StrategyVersion | None,
    *,
    requested_oos_start: date | None = None,
    requested_oos_end: date | None = None,
) -> OosEvaluationIdentity:
    """Validate the immutable protocol prerequisites before building an OOS spec."""
    if not isinstance(protocol, ResearchProtocol):
        raise OosPreconditionError("research protocol is invalid", code="OOS_PRECONDITION_FAILED")
    if protocol.status != ProtocolStatus.SELECTION_RECORDED:
        raise OosPreconditionError(
            "protocol must be in selection_recorded state", code="OOS_PROTOCOL_STATE_INVALID"
        )
    if selection_decision is None or strategy_freeze is None or strategy_version is None:
        raise OosPreconditionError(
            "selection, freeze, and persisted strategy version are required",
            code="OOS_PRECONDITION_FAILED",
        )
    if requested_oos_start is not None and requested_oos_start != protocol.oos_start_date:
        raise OosRangeMismatchError(
            "caller OOS start does not match frozen protocol", code="OOS_RANGE_MISMATCH"
        )
    if requested_oos_end is not None and requested_oos_end != protocol.oos_end_date:
        raise OosRangeMismatchError(
            "caller OOS end does not match frozen protocol", code="OOS_RANGE_MISMATCH"
        )
    identity = OosEvaluationIdentity(
        protocol_id=protocol.protocol_id,
        selection_decision_id=selection_decision.decision_id,
        strategy_freeze_id=strategy_freeze.freeze_id,
        strategy_version_id=strategy_freeze.strategy_version_id,
        strategy_content_hash=strategy_freeze.strategy_version_content_hash,
    )
    if (
        selection_decision.protocol_id != protocol.protocol_id
        or strategy_freeze.protocol_id != protocol.protocol_id
    ):
        raise OosPreconditionError(
            "selection and freeze must belong to protocol", code="OOS_PRECONDITION_FAILED"
        )
    if strategy_freeze.selection_decision_id != selection_decision.decision_id:
        raise OosPreconditionError(
            "freeze must reference the official selection", code="OOS_PRECONDITION_FAILED"
        )
    validate_oos_strategy_identity(selection_decision, strategy_freeze, strategy_version, identity)
    if protocol.evaluation_config is None:
        raise OosPreconditionError(
            "protocol must contain a frozen evaluation configuration",
            code="OOS_PRECONDITION_FAILED",
        )
    return identity


def validate_oos_range(
    is_start: date,
    is_end: date,
    oos_start: date,
    oos_end: date,
    *,
    warmup_start: date | None = None,
) -> None:
    """Validate inclusive IS/OOS ranges and warm-up boundary."""
    for value, label in (
        (is_start, "is_start"),
        (is_end, "is_end"),
        (oos_start, "oos_start"),
        (oos_end, "oos_end"),
    ):
        _date(value, label)
    if is_start > is_end or oos_start > oos_end:
        raise OosRangeMismatchError("date range ordering is invalid", code="OOS_RANGE_MISMATCH")
    if is_end >= oos_start:
        raise OosRangeMismatchError("IS and OOS ranges must not overlap", code="OOS_RANGE_MISMATCH")
    if warmup_start is not None and _date(warmup_start, "warmup_start") > oos_start:
        raise OosRangeMismatchError(
            "warmup_start must be on or before OOS start", code="OOS_RANGE_MISMATCH"
        )


def validate_oos_configuration(expected: OosEvaluationConfig, actual: OosEvaluationConfig) -> None:
    """Require exact canonical configuration equality; no tolerance or defaults."""
    if not isinstance(expected, OosEvaluationConfig) or not isinstance(actual, OosEvaluationConfig):
        raise OosConfigurationMismatchError(
            "OOS configurations are invalid", code="OOS_CONFIGURATION_MISMATCH"
        )
    if expected.to_dict() != actual.to_dict():
        fields = [
            key
            for key in expected.to_dict()
            if expected.to_dict()[key] != actual.to_dict().get(key)
        ]
        raise OosConfigurationMismatchError(
            "OOS configuration does not match frozen configuration",
            code="OOS_CONFIGURATION_MISMATCH",
            details={"fields": fields},
        )


def validate_one_shot_result(
    existing_result: OosEvaluationResult | None,
    candidate_result: OosEvaluationResult,
) -> None:
    """Allow only an exact idempotent replay of an already official result."""
    if not isinstance(candidate_result, OosEvaluationResult):
        raise OosResultIntegrityError(
            "candidate OOS result is invalid", code="OOS_RESULT_INTEGRITY_ERROR"
        )
    if existing_result is None:
        return
    if (
        not isinstance(existing_result, OosEvaluationResult)
        or existing_result.protocol_id != candidate_result.protocol_id
    ):
        raise OosAlreadyObservedError(
            "an official OOS result already exists", code="OOS_ALREADY_OBSERVED"
        )
    if existing_result.result_hash != candidate_result.result_hash:
        raise OosAlreadyObservedError(
            "a protocol cannot replace its official OOS result", code="OOS_ALREADY_OBSERVED"
        )


__all__ = [
    "OosBoundarySignalPolicy",
    "OosDataProvenance",
    "OosEvaluationConfig",
    "OosEvaluationIdentity",
    "OosEvaluationRange",
    "OosEvaluationResult",
    "OosEvaluationSpec",
    "OosExecutionStatus",
    "OosPerformanceSummary",
    "PortfolioInitializationMode",
    "validate_one_shot_result",
    "validate_oos_configuration",
    "validate_oos_preconditions",
    "validate_oos_range",
    "validate_oos_strategy_identity",
]
