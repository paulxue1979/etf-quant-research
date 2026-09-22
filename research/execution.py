"""Immutable PHASE 8D-2 candidate execution records and state transitions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from research.canonical import canonical_json, sha256_hash
from research.exceptions import ExecutionStateTransitionError, InvalidCandidateExecutionError

_HASH = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_ID = re.compile(r"^execution-[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class CandidateExecutionStatus(StrEnum):
    """Lifecycle states for one persisted candidate execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _text(value: object, label: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidCandidateExecutionError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise InvalidCandidateExecutionError(f"{label} is too long")
    if any(marker in normalized.lower() for marker in ("tiingo_api_key", "api_key", "password")):
        raise InvalidCandidateExecutionError(f"{label} contains sensitive data")
    return normalized


def _hash(value: object, label: str) -> str:
    normalized = _text(value, label, max_length=64)
    if not _HASH.fullmatch(normalized):
        raise InvalidCandidateExecutionError(f"{label} must be a SHA-256 hex digest")
    return normalized


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidCandidateExecutionError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidCandidateExecutionError(f"{label} must be timezone-aware")
    return value


def _status(value: object) -> CandidateExecutionStatus:
    try:
        return (
            value
            if isinstance(value, CandidateExecutionStatus)
            else CandidateExecutionStatus(value)
        )
    except (TypeError, ValueError) as exc:
        raise InvalidCandidateExecutionError("status is invalid") from exc


def candidate_id_for(experiment_id: str, candidate_index: int, parameter_set_hash: str) -> str:
    """Derive a stable candidate reference without introducing a second candidate model."""
    return sha256_hash(
        {
            "experiment_id": _text(experiment_id, "experiment_id"),
            "candidate_index": candidate_index,
            "parameter_set_hash": _hash(parameter_set_hash, "parameter_set_hash"),
        }
    )


def execution_id_for(
    experiment_id: str,
    candidate_id: str,
    parameter_set_hash: str,
    parameter_binding_hash: str,
) -> str:
    """Derive the deterministic business identity of one candidate execution."""
    return "execution-" + sha256_hash(
        {
            "experiment_id": _text(experiment_id, "experiment_id"),
            "candidate_id": _hash(candidate_id, "candidate_id"),
            "parameter_set_hash": _hash(parameter_set_hash, "parameter_set_hash"),
            "parameter_binding_hash": _hash(parameter_binding_hash, "parameter_binding_hash"),
        }
    )


@dataclass(frozen=True)
class CandidateExecution:
    """Immutable identity plus controlled mutable lifecycle metadata."""

    execution_id: str
    experiment_id: str
    experiment_hash: str
    candidate_id: str
    candidate_index: int
    parameter_set_hash: str
    parameter_binding_hash: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    derived_strategy_version_id: str | None
    derived_strategy_version_hash: str | None
    status: CandidateExecutionStatus
    created_at: datetime
    max_retries: int = 3
    retry_count: int = 0
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_retryable: bool | None = None

    @classmethod
    def pending(
        cls,
        *,
        experiment_id: str,
        experiment_hash: str,
        candidate_id: str,
        candidate_index: int,
        parameter_set_hash: str,
        parameter_binding_hash: str,
        base_strategy_version_id: str,
        base_strategy_version_hash: str,
        derived_strategy_version_id: str | None = None,
        derived_strategy_version_hash: str | None = None,
        created_at: datetime | None = None,
        max_retries: int = 3,
    ) -> CandidateExecution:
        return cls(
            execution_id=execution_id_for(
                experiment_id, candidate_id, parameter_set_hash, parameter_binding_hash
            ),
            experiment_id=experiment_id,
            experiment_hash=experiment_hash,
            candidate_id=candidate_id,
            candidate_index=candidate_index,
            parameter_set_hash=parameter_set_hash,
            parameter_binding_hash=parameter_binding_hash,
            base_strategy_version_id=base_strategy_version_id,
            base_strategy_version_hash=base_strategy_version_hash,
            derived_strategy_version_id=derived_strategy_version_id,
            derived_strategy_version_hash=derived_strategy_version_hash,
            status=CandidateExecutionStatus.PENDING,
            created_at=created_at or datetime.now(UTC),
            max_retries=max_retries,
        )

    def __post_init__(self) -> None:
        for label in (
            "execution_id",
            "experiment_id",
            "candidate_id",
            "base_strategy_version_id",
        ):
            value = _text(getattr(self, label), label)
            if label == "candidate_id":
                value = _hash(value, label)
            elif label == "execution_id" and not _EXECUTION_ID.fullmatch(value):
                raise InvalidCandidateExecutionError(
                    "execution_id must be a deterministic execution identity"
                )
            object.__setattr__(self, label, value)
        for label in (
            "experiment_hash",
            "parameter_set_hash",
            "parameter_binding_hash",
            "base_strategy_version_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        if self.derived_strategy_version_id is None:
            if self.derived_strategy_version_hash is not None:
                raise InvalidCandidateExecutionError("derived strategy version hash requires an id")
        else:
            object.__setattr__(
                self,
                "derived_strategy_version_id",
                _text(self.derived_strategy_version_id, "derived_strategy_version_id"),
            )
            if self.derived_strategy_version_hash is None:
                raise InvalidCandidateExecutionError("derived strategy version id requires a hash")
            object.__setattr__(
                self,
                "derived_strategy_version_hash",
                _hash(self.derived_strategy_version_hash, "derived_strategy_version_hash"),
            )
        if (
            isinstance(self.candidate_index, bool)
            or not isinstance(self.candidate_index, int)
            or self.candidate_index < 0
        ):
            raise InvalidCandidateExecutionError("candidate_index must be non-negative")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
        ):
            raise InvalidCandidateExecutionError("max_retries must be non-negative")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or not 0 <= self.retry_count <= self.max_retries + 1
        ):
            raise InvalidCandidateExecutionError("retry_count is outside max_retries")
        object.__setattr__(self, "status", _status(self.status))
        object.__setattr__(self, "created_at", _datetime(self.created_at, "created_at"))
        for label in ("claimed_at", "lease_expires_at", "completed_at", "failed_at"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(self, label, _datetime(value, label))
        if self.claimed_by is not None:
            object.__setattr__(self, "claimed_by", _text(self.claimed_by, "claimed_by"))
        if self.failure_code is not None:
            code = _text(self.failure_code, "failure_code", max_length=64)
            if not _NAME.fullmatch(code):
                raise InvalidCandidateExecutionError("failure_code must be a safe identifier")
            object.__setattr__(self, "failure_code", code)
        if self.failure_message is not None:
            object.__setattr__(
                self,
                "failure_message",
                _text(self.failure_message, "failure_message", max_length=1000),
            )
        if self.failure_retryable is not None and not isinstance(self.failure_retryable, bool):
            raise InvalidCandidateExecutionError("failure_retryable must be a boolean")
        if self.status is CandidateExecutionStatus.RUNNING and not (
            self.claimed_at and self.claimed_by and self.lease_expires_at
        ):
            raise InvalidCandidateExecutionError("RUNNING execution requires an active lease")
        if self.status is CandidateExecutionStatus.RUNNING and any(
            value is not None
            for value in (
                self.completed_at,
                self.failed_at,
                self.failure_code,
                self.failure_message,
                self.failure_retryable,
            )
        ):
            raise InvalidCandidateExecutionError("RUNNING execution must not retain final metadata")
        if self.status is CandidateExecutionStatus.COMPLETED and self.completed_at is None:
            raise InvalidCandidateExecutionError("COMPLETED execution requires completed_at")
        if self.status is CandidateExecutionStatus.COMPLETED and any(
            value is not None
            for value in (
                self.failed_at,
                self.failure_code,
                self.failure_message,
                self.failure_retryable,
            )
        ):
            raise InvalidCandidateExecutionError(
                "COMPLETED execution must not retain failure metadata"
            )
        if self.status is CandidateExecutionStatus.FAILED and not (
            self.failed_at and self.failure_code and self.failure_message is not None
        ):
            raise InvalidCandidateExecutionError("FAILED execution requires failure metadata")
        if self.status is CandidateExecutionStatus.FAILED and self.completed_at is not None:
            raise InvalidCandidateExecutionError("FAILED execution must not retain completed_at")
        if self.status is CandidateExecutionStatus.PENDING and any(
            value is not None
            for value in (
                self.completed_at,
                self.failed_at,
                self.failure_code,
                self.failure_message,
                self.failure_retryable,
            )
        ):
            raise InvalidCandidateExecutionError("PENDING execution must not retain final metadata")
        if self.status is not CandidateExecutionStatus.RUNNING and any(
            value is not None for value in (self.claimed_at, self.claimed_by, self.lease_expires_at)
        ):
            raise InvalidCandidateExecutionError("non-running execution must not retain a lease")

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "candidate_index": self.candidate_index,
            "parameter_set_hash": self.parameter_set_hash,
            "parameter_binding_hash": self.parameter_binding_hash,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "derived_strategy_version_id": self.derived_strategy_version_id,
            "derived_strategy_version_hash": self.derived_strategy_version_hash,
        }

    @property
    def identity_hash(self) -> str:
        return sha256_hash(self.identity_payload)

    def claim(
        self, *, claimed_at: datetime, claimed_by: str, lease_expires_at: datetime
    ) -> CandidateExecution:
        if self.status is not CandidateExecutionStatus.PENDING:
            raise ExecutionStateTransitionError("only PENDING executions may be claimed")
        claimed_at = _datetime(claimed_at, "claimed_at")
        lease_expires_at = _datetime(lease_expires_at, "lease_expires_at")
        if lease_expires_at <= claimed_at:
            raise ExecutionStateTransitionError("lease_expires_at must be after claimed_at")
        return replace(
            self,
            status=CandidateExecutionStatus.RUNNING,
            claimed_at=claimed_at,
            claimed_by=_text(claimed_by, "claimed_by"),
            lease_expires_at=lease_expires_at,
        )

    def renew(self, *, lease_expires_at: datetime, now: datetime, actor: str) -> CandidateExecution:
        if self.status is not CandidateExecutionStatus.RUNNING:
            raise ExecutionStateTransitionError("only RUNNING executions may renew a lease")
        if self.claimed_by != _text(actor, "actor"):
            raise ExecutionStateTransitionError("only the lease owner may renew a lease")
        now = _datetime(now, "now")
        lease_expires_at = _datetime(lease_expires_at, "lease_expires_at")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise ExecutionStateTransitionError("expired lease cannot be renewed")
        if lease_expires_at <= now:
            raise ExecutionStateTransitionError("lease_expires_at must be in the future")
        return replace(self, lease_expires_at=lease_expires_at)

    def complete(self, *, completed_at: datetime, actor: str, now: datetime) -> CandidateExecution:
        self._require_live_owner(actor, now)
        return replace(
            self,
            status=CandidateExecutionStatus.COMPLETED,
            completed_at=_datetime(completed_at, "completed_at"),
            claimed_at=None,
            claimed_by=None,
            lease_expires_at=None,
        )

    def fail(
        self,
        *,
        failed_at: datetime,
        actor: str,
        now: datetime,
        failure_code: str,
        failure_message: str,
        retryable: bool,
    ) -> CandidateExecution:
        self._require_live_owner(actor, now)
        if not isinstance(retryable, bool):
            raise InvalidCandidateExecutionError("retryable must be a boolean")
        return replace(
            self,
            status=CandidateExecutionStatus.FAILED,
            retry_count=self.retry_count + 1,
            failed_at=_datetime(failed_at, "failed_at"),
            failure_code=failure_code,
            failure_message=failure_message,
            failure_retryable=retryable,
            claimed_at=None,
            claimed_by=None,
            lease_expires_at=None,
        )

    def retry(self) -> CandidateExecution:
        if self.status is not CandidateExecutionStatus.FAILED:
            raise ExecutionStateTransitionError("only FAILED executions may be retried")
        if not self.failure_retryable or self.retry_count > self.max_retries:
            raise ExecutionStateTransitionError("execution failure is not retryable")
        return replace(
            self,
            status=CandidateExecutionStatus.PENDING,
            claimed_at=None,
            claimed_by=None,
            lease_expires_at=None,
            failed_at=None,
            failure_code=None,
            failure_message=None,
            failure_retryable=None,
        )

    def recover_expired(self, *, now: datetime) -> CandidateExecution:
        if self.status is not CandidateExecutionStatus.RUNNING:
            raise ExecutionStateTransitionError("only RUNNING executions may be recovered")
        now = _datetime(now, "now")
        if self.lease_expires_at is None or self.lease_expires_at > now:
            raise ExecutionStateTransitionError("execution lease has not expired")
        return replace(
            self,
            status=CandidateExecutionStatus.PENDING,
            claimed_at=None,
            claimed_by=None,
            lease_expires_at=None,
        )

    def recover_unfinalized(self) -> CandidateExecution:
        """Return an execution-only completion to pending when no result was committed."""
        if self.status is not CandidateExecutionStatus.COMPLETED:
            raise ExecutionStateTransitionError(
                "only COMPLETED executions without results may be recovered"
            )
        return replace(self, status=CandidateExecutionStatus.PENDING, completed_at=None)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "execution_id": self.execution_id,
            "experiment_id": self.experiment_id,
            "experiment_hash": self.experiment_hash,
            "candidate_id": self.candidate_id,
            "candidate_index": self.candidate_index,
            "parameter_set_hash": self.parameter_set_hash,
            "parameter_binding_hash": self.parameter_binding_hash,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "derived_strategy_version_id": self.derived_strategy_version_id,
            "derived_strategy_version_hash": self.derived_strategy_version_hash,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at else None,
            "claimed_by": self.claimed_by,
            "lease_expires_at": self.lease_expires_at.isoformat()
            if self.lease_expires_at
            else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "failure_retryable": self.failure_retryable,
        }
        canonical_json(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> CandidateExecution:
        if not isinstance(payload, Mapping):
            raise InvalidCandidateExecutionError("execution must be an object")

        def parse_datetime(key: str) -> datetime | None:
            value = payload.get(key)
            return datetime.fromisoformat(str(value)) if value is not None else None

        try:
            return cls(
                execution_id=payload.get("execution_id", ""),
                experiment_id=payload.get("experiment_id", ""),
                experiment_hash=payload.get("experiment_hash", ""),
                candidate_id=payload.get("candidate_id", ""),
                candidate_index=payload.get("candidate_index"),
                parameter_set_hash=payload.get("parameter_set_hash", ""),
                parameter_binding_hash=payload.get("parameter_binding_hash", ""),
                base_strategy_version_id=payload.get("base_strategy_version_id", ""),
                base_strategy_version_hash=payload.get("base_strategy_version_hash", ""),
                derived_strategy_version_id=payload.get("derived_strategy_version_id"),
                derived_strategy_version_hash=payload.get("derived_strategy_version_hash"),
                status=payload.get("status", ""),
                created_at=datetime.fromisoformat(str(payload.get("created_at", ""))),
                max_retries=payload.get("max_retries", 3),
                retry_count=payload.get("retry_count", 0),
                claimed_at=parse_datetime("claimed_at"),
                claimed_by=payload.get("claimed_by"),
                lease_expires_at=parse_datetime("lease_expires_at"),
                completed_at=parse_datetime("completed_at"),
                failed_at=parse_datetime("failed_at"),
                failure_code=payload.get("failure_code"),
                failure_message=payload.get("failure_message"),
                failure_retryable=payload.get("failure_retryable"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidCandidateExecutionError("execution payload is malformed") from exc

    def _require_live_owner(self, actor: str, now: datetime) -> None:
        if self.status is not CandidateExecutionStatus.RUNNING:
            raise ExecutionStateTransitionError("execution must be RUNNING")
        if self.claimed_by != _text(actor, "actor"):
            raise ExecutionStateTransitionError("actor does not own the execution lease")
        now = _datetime(now, "now")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise ExecutionStateTransitionError("execution lease has expired")


@dataclass(frozen=True)
class ExecutionEvent:
    """One append-only execution audit event."""

    event_id: str
    execution_id: str
    experiment_id: str
    event_type: str
    occurred_at: datetime
    actor: str
    payload: Mapping[str, Any]
    from_state: CandidateExecutionStatus | None = None
    to_state: CandidateExecutionStatus | None = None
    schema_version: int = 1
    sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id"))
        object.__setattr__(self, "experiment_id", _text(self.experiment_id, "experiment_id"))
        event_type = _text(self.event_type, "event_type", max_length=64)
        if not _NAME.fullmatch(event_type):
            raise InvalidCandidateExecutionError("event_type must be a safe identifier")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "occurred_at", _datetime(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "actor", _text(self.actor, "actor"))
        if not isinstance(self.payload, Mapping):
            raise InvalidCandidateExecutionError("event payload must be an object")
        canonical_json(self.payload)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        for label in ("from_state", "to_state"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(self, label, _status(value))
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
        ):
            raise InvalidCandidateExecutionError("schema_version must be positive")
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise InvalidCandidateExecutionError("sequence must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "experiment_id": self.experiment_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "actor": self.actor,
            "payload": dict(self.payload),
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value if self.to_state else None,
            "schema_version": self.schema_version,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ExecutionEvent:
        if not isinstance(payload, Mapping):
            raise InvalidCandidateExecutionError("event must be an object")
        try:
            return cls(
                event_id=payload.get("event_id", ""),
                execution_id=payload.get("execution_id", ""),
                experiment_id=payload.get("experiment_id", ""),
                event_type=payload.get("event_type", ""),
                occurred_at=datetime.fromisoformat(str(payload.get("occurred_at", ""))),
                actor=payload.get("actor", ""),
                payload=payload.get("payload", {}),
                from_state=payload.get("from_state"),
                to_state=payload.get("to_state"),
                schema_version=payload.get("schema_version", 1),
                sequence=payload.get("sequence"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidCandidateExecutionError("event payload is malformed") from exc


__all__ = [
    "CandidateExecution",
    "CandidateExecutionStatus",
    "ExecutionEvent",
    "candidate_id_for",
    "execution_id_for",
]
