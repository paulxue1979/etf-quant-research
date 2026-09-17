"""Immutable research-protocol models and append-only persistence.

This module owns research governance records only.  It deliberately does not
run backtests, calculate metrics, or choose a strategy automatically.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from backend.app.backtest_models import BacktestRun
from backtest.models import ContributionSchedule, ExecutionRule, RebalanceFrequency
from data.models import PriceField
from research.materialization import derived_strategy_version_hash, derived_strategy_version_id
from strategies.models import StrategyVersion


class ResearchProtocolError(ValueError):
    """Raised when a research record violates the protocol contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


class ResearchPersistenceError(RuntimeError):
    """Raised when a research record cannot be safely persisted or restored."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


class ProtocolStatus:
    DRAFT = "draft"
    FROZEN = "frozen"
    IS_EVALUATED = "is_evaluated"
    SELECTION_RECORDED = "selection_recorded"
    OOS_EVALUATED = "oos_evaluated"
    CLOSED = "closed"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return (
            cls.DRAFT,
            cls.FROZEN,
            cls.IS_EVALUATED,
            cls.SELECTION_RECORDED,
            cls.OOS_EVALUATED,
            cls.CLOSED,
        )


class CandidateSetStatus:
    OPEN = "open"
    LOCKED = "locked"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return (cls.OPEN, cls.LOCKED)


class EvaluationSplit:
    IS = "is"
    OOS = "oos"


class OOSObservationStatus:
    PLANNED = "planned"
    OBSERVED = "observed"
    SEALED = "sealed"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return (cls.PLANNED, cls.OBSERVED, cls.SEALED)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchProtocolError(f"{label} must be a non-empty string")
    return value.strip()


def _require_date(value: object, label: str) -> date:
    if not isinstance(value, date):
        raise ResearchProtocolError(f"{label} must be a date")
    return value


def _text_tuple(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ResearchProtocolError(f"{label} must be a sequence")
    result = tuple(_require_text(item, f"{label} item") for item in value)
    if len(set(result)) != len(result):
        raise ResearchProtocolError(f"{label} must not contain duplicates")
    return result


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchProtocolError(f"{label} must be an object")
    return MappingProxyType(dict(value))


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchProtocolError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise ResearchProtocolError(f"{label} must be a {qualifier} number")
    return numeric


def _canonical_json(payload: Mapping[str, Any] | object) -> str:
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True, eq=False)
class ResearchEvaluationConfig:
    """Frozen projection of result-affecting BacktestConfig snapshot fields."""

    price_field_used: str
    initial_capital: float
    commission: Mapping[str, float]
    slippage: float
    execution_rule: str
    fractional_shares: bool
    rebalance_policy: Mapping[str, Any]
    engine_version: str
    contribution_schedule: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        try:
            PriceField(self.price_field_used)
        except (TypeError, ValueError) as exc:
            raise ResearchProtocolError("price_field_used is invalid") from exc
        object.__setattr__(
            self,
            "initial_capital",
            _finite_number(self.initial_capital, "initial_capital", positive=True),
        )
        if not isinstance(self.commission, Mapping) or set(self.commission) != {
            "rate",
            "per_order",
        }:
            raise ResearchProtocolError("commission must contain rate and per_order")
        commission = {
            "rate": _finite_number(self.commission["rate"], "commission rate"),
            "per_order": _finite_number(self.commission["per_order"], "commission per_order"),
        }
        if commission["rate"] < 0 or commission["per_order"] < 0:
            raise ResearchProtocolError("commission values must be non-negative")
        object.__setattr__(self, "commission", MappingProxyType(commission))
        slippage = _finite_number(self.slippage, "slippage")
        if not 0 <= slippage < 1:
            raise ResearchProtocolError("slippage must be in [0, 1)")
        object.__setattr__(self, "slippage", slippage)
        try:
            ExecutionRule(self.execution_rule)
        except (TypeError, ValueError) as exc:
            raise ResearchProtocolError("execution_rule is invalid") from exc
        if not isinstance(self.fractional_shares, bool):
            raise ResearchProtocolError("fractional_shares must be a boolean")
        if not isinstance(self.rebalance_policy, Mapping) or set(self.rebalance_policy) != {
            "frequency",
            "threshold",
        }:
            raise ResearchProtocolError("rebalance_policy must contain frequency and threshold")
        try:
            frequency = RebalanceFrequency(self.rebalance_policy["frequency"]).value
        except (TypeError, ValueError) as exc:
            raise ResearchProtocolError("rebalance frequency is invalid") from exc
        threshold = self.rebalance_policy["threshold"]
        if threshold is not None:
            threshold = _finite_number(threshold, "rebalance threshold")
            if threshold < 0:
                raise ResearchProtocolError("rebalance threshold must be non-negative")
        object.__setattr__(
            self,
            "rebalance_policy",
            MappingProxyType({"frequency": frequency, "threshold": threshold}),
        )
        object.__setattr__(
            self, "engine_version", _require_text(self.engine_version, "engine_version")
        )
        schedule = self.contribution_schedule
        if schedule is not None:
            try:
                normalized_schedule = ContributionSchedule.from_dict(schedule).to_dict()
            except (TypeError, ValueError) as exc:
                raise ResearchProtocolError("contribution_schedule is invalid") from exc
            object.__setattr__(self, "contribution_schedule", MappingProxyType(normalized_schedule))

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_field_used": self.price_field_used,
            "initial_capital": self.initial_capital,
            "commission": dict(self.commission),
            "slippage": self.slippage,
            "execution_rule": self.execution_rule,
            "fractional_shares": self.fractional_shares,
            "rebalance_policy": dict(self.rebalance_policy),
            "engine_version": self.engine_version,
            "contribution_schedule": (
                dict(self.contribution_schedule) if self.contribution_schedule is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ResearchEvaluationConfig:
        if not isinstance(payload, Mapping):
            raise ResearchProtocolError("evaluation configuration must be an object")
        return cls(
            price_field_used=str(payload.get("price_field_used", "")),
            initial_capital=payload.get("initial_capital"),
            commission=payload.get("commission", {}),
            slippage=payload.get("slippage"),
            execution_rule=str(payload.get("execution_rule", "")),
            fractional_shares=payload.get("fractional_shares"),
            rebalance_policy=payload.get("rebalance_policy", {}),
            engine_version=str(payload.get("engine_version", "")),
            contribution_schedule=payload.get("contribution_schedule"),
        )

    @classmethod
    def from_backtest_run(cls, run: BacktestRun) -> ResearchEvaluationConfig:
        snapshot = run.backtest_result.configuration_snapshot
        if not isinstance(snapshot, Mapping):
            raise ResearchProtocolError("backtest configuration snapshot is invalid")
        config = cls.from_dict(snapshot)
        if config.engine_version != run.backtest_result.engine_version:
            raise ResearchProtocolError("backtest engine version does not match its snapshot")
        return config

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def mismatch_fields(self, actual: ResearchEvaluationConfig) -> tuple[str, ...]:
        expected = self.to_dict()
        observed = actual.to_dict()
        return tuple(
            field
            for field in expected
            if _canonical_json(expected[field]) != _canonical_json(observed[field])
        )


@dataclass(frozen=True)
class ResearchProtocol:
    """Immutable holdout research rules and date boundaries."""

    protocol_id: str
    protocol_version: int
    created_at: datetime
    is_start_date: date
    is_end_date: date
    oos_start_date: date
    oos_end_date: date
    split_type: str = "holdout"
    split_policy: str = "calendar_date_non_overlapping"
    timezone: str = "America/New_York"
    gap_days: int = 0
    embargo_days: int = 0
    selection_rules: tuple[str, ...] = ()
    allowed_metrics: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    strategy_freeze_required: bool = True
    data_policy: Mapping[str, Any] = MappingProxyType({})
    execution_policy: Mapping[str, Any] = MappingProxyType({})
    evaluation_policy: Mapping[str, Any] = MappingProxyType({})
    evaluation_config: ResearchEvaluationConfig | None = None
    provenance: Mapping[str, Any] = MappingProxyType({})
    status: str = ProtocolStatus.DRAFT

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol_id", _require_text(self.protocol_id, "protocol_id"))
        if (
            isinstance(self.protocol_version, bool)
            or not isinstance(self.protocol_version, int)
            or self.protocol_version <= 0
        ):
            raise ResearchProtocolError("protocol_version must be a positive integer")
        if not isinstance(self.created_at, datetime):
            raise ResearchProtocolError("created_at must be a datetime")
        dates = tuple(
            _require_date(getattr(self, field), field)
            for field in ("is_start_date", "is_end_date", "oos_start_date", "oos_end_date")
        )
        is_start, is_end, oos_start, oos_end = dates
        if is_start > is_end:
            raise ResearchProtocolError("IS start date must be on or before IS end date")
        if oos_start > oos_end:
            raise ResearchProtocolError("OOS start date must be on or before OOS end date")
        if (
            isinstance(self.gap_days, bool)
            or not isinstance(self.gap_days, int)
            or self.gap_days < 0
        ):
            raise ResearchProtocolError("gap_days must be a non-negative integer")
        if (
            isinstance(self.embargo_days, bool)
            or not isinstance(self.embargo_days, int)
            or self.embargo_days < 0
        ):
            raise ResearchProtocolError("embargo_days must be a non-negative integer")
        required_start = is_end + timedelta(days=max(self.gap_days, self.embargo_days) + 1)
        if oos_start < required_start:
            raise ResearchProtocolError(
                "IS and OOS periods overlap or violate the required separation"
            )
        if self.split_type != "holdout":
            raise ResearchProtocolError("only holdout split_type is supported")
        for label in ("split_policy", "timezone"):
            _require_text(getattr(self, label), label)
        if not isinstance(self.strategy_freeze_required, bool) or not self.strategy_freeze_required:
            raise ResearchProtocolError("strategy_freeze_required must be true")
        if self.status not in ProtocolStatus.values():
            raise ResearchProtocolError("status is invalid")
        object.__setattr__(
            self, "selection_rules", _text_tuple(self.selection_rules, "selection_rules")
        )
        object.__setattr__(
            self, "allowed_metrics", _text_tuple(self.allowed_metrics, "allowed_metrics")
        )
        object.__setattr__(
            self, "forbidden_actions", _text_tuple(self.forbidden_actions, "forbidden_actions")
        )
        for label in ("data_policy", "execution_policy", "evaluation_policy", "provenance"):
            object.__setattr__(self, label, _mapping(getattr(self, label), label))
        if self.evaluation_config is not None and not isinstance(
            self.evaluation_config, ResearchEvaluationConfig
        ):
            raise ResearchProtocolError("evaluation_config must be a ResearchEvaluationConfig")

    def with_status(self, status: str) -> ResearchProtocol:
        if status not in ProtocolStatus.values():
            raise ResearchProtocolError("status is invalid")
        allowed = {
            ProtocolStatus.DRAFT: {ProtocolStatus.FROZEN},
            ProtocolStatus.FROZEN: {ProtocolStatus.IS_EVALUATED},
            ProtocolStatus.IS_EVALUATED: {ProtocolStatus.SELECTION_RECORDED},
            ProtocolStatus.SELECTION_RECORDED: {ProtocolStatus.OOS_EVALUATED},
            ProtocolStatus.OOS_EVALUATED: {ProtocolStatus.CLOSED},
            ProtocolStatus.CLOSED: set(),
        }
        if status not in allowed[self.status]:
            raise ResearchProtocolError(f"invalid protocol transition: {self.status} -> {status}")
        return self.__class__(
            protocol_id=self.protocol_id,
            protocol_version=self.protocol_version,
            created_at=self.created_at,
            is_start_date=self.is_start_date,
            is_end_date=self.is_end_date,
            oos_start_date=self.oos_start_date,
            oos_end_date=self.oos_end_date,
            split_type=self.split_type,
            split_policy=self.split_policy,
            timezone=self.timezone,
            gap_days=self.gap_days,
            embargo_days=self.embargo_days,
            selection_rules=self.selection_rules,
            allowed_metrics=self.allowed_metrics,
            forbidden_actions=self.forbidden_actions,
            strategy_freeze_required=self.strategy_freeze_required,
            data_policy=self.data_policy,
            execution_policy=self.execution_policy,
            evaluation_policy=self.evaluation_policy,
            evaluation_config=self.evaluation_config,
            provenance=self.provenance,
            status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "created_at": self.created_at.isoformat(),
            "is_start_date": self.is_start_date.isoformat(),
            "is_end_date": self.is_end_date.isoformat(),
            "oos_start_date": self.oos_start_date.isoformat(),
            "oos_end_date": self.oos_end_date.isoformat(),
            "split_type": self.split_type,
            "split_policy": self.split_policy,
            "timezone": self.timezone,
            "gap_days": self.gap_days,
            "embargo_days": self.embargo_days,
            "selection_rules": list(self.selection_rules),
            "allowed_metrics": list(self.allowed_metrics),
            "forbidden_actions": list(self.forbidden_actions),
            "strategy_freeze_required": self.strategy_freeze_required,
            "data_policy": dict(self.data_policy),
            "execution_policy": dict(self.execution_policy),
            "evaluation_policy": dict(self.evaluation_policy),
            "evaluation_config": (
                self.evaluation_config.to_dict() if self.evaluation_config is not None else None
            ),
            "provenance": dict(self.provenance),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ResearchProtocol:
        if not isinstance(payload, Mapping):
            raise ResearchProtocolError("research protocol must be an object")
        return cls(
            protocol_id=payload.get("protocol_id", ""),
            protocol_version=payload.get("protocol_version"),
            created_at=datetime.fromisoformat(str(payload.get("created_at", ""))),
            is_start_date=date.fromisoformat(str(payload.get("is_start_date", ""))),
            is_end_date=date.fromisoformat(str(payload.get("is_end_date", ""))),
            oos_start_date=date.fromisoformat(str(payload.get("oos_start_date", ""))),
            oos_end_date=date.fromisoformat(str(payload.get("oos_end_date", ""))),
            split_type=payload.get("split_type", ""),
            split_policy=payload.get("split_policy", ""),
            timezone=payload.get("timezone", ""),
            gap_days=payload.get("gap_days", 0),
            embargo_days=payload.get("embargo_days", 0),
            selection_rules=payload.get("selection_rules", ()),
            allowed_metrics=payload.get("allowed_metrics", ()),
            forbidden_actions=payload.get("forbidden_actions", ()),
            strategy_freeze_required=payload.get("strategy_freeze_required", False),
            data_policy=payload.get("data_policy", {}),
            execution_policy=payload.get("execution_policy", {}),
            evaluation_policy=payload.get("evaluation_policy", {}),
            evaluation_config=(
                ResearchEvaluationConfig.from_dict(payload["evaluation_config"])
                if payload.get("evaluation_config") is not None
                else None
            ),
            provenance=payload.get("provenance", {}),
            status=payload.get("status", ProtocolStatus.DRAFT),
        )


@dataclass(frozen=True)
class CandidateSet:
    candidate_set_id: str
    protocol_id: str
    strategy_version_ids: tuple[str, ...]
    created_at: datetime
    strategy_version_content_hashes: Mapping[str, str] = MappingProxyType({})
    status: str = CandidateSetStatus.OPEN

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_set_id", _require_text(self.candidate_set_id, "candidate_set_id")
        )
        object.__setattr__(self, "protocol_id", _require_text(self.protocol_id, "protocol_id"))
        ids = _text_tuple(self.strategy_version_ids, "strategy_version_ids")
        if not ids:
            raise ResearchProtocolError("candidate set must contain at least one strategy version")
        object.__setattr__(self, "strategy_version_ids", ids)
        if not isinstance(self.strategy_version_content_hashes, Mapping):
            raise ResearchProtocolError("candidate strategy hashes must be an object")
        hashes: dict[str, str] = {}
        for version_id, content_hash in self.strategy_version_content_hashes.items():
            if not isinstance(version_id, str) or version_id not in ids:
                raise ResearchProtocolError("candidate hash references an unknown strategy version")
            hashes[version_id] = _require_text(content_hash, "candidate content hash")
        if hashes and set(hashes) != set(ids):
            raise ResearchProtocolError("candidate hashes must cover every strategy version")
        object.__setattr__(
            self, "strategy_version_content_hashes", MappingProxyType(dict(sorted(hashes.items())))
        )
        if not isinstance(self.created_at, datetime):
            raise ResearchProtocolError("created_at must be a datetime")
        if self.status not in CandidateSetStatus.values():
            raise ResearchProtocolError("candidate set status is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_set_id": self.candidate_set_id,
            "protocol_id": self.protocol_id,
            "strategy_version_ids": list(self.strategy_version_ids),
            "strategy_version_content_hashes": dict(self.strategy_version_content_hashes),
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }

    @property
    def has_complete_hash_binding(self) -> bool:
        return set(self.strategy_version_content_hashes) == set(self.strategy_version_ids)


@dataclass(frozen=True)
class SelectionDecision:
    decision_id: str
    protocol_id: str
    candidate_set_id: str
    selected_strategy_version_id: str
    is_backtest_run_ids: tuple[str, ...]
    selected_metrics: Mapping[str, Any]
    rationale: str
    created_at: datetime
    data_provenance: Mapping[str, Any] = MappingProxyType({})
    source: str = "human"

    def __post_init__(self) -> None:
        for label in (
            "decision_id",
            "protocol_id",
            "candidate_set_id",
            "selected_strategy_version_id",
            "rationale",
            "source",
        ):
            object.__setattr__(self, label, _require_text(getattr(self, label), label))
        object.__setattr__(
            self,
            "is_backtest_run_ids",
            _text_tuple(self.is_backtest_run_ids, "is_backtest_run_ids"),
        )
        if not self.is_backtest_run_ids:
            raise ResearchProtocolError("selection decision requires IS backtest references")
        if not isinstance(self.created_at, datetime):
            raise ResearchProtocolError("created_at must be a datetime")
        object.__setattr__(
            self, "selected_metrics", _mapping(self.selected_metrics, "selected_metrics")
        )
        object.__setattr__(
            self, "data_provenance", _mapping(self.data_provenance, "data_provenance")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "protocol_id": self.protocol_id,
            "candidate_set_id": self.candidate_set_id,
            "selected_strategy_version_id": self.selected_strategy_version_id,
            "is_backtest_run_ids": list(self.is_backtest_run_ids),
            "selected_metrics": dict(self.selected_metrics),
            "rationale": self.rationale,
            "created_at": self.created_at.isoformat(),
            "data_provenance": dict(self.data_provenance),
            "source": self.source,
        }


@dataclass(frozen=True)
class StrategyFreezeRecord:
    freeze_id: str
    protocol_id: str
    strategy_version_id: str
    strategy_version_content_hash: str
    selection_decision_id: str
    frozen_at: datetime
    reason: str

    def __post_init__(self) -> None:
        for label in (
            "freeze_id",
            "protocol_id",
            "strategy_version_id",
            "strategy_version_content_hash",
            "selection_decision_id",
            "reason",
        ):
            object.__setattr__(self, label, _require_text(getattr(self, label), label))
        if not isinstance(self.frozen_at, datetime):
            raise ResearchProtocolError("frozen_at must be a datetime")

    def to_dict(self) -> dict[str, Any]:
        return {
            "freeze_id": self.freeze_id,
            "protocol_id": self.protocol_id,
            "strategy_version_id": self.strategy_version_id,
            "strategy_version_content_hash": self.strategy_version_content_hash,
            "selection_decision_id": self.selection_decision_id,
            "frozen_at": self.frozen_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OOSEvaluationRecord:
    evaluation_id: str
    protocol_id: str
    freeze_id: str
    strategy_version_id: str
    backtest_run_id: str
    status: str
    observed_at: datetime | None
    created_at: datetime
    provenance: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        for label in (
            "evaluation_id",
            "protocol_id",
            "freeze_id",
            "strategy_version_id",
            "backtest_run_id",
        ):
            object.__setattr__(self, label, _require_text(getattr(self, label), label))
        if self.status not in OOSObservationStatus.values():
            raise ResearchProtocolError("OOS evaluation status is invalid")
        if (
            self.status in (OOSObservationStatus.OBSERVED, OOSObservationStatus.SEALED)
            and self.observed_at is None
        ):
            raise ResearchProtocolError("observed OOS evaluations require observed_at")
        if not isinstance(self.created_at, datetime):
            raise ResearchProtocolError("created_at must be a datetime")
        if self.observed_at is not None and not isinstance(self.observed_at, datetime):
            raise ResearchProtocolError("observed_at must be a datetime")
        object.__setattr__(self, "provenance", _mapping(self.provenance, "provenance"))

    @property
    def untouched_oos(self) -> bool:
        return self.status == OOSObservationStatus.PLANNED

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "protocol_id": self.protocol_id,
            "freeze_id": self.freeze_id,
            "strategy_version_id": self.strategy_version_id,
            "backtest_run_id": self.backtest_run_id,
            "status": self.status,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "created_at": self.created_at.isoformat(),
            "provenance": dict(self.provenance),
            "untouched_oos": self.untouched_oos,
        }


class ResearchProtocolRepository:
    """SQLite append-only repository for protocol governance records."""

    SCHEMA_VERSION = 3
    _SCHEMA_KEY = "research_protocol_repository"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except OSError as exc:
            raise ResearchPersistenceError("could not prepare research database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not open research database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_protocols (
                    protocol_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_protocol_events (
                    event_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id)
                );
                CREATE TABLE IF NOT EXISTS research_candidate_sets (
                    candidate_set_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id)
                );
                CREATE TABLE IF NOT EXISTS research_selection_decisions (
                    decision_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    candidate_set_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id)
                );
                CREATE TABLE IF NOT EXISTS research_strategy_freezes (
                    freeze_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    strategy_version_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id)
                );
                CREATE TABLE IF NOT EXISTS research_oos_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_events_protocol
                    ON research_protocol_events(protocol_id, created_at);
            """)
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                (self._SCHEMA_KEY,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_metadata(schema_key, schema_version) VALUES (?, ?)",
                    (self._SCHEMA_KEY, 1),
                )
                schema_version = 1
            else:
                schema_version = int(row["schema_version"])
            self._migrate(connection, schema_version)
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not initialize research database") from exc
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection, schema_version: int) -> None:
        if schema_version > self.SCHEMA_VERSION:
            raise ResearchPersistenceError("unsupported research database schema version")
        if schema_version == self.SCHEMA_VERSION:
            return
        try:
            connection.execute("BEGIN IMMEDIATE")
            if schema_version < 2:
                duplicates = connection.execute(
                    """
                    SELECT protocol_id, COUNT(*) AS count
                    FROM research_oos_evaluations
                    GROUP BY protocol_id
                    HAVING COUNT(*) > 1
                    """
                ).fetchall()
                if duplicates:
                    protocol_ids = tuple(sorted(str(row["protocol_id"]) for row in duplicates))
                    raise ResearchPersistenceError(
                        "research schema migration blocked by duplicate OOS observations",
                        code="MIGRATION_INTEGRITY_CONFLICT",
                        details={"record_type": "oos_evaluation", "protocol_ids": protocol_ids},
                    )

            if schema_version < 3:
                duplicates = connection.execute(
                    """
                    SELECT protocol_id, COUNT(*) AS count
                    FROM research_selection_decisions
                    GROUP BY protocol_id
                    HAVING COUNT(*) > 1
                    """
                ).fetchall()
                if duplicates:
                    protocol_ids = tuple(sorted(str(row["protocol_id"]) for row in duplicates))
                    raise ResearchPersistenceError(
                        "research schema migration blocked by duplicate selection decisions",
                        code="MIGRATION_INTEGRITY_CONFLICT",
                        details={
                            "record_type": "selection_decision",
                            "protocol_ids": protocol_ids,
                        },
                    )

            if schema_version < 2:
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_research_oos_protocol "
                    "ON research_oos_evaluations(protocol_id)"
                )
            if schema_version < 3:
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_research_selection_protocol "
                    "ON research_selection_decisions(protocol_id)"
                )
            connection.execute(
                "UPDATE schema_metadata SET schema_version = ? WHERE schema_key = ?",
                (self.SCHEMA_VERSION, self._SCHEMA_KEY),
            )
            connection.execute("COMMIT")
        except ResearchPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ResearchPersistenceError("could not migrate research database") from exc

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def create_protocol(self, protocol: ResearchProtocol) -> ResearchProtocol:
        if not isinstance(protocol, ResearchProtocol):
            raise ResearchPersistenceError("research protocol is invalid")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO research_protocols VALUES (?, ?, ?)",
                (protocol.protocol_id, protocol.created_at.isoformat(), _dump(protocol.to_dict())),
            )
            return protocol
        except sqlite3.IntegrityError as exc:
            raise ResearchPersistenceError("research protocol already exists") from exc
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not persist research protocol") from exc
        finally:
            connection.close()

    def get_protocol(self, protocol_id: str) -> ResearchProtocol | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json FROM research_protocols WHERE protocol_id = ?", (protocol_id,)
            ).fetchone()
            if row is None:
                return None
            protocol = ResearchProtocol.from_dict(_load(row["payload_json"]))
            events = connection.execute(
                "SELECT event_type FROM research_protocol_events "
                "WHERE protocol_id = ? ORDER BY created_at ASC, event_id ASC",
                (protocol_id,),
            ).fetchall()
            for event in events:
                event_type = str(event["event_type"])
                if event_type in ProtocolStatus.values():
                    protocol = protocol.with_status(event_type)
            return protocol
        except (
            sqlite3.Error,
            ResearchProtocolError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, ResearchPersistenceError):
                raise
            raise ResearchPersistenceError(
                "stored research protocol failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def list_protocols(self) -> tuple[ResearchProtocol, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT protocol_id FROM research_protocols "
                "ORDER BY created_at DESC, protocol_id DESC"
            ).fetchall()
            protocols = tuple(
                protocol
                for row in rows
                if (protocol := self.get_protocol(str(row["protocol_id"]))) is not None
            )
            return protocols
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not list research protocols") from exc
        finally:
            connection.close()

    def transition_protocol(self, protocol_id: str, status: str) -> ResearchProtocol:
        protocol = self.get_protocol(protocol_id)
        if protocol is None:
            raise ResearchProtocolError("research protocol was not found")
        if status == ProtocolStatus.FROZEN and not self._has_locked_candidate_set(protocol_id):
            raise ResearchProtocolError("freezing a protocol requires a locked candidate set")
        if status == ProtocolStatus.FROZEN and protocol.evaluation_config is None:
            raise ResearchProtocolError("freezing a protocol requires an evaluation configuration")
        if status == ProtocolStatus.SELECTION_RECORDED and self._selection_count(protocol_id) != 1:
            raise ResearchProtocolError(
                "selection transition requires exactly one selection decision"
            )
        if status == ProtocolStatus.OOS_EVALUATED and not self._observed_oos(protocol_id):
            raise ResearchProtocolError("OOS evaluation transition requires an observed OOS record")
        updated = protocol.with_status(status)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if status == ProtocolStatus.FROZEN:
                connection.execute(
                    "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
                    (
                        f"event-{uuid4().hex}",
                        protocol_id,
                        "evaluation_config_frozen",
                        datetime.now(UTC).isoformat(),
                        _dump(protocol.evaluation_config.to_dict()),
                    ),
                )
            connection.execute(
                "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
                (
                    f"event-{uuid4().hex}",
                    protocol_id,
                    status,
                    datetime.now(UTC).isoformat(),
                    _dump({"status": status}),
                ),
            )
            connection.execute("COMMIT")
            return updated
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ResearchPersistenceError("could not persist protocol transition") from exc
        finally:
            connection.close()

    def create_candidate_set(self, candidate_set: CandidateSet) -> CandidateSet:
        if not isinstance(candidate_set, CandidateSet):
            raise ResearchPersistenceError("candidate set is invalid")
        if not candidate_set.has_complete_hash_binding:
            raise ResearchProtocolError(
                "candidate set requires a content hash for every strategy version",
                code="CANDIDATE_CONTENT_HASH_MISMATCH",
            )
        self._require_protocol_status(candidate_set.protocol_id, ProtocolStatus.DRAFT)
        if self.list_candidate_sets(candidate_set.protocol_id):
            raise ResearchProtocolError("research protocol already has a candidate set")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO research_candidate_sets VALUES (?, ?, ?, ?)",
                (
                    candidate_set.candidate_set_id,
                    candidate_set.protocol_id,
                    candidate_set.created_at.isoformat(),
                    _dump(candidate_set.to_dict()),
                ),
            )
            return candidate_set
        except sqlite3.IntegrityError as exc:
            raise ResearchPersistenceError("candidate set already exists") from exc
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not persist candidate set") from exc
        finally:
            connection.close()

    def get_candidate_set(self, candidate_set_id: str) -> CandidateSet | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json FROM research_candidate_sets WHERE candidate_set_id = ?",
                (candidate_set_id,),
            ).fetchone()
            if row is None:
                return None
            item = CandidateSet(**_candidate_kwargs(_load(row["payload_json"])))
            events = connection.execute(
                "SELECT payload_json FROM research_protocol_events "
                "WHERE protocol_id = ? AND event_type = ? ORDER BY created_at ASC, event_id ASC",
                (item.protocol_id, "candidate_set_locked"),
            ).fetchall()
            locked = any(
                _load(event["payload_json"]).get("candidate_set_id") == candidate_set_id
                for event in events
            )
            return (
                item
                if not locked
                else CandidateSet(
                    candidate_set_id=item.candidate_set_id,
                    protocol_id=item.protocol_id,
                    strategy_version_ids=item.strategy_version_ids,
                    created_at=item.created_at,
                    strategy_version_content_hashes=item.strategy_version_content_hashes,
                    status=CandidateSetStatus.LOCKED,
                )
            )
        except (
            sqlite3.Error,
            ResearchProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchPersistenceError("stored candidate set failed integrity checks") from exc
        finally:
            connection.close()

    def lock_candidate_set(self, candidate_set_id: str) -> CandidateSet:
        item = self.get_candidate_set(candidate_set_id)
        if item is None:
            raise ResearchProtocolError("candidate set was not found")
        if item.status != CandidateSetStatus.OPEN:
            raise ResearchProtocolError("candidate set is already locked")
        if not item.has_complete_hash_binding:
            raise ResearchProtocolError(
                "candidate set cannot be locked without content hashes",
                code="CANDIDATE_CONTENT_HASH_MISMATCH",
            )
        self._require_protocol_status(item.protocol_id, ProtocolStatus.DRAFT)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
                (
                    f"event-{uuid4().hex}",
                    item.protocol_id,
                    "candidate_set_locked",
                    datetime.now(UTC).isoformat(),
                    _dump({"candidate_set_id": item.candidate_set_id}),
                ),
            )
            return CandidateSet(
                candidate_set_id=item.candidate_set_id,
                protocol_id=item.protocol_id,
                strategy_version_ids=item.strategy_version_ids,
                created_at=item.created_at,
                strategy_version_content_hashes=item.strategy_version_content_hashes,
                status=CandidateSetStatus.LOCKED,
            )
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not lock candidate set") from exc
        finally:
            connection.close()

    def create_selection(
        self,
        decision: SelectionDecision,
        *,
        candidate_set: CandidateSet,
        runs: Sequence[BacktestRun],
        version: StrategyVersion,
    ) -> SelectionDecision:
        self._require_protocol_status(decision.protocol_id, ProtocolStatus.IS_EVALUATED)
        stored_candidate_set = self.get_candidate_set(decision.candidate_set_id)
        if (
            stored_candidate_set is None
            or stored_candidate_set.protocol_id != decision.protocol_id
            or candidate_set != stored_candidate_set
            or stored_candidate_set.status != CandidateSetStatus.LOCKED
        ):
            raise ResearchProtocolError("selection requires a locked candidate set")
        if decision.selected_strategy_version_id not in stored_candidate_set.strategy_version_ids:
            raise ResearchProtocolError("selected strategy version is not in the candidate set")
        if version.version_id != decision.selected_strategy_version_id:
            raise ResearchProtocolError("selected strategy version does not match supplied version")
        self._require_candidate_hash_match(stored_candidate_set, version)
        protocol = self.get_protocol(decision.protocol_id)
        if protocol is None:
            raise ResearchProtocolError("research protocol was not found")
        run_ids = tuple(run.backtest_run_id for run in runs)
        if set(run_ids) != set(decision.is_backtest_run_ids):
            raise ResearchProtocolError("selection backtest references do not match supplied runs")
        if not runs or any(
            run.strategy_version_id not in stored_candidate_set.strategy_version_ids
            or run.backtest_result.start_date < protocol.is_start_date
            or run.backtest_result.end_date > protocol.is_end_date
            for run in runs
        ):
            raise ResearchProtocolError("selection may reference IS backtest runs only")
        if not any(
            run.strategy_version_id == decision.selected_strategy_version_id
            and run.strategy_version_content_hash == version.content_hash
            for run in runs
        ):
            raise ResearchProtocolError(
                "selection requires complete IS evidence for the selected strategy version",
                code="SELECTION_MISSING_SELECTED_VERSION_IS_RUN",
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                "SELECT payload_json FROM research_selection_decisions WHERE protocol_id = ?",
                (decision.protocol_id,),
            ).fetchone()
            if existing_row is not None:
                try:
                    existing = _selection_from_dict(_load(existing_row["payload_json"]))
                except (ResearchProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ResearchPersistenceError(
                        "stored selection decision failed integrity checks"
                    ) from exc
                if _selection_semantic_identity(existing) == _selection_semantic_identity(decision):
                    connection.execute("COMMIT")
                    return existing
                raise ResearchProtocolError(
                    "research protocol already has a selection decision; "
                    "submitted selection conflicts",
                    code="SELECTION_CONFLICT",
                )
            connection.execute(
                "INSERT INTO research_selection_decisions VALUES (?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.protocol_id,
                    decision.candidate_set_id,
                    decision.created_at.isoformat(),
                    _dump(decision.to_dict()),
                ),
            )
            connection.execute("COMMIT")
            return decision
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            if "uq_research_selection_protocol" in str(
                exc
            ) or "research_selection_decisions.protocol_id" in str(exc):
                raise ResearchProtocolError(
                    "research protocol already has a selection decision",
                    code="SELECTION_ALREADY_EXISTS",
                ) from exc
            if "research_selection_decisions.decision_id" in str(exc):
                raise ResearchPersistenceError(
                    "selection decision already exists",
                    code="SELECTION_ALREADY_EXISTS",
                ) from exc
            raise ResearchPersistenceError("selection decision already exists") from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ResearchPersistenceError("could not persist selection decision") from exc
        except (ResearchProtocolError, ResearchPersistenceError):
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def persist_selection_and_freeze_atomic(
        self,
        decision: SelectionDecision,
        freeze: StrategyFreezeRecord,
        *,
        candidate_set: CandidateSet,
        version: StrategyVersion,
        run: BacktestRun,
        provenance: Mapping[str, Any],
    ) -> tuple[SelectionDecision, StrategyFreezeRecord]:
        """Persist a controlled experiment handoff in one SQLite transaction.

        This path is intentionally separate from the public PHASE 7 creation
        methods.  A handoff references an already-persisted derived strategy;
        the PHASE 7 candidate set remains bound to the experiment's base
        strategy version.  Selection, lifecycle event, and freeze must commit
        together or none of them may become visible.
        """
        if not isinstance(decision, SelectionDecision):
            raise ResearchProtocolError("selection decision is invalid")
        if not isinstance(freeze, StrategyFreezeRecord):
            raise ResearchProtocolError("strategy freeze is invalid")
        if not isinstance(candidate_set, CandidateSet):
            raise ResearchProtocolError("candidate set is invalid")
        if not isinstance(version, StrategyVersion):
            raise ResearchProtocolError("strategy version is invalid")
        if not isinstance(run, BacktestRun):
            raise ResearchProtocolError("backtest run is invalid")
        if not isinstance(provenance, Mapping):
            raise ResearchProtocolError("handoff provenance is invalid")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            protocol = self._get_protocol_in_connection(connection, decision.protocol_id)
            if protocol is None:
                raise ResearchProtocolError(
                    "research protocol was not found", code="PROTOCOL_NOT_FOUND"
                )
            if decision.protocol_id != freeze.protocol_id:
                raise ResearchProtocolError("selection and freeze protocol identities differ")
            if decision.protocol_id != candidate_set.protocol_id:
                raise ResearchProtocolError("candidate set does not belong to the protocol")
            if candidate_set.status != CandidateSetStatus.LOCKED:
                raise ResearchProtocolError(
                    "selection handoff requires a locked candidate set",
                    code="PROTOCOL_INVALID_STATE",
                )
            stored_candidate_set = self._get_candidate_set_in_connection(
                connection, candidate_set.candidate_set_id
            )
            if stored_candidate_set is None:
                raise ResearchProtocolError(
                    "candidate set was not found",
                    code="CANDIDATE_SET_NOT_FOUND",
                )
            if stored_candidate_set != candidate_set:
                raise ResearchProtocolError(
                    "candidate set does not match persisted protocol state",
                    code="CANDIDATE_SET_HASH_MISMATCH",
                )
            if decision.candidate_set_id != candidate_set.candidate_set_id:
                raise ResearchProtocolError("selection candidate set does not match handoff")
            if decision.selected_strategy_version_id != version.version_id:
                raise ResearchProtocolError("selected strategy version does not match handoff")
            if freeze.strategy_version_id != version.version_id:
                raise ResearchProtocolError("frozen strategy version does not match handoff")
            if freeze.selection_decision_id != decision.decision_id:
                raise ResearchProtocolError("freeze does not reference the handoff selection")
            if freeze.strategy_version_content_hash != version.content_hash:
                raise ResearchProtocolError(
                    "strategy version content hash does not match freeze",
                    code="DERIVED_STRATEGY_HASH_MISMATCH",
                )
            if decision.is_backtest_run_ids != (run.backtest_run_id,):
                raise ResearchProtocolError(
                    "handoff must reference exactly its IS backtest run",
                    code="HANDOFF_INTEGRITY_ERROR",
                )
            if run.strategy_version_id != version.version_id:
                raise ResearchProtocolError("IS backtest strategy version does not match handoff")
            if run.strategy_version_content_hash != version.content_hash:
                raise ResearchProtocolError(
                    "IS backtest strategy hash does not match handoff",
                    code="DERIVED_STRATEGY_HASH_MISMATCH",
                )
            if (
                run.backtest_result.start_date != protocol.is_start_date
                or run.backtest_result.end_date != protocol.is_end_date
            ):
                raise ResearchProtocolError(
                    "handoff may reference IS backtest runs only",
                    code="HANDOFF_INTEGRITY_ERROR",
                )
            provenance_payload = _canonical_json(dict(provenance))

            existing_row = connection.execute(
                "SELECT payload_json FROM research_selection_decisions WHERE protocol_id = ?",
                (decision.protocol_id,),
            ).fetchone()
            existing_freeze_row = connection.execute(
                "SELECT payload_json FROM research_strategy_freezes WHERE protocol_id = ?",
                (decision.protocol_id,),
            ).fetchone()
            existing_event = self._find_handoff_event(connection, decision.protocol_id)

            if existing_row is not None:
                existing = _selection_from_dict(_load(existing_row["payload_json"]))
                if existing != decision:
                    raise ResearchProtocolError(
                        "research protocol already has a different selection",
                        code="PROTOCOL_SELECTION_CONFLICT",
                    )
                if existing_freeze_row is None or existing_event is None:
                    raise ResearchPersistenceError(
                        "handoff selection is missing its freeze or lifecycle event",
                        code="HANDOFF_INTEGRITY_ERROR",
                    )
                stored_freeze = _freeze_from_dict(_load(existing_freeze_row["payload_json"]))
                if stored_freeze != freeze or protocol.status != ProtocolStatus.SELECTION_RECORDED:
                    raise ResearchPersistenceError(
                        "stored handoff is not internally consistent",
                        code="HANDOFF_INTEGRITY_ERROR",
                    )
                try:
                    stored_provenance = _canonical_json(_load(existing_event["payload_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ResearchPersistenceError(
                        "stored handoff provenance is invalid",
                        code="HANDOFF_INTEGRITY_ERROR",
                    ) from exc
                if stored_provenance != provenance_payload:
                    raise ResearchPersistenceError(
                        "stored handoff provenance does not match",
                        code="HANDOFF_INTEGRITY_ERROR",
                    )
                connection.execute("COMMIT")
                return existing, stored_freeze

            if protocol.status != ProtocolStatus.IS_EVALUATED:
                raise ResearchProtocolError(
                    "research protocol must be is_evaluated before handoff",
                    code="PROTOCOL_INVALID_STATE",
                )
            self._validate_handoff_strategy_binding(candidate_set, version)
            self._insert_handoff_selection(connection, decision)
            self._insert_handoff_event(
                connection,
                decision,
                provenance_payload,
                selection_hash=_handoff_selection_hash(provenance),
            )
            self._insert_handoff_freeze(connection, freeze)
            connection.execute("COMMIT")
            return decision, freeze
        except (ResearchProtocolError, ResearchPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            if "research_selection_decisions.protocol_id" in str(exc):
                raise ResearchProtocolError(
                    "research protocol already has a selection",
                    code="PROTOCOL_SELECTION_CONFLICT",
                ) from exc
            raise ResearchPersistenceError(
                "could not persist selection handoff atomically",
                code="HANDOFF_INTEGRITY_ERROR",
            ) from exc
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise ResearchPersistenceError(
                "could not persist selection handoff atomically",
                code="HANDOFF_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _validate_handoff_strategy_binding(
        candidate_set: CandidateSet, version: StrategyVersion
    ) -> None:
        provenance = version.materialization_provenance
        if provenance is None:
            raise ResearchProtocolError(
                "handoff requires an immutable derived strategy version",
                code="DERIVED_STRATEGY_NOT_FOUND",
            )
        expected_hash = candidate_set.strategy_version_content_hashes.get(
            provenance.base_strategy_version_id
        )
        if (
            provenance.base_strategy_version_id not in candidate_set.strategy_version_ids
            or expected_hash != provenance.base_strategy_version_hash
        ):
            raise ResearchProtocolError(
                "candidate set does not contain the experiment base strategy",
                code="PROTOCOL_MISMATCH",
            )
        semantic_hash = derived_strategy_version_hash(
            version.configuration,
            base_strategy_version_id=provenance.base_strategy_version_id,
            base_strategy_version_hash=provenance.base_strategy_version_hash,
            parameter_set_hash=provenance.parameter_set_hash,
            binding_hash=provenance.binding_hash,
            materialization_spec_hash=provenance.materialization_spec_hash,
        )
        if provenance.derived_strategy_version_hash != semantic_hash:
            raise ResearchProtocolError(
                "derived strategy provenance hash is invalid",
                code="DERIVED_STRATEGY_HASH_MISMATCH",
            )
        if version.version_id != derived_strategy_version_id(
            provenance.base_strategy_version_id, semantic_hash
        ):
            raise ResearchProtocolError(
                "derived strategy version identity is invalid",
                code="DERIVED_STRATEGY_HASH_MISMATCH",
            )
        canonical = StrategyVersion(
            strategy_id=version.strategy_id,
            version_id=version.version_id,
            version_number=version.version_number,
            created_at=version.created_at,
            configuration=version.configuration,
            status=version.status,
            materialization_provenance=provenance,
        )
        if version.content_hash != canonical.content_hash:
            raise ResearchProtocolError(
                "derived strategy content hash is invalid",
                code="DERIVED_STRATEGY_HASH_MISMATCH",
            )

    @staticmethod
    def _insert_handoff_selection(
        connection: sqlite3.Connection, decision: SelectionDecision
    ) -> None:
        connection.execute(
            "INSERT INTO research_selection_decisions VALUES (?, ?, ?, ?, ?)",
            (
                decision.decision_id,
                decision.protocol_id,
                decision.candidate_set_id,
                decision.created_at.isoformat(),
                _dump(decision.to_dict()),
            ),
        )

    @staticmethod
    def _insert_handoff_event(
        connection: sqlite3.Connection,
        decision: SelectionDecision,
        provenance_payload: str,
        *,
        selection_hash: str,
    ) -> None:
        connection.execute(
            "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
            (
                f"event-handoff-{selection_hash[:32]}",
                decision.protocol_id,
                ProtocolStatus.SELECTION_RECORDED,
                datetime.now(UTC).isoformat(),
                provenance_payload,
            ),
        )

    @staticmethod
    def _insert_handoff_freeze(
        connection: sqlite3.Connection, freeze: StrategyFreezeRecord
    ) -> None:
        connection.execute(
            "INSERT INTO research_strategy_freezes VALUES (?, ?, ?, ?, ?)",
            (
                freeze.freeze_id,
                freeze.protocol_id,
                freeze.strategy_version_id,
                freeze.frozen_at.isoformat(),
                _dump(freeze.to_dict()),
            ),
        )

    @staticmethod
    def _find_handoff_event(connection: sqlite3.Connection, protocol_id: str) -> sqlite3.Row | None:
        rows = connection.execute(
            "SELECT * FROM research_protocol_events "
            "WHERE protocol_id = ? AND event_type = ? "
            "ORDER BY created_at ASC, event_id ASC",
            (protocol_id, ProtocolStatus.SELECTION_RECORDED),
        ).fetchall()
        for row in rows:
            try:
                payload = _load(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping) and payload.get("handoff") is True:
                return row
        return None

    def _get_protocol_in_connection(
        self, connection: sqlite3.Connection, protocol_id: str
    ) -> ResearchProtocol | None:
        row = connection.execute(
            "SELECT payload_json FROM research_protocols WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        if row is None:
            return None
        protocol = ResearchProtocol.from_dict(_load(row["payload_json"]))
        events = connection.execute(
            "SELECT event_type FROM research_protocol_events "
            "WHERE protocol_id = ? ORDER BY created_at ASC, event_id ASC",
            (protocol_id,),
        ).fetchall()
        for event in events:
            event_type = str(event["event_type"])
            if event_type in ProtocolStatus.values():
                protocol = protocol.with_status(event_type)
        return protocol

    def _get_candidate_set_in_connection(
        self, connection: sqlite3.Connection, candidate_set_id: str
    ) -> CandidateSet | None:
        row = connection.execute(
            "SELECT payload_json FROM research_candidate_sets WHERE candidate_set_id = ?",
            (candidate_set_id,),
        ).fetchone()
        if row is None:
            return None
        item = CandidateSet(**_candidate_kwargs(_load(row["payload_json"])))
        events = connection.execute(
            "SELECT payload_json FROM research_protocol_events "
            "WHERE protocol_id = ? AND event_type = ? ORDER BY created_at ASC, event_id ASC",
            (item.protocol_id, "candidate_set_locked"),
        ).fetchall()
        locked = any(
            _load(event["payload_json"]).get("candidate_set_id") == candidate_set_id
            for event in events
        )
        if not locked:
            return item
        return CandidateSet(
            candidate_set_id=item.candidate_set_id,
            protocol_id=item.protocol_id,
            strategy_version_ids=item.strategy_version_ids,
            created_at=item.created_at,
            strategy_version_content_hashes=item.strategy_version_content_hashes,
            status=CandidateSetStatus.LOCKED,
        )

    def get_selection(self, decision_id: str) -> SelectionDecision | None:
        return self._get_record(
            table="research_selection_decisions",
            identifier="decision_id",
            value=decision_id,
            builder=_selection_from_dict,
            label="selection decision",
        )

    def create_freeze(
        self, freeze: StrategyFreezeRecord, *, decision: SelectionDecision, version: StrategyVersion
    ) -> StrategyFreezeRecord:
        self._require_protocol_status(freeze.protocol_id, ProtocolStatus.SELECTION_RECORDED)
        stored_decision = self.get_selection(freeze.selection_decision_id)
        if (
            stored_decision is None
            or stored_decision.protocol_id != freeze.protocol_id
            or decision != stored_decision
            or freeze.strategy_version_id != stored_decision.selected_strategy_version_id
        ):
            raise ResearchProtocolError("freeze must match the recorded selection decision")
        if version.version_id != freeze.strategy_version_id:
            raise ResearchProtocolError("strategy version does not match freeze")
        candidate_set = self.get_candidate_set(stored_decision.candidate_set_id)
        if candidate_set is None:
            raise ResearchProtocolError("recorded selection candidate set was not found")
        self._require_candidate_hash_match(candidate_set, version)
        if freeze.strategy_version_content_hash != version.content_hash:
            raise ResearchProtocolError("strategy version content hash does not match freeze")
        if self.list_freezes(freeze.protocol_id):
            raise ResearchProtocolError("research protocol already has a strategy freeze")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO research_strategy_freezes VALUES (?, ?, ?, ?, ?)",
                (
                    freeze.freeze_id,
                    freeze.protocol_id,
                    freeze.strategy_version_id,
                    freeze.frozen_at.isoformat(),
                    _dump(freeze.to_dict()),
                ),
            )
            return freeze
        except sqlite3.IntegrityError as exc:
            raise ResearchPersistenceError("strategy freeze already exists") from exc
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not persist strategy freeze") from exc
        finally:
            connection.close()

    def get_freeze(self, freeze_id: str) -> StrategyFreezeRecord | None:
        return self._get_record(
            table="research_strategy_freezes",
            identifier="freeze_id",
            value=freeze_id,
            builder=_freeze_from_dict,
            label="strategy freeze",
        )

    def create_oos_evaluation(
        self, evaluation: OOSEvaluationRecord, *, freeze: StrategyFreezeRecord, run: BacktestRun
    ) -> OOSEvaluationRecord:
        protocol = self.get_protocol(evaluation.protocol_id)
        if protocol is None:
            raise ResearchProtocolError("research protocol was not found")
        # A duplicate request must keep its stable integrity error even after the
        # caller advances the protocol to OOS_EVALUATED.  This also makes retry
        # behavior deterministic without weakening the state-machine check for
        # protocols that have not recorded an observation.
        if self._observed_oos(evaluation.protocol_id):
            raise ResearchProtocolError(
                "OOS has already been observed for this protocol",
                code="OOS_OBSERVATION_ALREADY_RECORDED",
            )
        self._require_protocol_status(evaluation.protocol_id, ProtocolStatus.SELECTION_RECORDED)
        stored_freeze = self.get_freeze(evaluation.freeze_id)
        if (
            stored_freeze is None
            or stored_freeze.protocol_id != evaluation.protocol_id
            or freeze != stored_freeze
            or evaluation.strategy_version_id != stored_freeze.strategy_version_id
        ):
            raise ResearchProtocolError(
                "OOS evaluation must reference the frozen selected strategy"
            )
        result = run.backtest_result
        if evaluation.backtest_run_id != run.backtest_run_id:
            raise ResearchProtocolError("OOS evaluation backtest run does not match supplied run")
        if run.strategy_version_id != stored_freeze.strategy_version_id:
            raise ResearchProtocolError(
                "OOS run strategy version does not match the frozen strategy"
            )
        if run.strategy_version_content_hash != stored_freeze.strategy_version_content_hash:
            raise ResearchProtocolError("OOS run strategy hash does not match the frozen strategy")
        if result.start_date != protocol.oos_start_date or result.end_date != protocol.oos_end_date:
            raise ResearchProtocolError("OOS backtest must exactly match the protocol OOS period")
        expected_config = self.get_frozen_evaluation_config(evaluation.protocol_id)
        if expected_config is None:
            raise ResearchProtocolError(
                "OOS evaluation requires a frozen evaluation configuration",
                code="OOS_CONFIGURATION_MISMATCH",
            )
        try:
            actual_config = ResearchEvaluationConfig.from_backtest_run(run)
        except ResearchProtocolError as exc:
            raise ResearchProtocolError(
                "OOS backtest configuration is invalid or incomplete",
                code="OOS_CONFIGURATION_MISMATCH",
            ) from exc
        mismatch_fields = expected_config.mismatch_fields(actual_config)
        if mismatch_fields:
            raise ResearchProtocolError(
                "OOS backtest configuration does not match the frozen protocol contract",
                code="OOS_CONFIGURATION_MISMATCH",
                details={"fields": list(mismatch_fields)},
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    evaluation.protocol_id,
                    evaluation.created_at.isoformat(),
                    _dump(evaluation.to_dict()),
                ),
            )
            connection.execute("COMMIT")
            return evaluation
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise ResearchProtocolError(
                "OOS has already been observed for this protocol",
                code="OOS_OBSERVATION_ALREADY_RECORDED",
            ) from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ResearchPersistenceError("could not persist OOS evaluation") from exc
        finally:
            connection.close()

    def list_oos_evaluations(self, protocol_id: str) -> tuple[OOSEvaluationRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT payload_json FROM research_oos_evaluations "
                "WHERE protocol_id = ? ORDER BY created_at ASC, evaluation_id ASC",
                (protocol_id,),
            ).fetchall()
            return tuple(_oos_from_dict(_load(row["payload_json"])) for row in rows)
        except (
            sqlite3.Error,
            ResearchProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchPersistenceError("stored OOS evaluation failed integrity checks") from exc
        finally:
            connection.close()

    def list_candidate_sets(self, protocol_id: str) -> tuple[CandidateSet, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT candidate_set_id FROM research_candidate_sets "
                "WHERE protocol_id = ? ORDER BY created_at ASC, candidate_set_id ASC",
                (protocol_id,),
            ).fetchall()
            return tuple(
                candidate_set
                for row in rows
                if (candidate_set := self.get_candidate_set(str(row["candidate_set_id"])))
                is not None
            )
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not list candidate sets") from exc
        finally:
            connection.close()

    def list_selections(self, protocol_id: str) -> tuple[SelectionDecision, ...]:
        return self._list_records(
            table="research_selection_decisions",
            identifier="decision_id",
            protocol_id=protocol_id,
            builder=_selection_from_dict,
            label="selection decision",
        )

    def list_freezes(self, protocol_id: str) -> tuple[StrategyFreezeRecord, ...]:
        return self._list_records(
            table="research_strategy_freezes",
            identifier="freeze_id",
            protocol_id=protocol_id,
            builder=_freeze_from_dict,
            label="strategy freeze",
        )

    def _observed_oos(self, protocol_id: str) -> bool:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT payload_json FROM research_oos_evaluations WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchall()
            return any(
                _load(row["payload_json"]).get("status")
                in (OOSObservationStatus.OBSERVED, OOSObservationStatus.SEALED)
                for row in rows
            )
        finally:
            connection.close()

    def get_frozen_evaluation_config(self, protocol_id: str) -> ResearchEvaluationConfig | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT payload_json FROM research_protocol_events
                WHERE protocol_id = ? AND event_type = ?
                ORDER BY created_at ASC, event_id ASC
                LIMIT 1
                """,
                (protocol_id, "evaluation_config_frozen"),
            ).fetchone()
            if row is None:
                return None
            return ResearchEvaluationConfig.from_dict(_load(row["payload_json"]))
        except (
            sqlite3.Error,
            ResearchProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchPersistenceError(
                "stored frozen evaluation configuration failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def _has_locked_candidate_set(self, protocol_id: str) -> bool:
        candidate_sets = self.list_candidate_sets(protocol_id)
        return len(candidate_sets) == 1 and candidate_sets[0].status == CandidateSetStatus.LOCKED

    def _selection_count(self, protocol_id: str) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM research_selection_decisions WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            return int(row["count"])
        finally:
            connection.close()

    @staticmethod
    def _require_candidate_hash_match(
        candidate_set: CandidateSet, version: StrategyVersion
    ) -> None:
        expected = candidate_set.strategy_version_content_hashes.get(version.version_id)
        if (
            not candidate_set.has_complete_hash_binding
            or expected is None
            or expected != version.content_hash
        ):
            raise ResearchProtocolError(
                "candidate strategy content hash does not match the immutable strategy version",
                code="CANDIDATE_CONTENT_HASH_MISMATCH",
            )

    def _get_record(
        self,
        *,
        table: str,
        identifier: str,
        value: str,
        builder: Any,
        label: str,
    ) -> Any | None:
        connection = self._connect()
        try:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE {identifier} = ?", (value,)
            ).fetchone()
            if row is None:
                return None
            return builder(_load(row["payload_json"]))
        except (
            sqlite3.Error,
            ResearchProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchPersistenceError(f"stored {label} failed integrity checks") from exc
        finally:
            connection.close()

    def _list_records(
        self,
        *,
        table: str,
        identifier: str,
        protocol_id: str,
        builder: Any,
        label: str,
    ) -> tuple[Any, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT {identifier}, payload_json FROM {table} WHERE protocol_id = ? "
                f"ORDER BY created_at ASC, {identifier} ASC",
                (protocol_id,),
            ).fetchall()
            return tuple(builder(_load(row["payload_json"])) for row in rows)
        except (
            sqlite3.Error,
            ResearchProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchPersistenceError(f"stored {label} failed integrity checks") from exc
        finally:
            connection.close()

    def _require_protocol_status(self, protocol_id: str, status: str) -> ResearchProtocol:
        protocol = self.get_protocol(protocol_id)
        if protocol is None:
            raise ResearchProtocolError("research protocol was not found")
        if protocol.status != status:
            raise ResearchProtocolError(f"research protocol must be {status}")
        return protocol


def _dump(payload: Mapping[str, Any]) -> str:
    return _canonical_json(payload)


def _load(payload: object) -> Any:
    if not isinstance(payload, str):
        raise ValueError("stored research payload is invalid")
    return json.loads(
        payload, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
    )


def _candidate_kwargs(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_set_id": payload["candidate_set_id"],
        "protocol_id": payload["protocol_id"],
        "strategy_version_ids": tuple(payload["strategy_version_ids"]),
        "created_at": datetime.fromisoformat(str(payload["created_at"])),
        "strategy_version_content_hashes": payload.get("strategy_version_content_hashes", {}),
        "status": payload.get("status", CandidateSetStatus.OPEN),
    }


def _selection_from_dict(payload: Mapping[str, Any]) -> SelectionDecision:
    return SelectionDecision(
        decision_id=payload["decision_id"],
        protocol_id=payload["protocol_id"],
        candidate_set_id=payload["candidate_set_id"],
        selected_strategy_version_id=payload["selected_strategy_version_id"],
        is_backtest_run_ids=tuple(payload["is_backtest_run_ids"]),
        selected_metrics=payload["selected_metrics"],
        rationale=payload["rationale"],
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        data_provenance=payload.get("data_provenance", {}),
        source=payload.get("source", "human"),
    )


def _selection_semantic_identity(decision: SelectionDecision) -> str:
    """Return the full immutable identity used for retry/conflict detection."""
    return _canonical_json(decision.to_dict())


def _handoff_selection_hash(provenance: Mapping[str, Any]) -> str:
    value = provenance.get("selection_hash")
    if not isinstance(value, str) or len(value) != 64:
        raise ResearchProtocolError(
            "handoff provenance requires the experiment selection hash",
            code="HANDOFF_INTEGRITY_ERROR",
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise ResearchProtocolError(
            "handoff provenance requires the experiment selection hash",
            code="HANDOFF_INTEGRITY_ERROR",
        ) from exc
    return value


def _freeze_from_dict(payload: Mapping[str, Any]) -> StrategyFreezeRecord:
    return StrategyFreezeRecord(
        freeze_id=payload["freeze_id"],
        protocol_id=payload["protocol_id"],
        strategy_version_id=payload["strategy_version_id"],
        strategy_version_content_hash=payload["strategy_version_content_hash"],
        selection_decision_id=payload["selection_decision_id"],
        frozen_at=datetime.fromisoformat(str(payload["frozen_at"])),
        reason=payload["reason"],
    )


def _oos_from_dict(payload: Mapping[str, Any]) -> OOSEvaluationRecord:
    observed_at = payload.get("observed_at")
    return OOSEvaluationRecord(
        evaluation_id=payload["evaluation_id"],
        protocol_id=payload["protocol_id"],
        freeze_id=payload["freeze_id"],
        strategy_version_id=payload["strategy_version_id"],
        backtest_run_id=payload["backtest_run_id"],
        status=payload["status"],
        observed_at=datetime.fromisoformat(str(observed_at)) if observed_at else None,
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        provenance=payload.get("provenance", {}),
    )


__all__ = [
    "CandidateSet",
    "CandidateSetStatus",
    "EvaluationSplit",
    "OOSObservationStatus",
    "OOSEvaluationRecord",
    "ProtocolStatus",
    "ResearchPersistenceError",
    "ResearchEvaluationConfig",
    "ResearchProtocol",
    "ResearchProtocolError",
    "ResearchProtocolRepository",
    "SelectionDecision",
    "StrategyFreezeRecord",
]
