"""Immutable research-protocol models and append-only persistence.

This module owns research governance records only.  It deliberately does not
run backtests, calculate metrics, or choose a strategy automatically.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from backend.app.backtest_models import BacktestRun
from strategies.models import StrategyVersion


class ResearchProtocolError(ValueError):
    """Raised when a research record violates the protocol contract."""


class ResearchPersistenceError(RuntimeError):
    """Raised when a research record cannot be safely persisted or restored."""


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
            provenance=payload.get("provenance", {}),
            status=payload.get("status", ProtocolStatus.DRAFT),
        )


@dataclass(frozen=True)
class CandidateSet:
    candidate_set_id: str
    protocol_id: str
    strategy_version_ids: tuple[str, ...]
    created_at: datetime
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
        if not isinstance(self.created_at, datetime):
            raise ResearchProtocolError("created_at must be a datetime")
        if self.status not in CandidateSetStatus.values():
            raise ResearchProtocolError("candidate set status is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_set_id": self.candidate_set_id,
            "protocol_id": self.protocol_id,
            "strategy_version_ids": list(self.strategy_version_ids),
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }


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

    SCHEMA_VERSION = 1

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
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not initialize research database") from exc
        finally:
            connection.close()

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
        if status == ProtocolStatus.SELECTION_RECORDED and self._selection_count(protocol_id) != 1:
            raise ResearchProtocolError(
                "selection transition requires exactly one selection decision"
            )
        if status == ProtocolStatus.OOS_EVALUATED and not self._observed_oos(protocol_id):
            raise ResearchProtocolError("OOS evaluation transition requires an observed OOS record")
        updated = protocol.with_status(status)
        connection = self._connect()
        try:
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
            return updated
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not persist protocol transition") from exc
        finally:
            connection.close()

    def create_candidate_set(self, candidate_set: CandidateSet) -> CandidateSet:
        if not isinstance(candidate_set, CandidateSet):
            raise ResearchPersistenceError("candidate set is invalid")
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
        if self._selection_count(decision.protocol_id):
            raise ResearchProtocolError("research protocol already has a selection decision")
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
        connection = self._connect()
        try:
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
            return decision
        except sqlite3.IntegrityError as exc:
            raise ResearchPersistenceError("selection decision already exists") from exc
        except sqlite3.Error as exc:
            raise ResearchPersistenceError("could not persist selection decision") from exc
        finally:
            connection.close()

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
        if self._observed_oos(evaluation.protocol_id):
            raise ResearchProtocolError("OOS has already been observed for this protocol")
        self._require_protocol_status(evaluation.protocol_id, ProtocolStatus.SELECTION_RECORDED)
        protocol = self.get_protocol(evaluation.protocol_id)
        if protocol is None:
            raise ResearchProtocolError("research protocol was not found")
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
        if self.list_oos_evaluations(evaluation.protocol_id):
            raise ResearchProtocolError("research protocol already has an OOS evaluation record")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    evaluation.protocol_id,
                    evaluation.created_at.isoformat(),
                    _dump(evaluation.to_dict()),
                ),
            )
            return evaluation
        except sqlite3.IntegrityError as exc:
            raise ResearchPersistenceError("OOS evaluation already exists") from exc
        except sqlite3.Error as exc:
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
                if (
                    candidate_set := self.get_candidate_set(str(row["candidate_set_id"]))
                )
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
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


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
    "ResearchProtocol",
    "ResearchProtocolError",
    "ResearchProtocolRepository",
    "SelectionDecision",
    "StrategyFreezeRecord",
]
