"""Controlled OOS execution lifecycle contracts for PHASE 8F-2.

This module owns execution identity, leases, safe failure metadata, and
append-only event payloads.  It intentionally does not run market data,
strategies, signals, backtests, analytics, or official OOS observation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any

from research.canonical import sha256_hash
from research.exceptions import (
    OosExecutionError,
    OosExecutionLeaseMismatchError,
    OosExecutionNotRetryableError,
    OosExecutionNotRunningError,
    OosExecutionStaleWriterError,
    OosResultIntegrityError,
)
from research.oos import OosEvaluationSpec, OosExecutionStatus

_HASH = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_ID = re.compile(r"^oos-execution-[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret|private key)",
    re.IGNORECASE,
)


def _text(value: object, label: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OosExecutionError(f"{label} must be a non-empty string", code="OOS_EXECUTION_INVALID")
    result = value.strip()
    if len(result) > max_length:
        raise OosExecutionError(f"{label} is too long", code="OOS_EXECUTION_INVALID")
    if _SENSITIVE.search(result):
        raise OosExecutionError(f"{label} contains sensitive data", code="OOS_EXECUTION_SENSITIVE")
    return result


def _hash(value: object, label: str) -> str:
    result = _text(value, label, max_length=64).lower()
    if not _HASH.fullmatch(result):
        raise OosExecutionError(
            f"{label} must be a SHA-256 hex digest", code="OOS_EXECUTION_INVALID"
        )
    return result


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OosExecutionError(f"{label} must be timezone-aware", code="OOS_EXECUTION_INVALID")
    return value.astimezone(UTC)


def _date(value: object, label: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise OosExecutionError(f"{label} must be a date", code="OOS_EXECUTION_INVALID")
    return value


def _safe_value(value: object, path: str = "payload") -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE.search(key_text):
                raise OosExecutionError(
                    f"{path} contains sensitive data", code="OOS_EXECUTION_SENSITIVE"
                )
            result[key_text] = _safe_value(item, f"{path}.{key_text}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, f"{path}[]") for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise OosExecutionError(f"{path} must contain finite numbers", code="OOS_EXECUTION_INVALID")
    if isinstance(value, str):
        if _SENSITIVE.search(value) or "BEGIN PRIVATE KEY" in value.upper():
            raise OosExecutionError(
                f"{path} contains sensitive data", code="OOS_EXECUTION_SENSITIVE"
            )
        if value.startswith(("/", "~/")) or ("\\" in value and ":" in value[:3]):
            raise OosExecutionError(
                f"{path} must not contain a filesystem path", code="OOS_EXECUTION_SENSITIVE"
            )
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    raise OosExecutionError(f"{path} is not JSON-compatible", code="OOS_EXECUTION_INVALID")


def _safe_failure_message(value: object) -> str:
    message = _text(value, "failure_message_safe", max_length=1000)
    lowered = message.lower()
    if any(
        marker in lowered for marker in ("traceback", "stack trace", "api_key", "authorization")
    ):
        raise OosExecutionError(
            "failure_message_safe contains unsafe diagnostic data",
            code="OOS_EXECUTION_UNSAFE_FAILURE",
        )
    if message.startswith(("/", "~/")) or ("\\" in message and ":" in message[:3]):
        raise OosExecutionError(
            "failure_message_safe must not contain a filesystem path",
            code="OOS_EXECUTION_UNSAFE_FAILURE",
        )
    return message


def oos_execution_id_for(spec: OosEvaluationSpec) -> str:
    """Return the deterministic lineage identity for a frozen OOS spec."""
    if not isinstance(spec, OosEvaluationSpec):
        raise OosExecutionError("OOS spec is invalid", code="OOS_EXECUTION_INVALID")
    return "oos-execution-" + sha256_hash(
        {"protocol_id": spec.identity.protocol_id, "oos_spec_hash": spec.spec_hash}
    )


@dataclass(frozen=True)
class OosExecution:
    """Immutable identity plus controlled, lease-bound lifecycle metadata."""

    execution_id: str
    protocol_id: str
    oos_spec_hash: str
    selection_decision_id: str
    strategy_freeze_id: str
    strategy_version_id: str
    strategy_content_hash: str
    oos_start: date
    oos_end: date
    warmup_start: date
    configuration_hash: str
    status: OosExecutionStatus
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    lease_token: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_code: str | None = None
    failure_message_safe: str | None = None
    failure_retryable: bool | None = None

    @classmethod
    def pending(cls, spec: OosEvaluationSpec, *, now: datetime | None = None) -> OosExecution:
        if not isinstance(spec, OosEvaluationSpec):
            raise OosExecutionError("OOS spec is invalid", code="OOS_EXECUTION_INVALID")
        timestamp = _aware_datetime(now or datetime.now(UTC), "now")
        identity = spec.identity
        return cls(
            execution_id=oos_execution_id_for(spec),
            protocol_id=identity.protocol_id,
            oos_spec_hash=spec.spec_hash,
            selection_decision_id=identity.selection_decision_id,
            strategy_freeze_id=identity.strategy_freeze_id,
            strategy_version_id=identity.strategy_version_id,
            strategy_content_hash=identity.strategy_content_hash,
            oos_start=spec.evaluation_range.oos_start,
            oos_end=spec.evaluation_range.oos_end,
            warmup_start=spec.evaluation_range.warmup_start,
            configuration_hash=spec.configuration.configuration_hash,
            status=OosExecutionStatus.PENDING,
            attempt_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id"))
        if not _EXECUTION_ID.fullmatch(self.execution_id):
            raise OosExecutionError(
                "execution_id is not deterministic", code="OOS_EXECUTION_INVALID"
            )
        for label in (
            "protocol_id",
            "selection_decision_id",
            "strategy_freeze_id",
            "strategy_version_id",
        ):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        for label in (
            "oos_spec_hash",
            "strategy_content_hash",
            "configuration_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        for label in ("oos_start", "oos_end", "warmup_start"):
            object.__setattr__(self, label, _date(getattr(self, label), label))
        if self.warmup_start > self.oos_start or self.oos_start > self.oos_end:
            raise OosExecutionError("execution date range is invalid", code="OOS_EXECUTION_INVALID")
        try:
            status = (
                self.status
                if isinstance(self.status, OosExecutionStatus)
                else OosExecutionStatus(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise OosExecutionError(
                "execution status is invalid", code="OOS_EXECUTION_INVALID"
            ) from exc
        object.__setattr__(self, "status", status)
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count < 0
        ):
            raise OosExecutionError(
                "attempt_count must be non-negative", code="OOS_EXECUTION_INVALID"
            )
        object.__setattr__(self, "created_at", _aware_datetime(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _aware_datetime(self.updated_at, "updated_at"))
        for label in ("started_at", "finished_at", "lease_expires_at"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(self, label, _aware_datetime(value, label))
        if self.lease_token is not None:
            object.__setattr__(self, "lease_token", _text(self.lease_token, "lease_token"))
        if self.lease_owner is not None:
            object.__setattr__(self, "lease_owner", _text(self.lease_owner, "lease_owner"))
        if self.failure_code is not None:
            code = _text(self.failure_code, "failure_code", max_length=64)
            if not _SAFE_NAME.fullmatch(code):
                raise OosExecutionError("failure_code is invalid", code="OOS_EXECUTION_INVALID")
            object.__setattr__(self, "failure_code", code)
        if self.failure_message_safe is not None:
            object.__setattr__(
                self, "failure_message_safe", _safe_failure_message(self.failure_message_safe)
            )
        if self.failure_retryable is not None and not isinstance(self.failure_retryable, bool):
            raise OosExecutionError(
                "failure_retryable must be boolean", code="OOS_EXECUTION_INVALID"
            )
        if status is OosExecutionStatus.RUNNING and not all(
            (self.lease_token, self.lease_owner, self.lease_expires_at, self.started_at)
        ):
            raise OosExecutionError(
                "RUNNING execution requires an active lease", code="OOS_EXECUTION_INVALID"
            )
        if status is OosExecutionStatus.PENDING and any(
            value is not None
            for value in (
                self.lease_token,
                self.lease_owner,
                self.lease_expires_at,
                self.finished_at,
                self.failure_code,
                self.failure_message_safe,
                self.failure_retryable,
            )
        ):
            raise OosExecutionError(
                "PENDING execution contains final or lease metadata", code="OOS_EXECUTION_INVALID"
            )
        if status is OosExecutionStatus.FAILED and not (
            self.finished_at
            and self.failure_code
            and self.failure_message_safe is not None
            and self.failure_retryable is not None
        ):
            raise OosExecutionError(
                "FAILED execution requires safe failure metadata", code="OOS_EXECUTION_INVALID"
            )
        if status is OosExecutionStatus.BLOCKED and not (
            self.finished_at and self.failure_code and self.failure_message_safe is not None
        ):
            raise OosExecutionError(
                "BLOCKED execution requires safe failure metadata", code="OOS_EXECUTION_INVALID"
            )
        if status is not OosExecutionStatus.RUNNING and any(
            value is not None
            for value in (self.lease_token, self.lease_owner, self.lease_expires_at)
        ):
            raise OosExecutionError(
                "non-running execution must not retain a lease", code="OOS_EXECUTION_INVALID"
            )
        if status is OosExecutionStatus.COMPLETED and self.finished_at is None:
            raise OosExecutionError(
                "COMPLETED execution requires finished_at", code="OOS_EXECUTION_INVALID"
            )

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "protocol_id": self.protocol_id,
            "oos_spec_hash": self.oos_spec_hash,
            "selection_decision_id": self.selection_decision_id,
            "strategy_freeze_id": self.strategy_freeze_id,
            "strategy_version_id": self.strategy_version_id,
            "strategy_content_hash": self.strategy_content_hash,
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "warmup_start": self.warmup_start.isoformat(),
            "configuration_hash": self.configuration_hash,
        }

    @property
    def identity_hash(self) -> str:
        return sha256_hash(self.identity_payload)

    def claim(
        self, *, owner: str, now: datetime, lease_expires_at: datetime, lease_token: str
    ) -> OosExecution:
        now = _aware_datetime(now, "now")
        expiry = _aware_datetime(lease_expires_at, "lease_expires_at")
        if expiry <= now:
            raise OosExecutionError("lease must expire in the future", code="OOS_EXECUTION_INVALID")
        if (
            self.status is OosExecutionStatus.RUNNING
            and self.lease_expires_at
            and self.lease_expires_at > now
        ):
            raise OosExecutionError(
                "execution already has an active lease", code="OOS_EXECUTION_IN_PROGRESS"
            )
        if self.status is OosExecutionStatus.BLOCKED:
            raise OosExecutionNotRetryableError("BLOCKED execution cannot be retried")
        if self.status is OosExecutionStatus.FAILED and not self.failure_retryable:
            raise OosExecutionNotRetryableError("execution failure is not retryable")
        return replace(
            self,
            status=OosExecutionStatus.RUNNING,
            attempt_count=self.attempt_count + 1,
            updated_at=now,
            started_at=self.started_at or now,
            finished_at=None,
            failure_code=None,
            failure_message_safe=None,
            failure_retryable=None,
            lease_token=_text(lease_token, "lease_token"),
            lease_owner=_text(owner, "owner"),
            lease_expires_at=expiry,
        )

    def renew(self, *, lease_token: str, now: datetime, lease_expires_at: datetime) -> OosExecution:
        self._require_live_lease(lease_token, now)
        expiry = _aware_datetime(lease_expires_at, "lease_expires_at")
        now = _aware_datetime(now, "now")
        if expiry <= now:
            raise OosExecutionError("lease must expire in the future", code="OOS_EXECUTION_INVALID")
        return replace(self, lease_expires_at=expiry, updated_at=now)

    def fail(
        self,
        *,
        lease_token: str,
        now: datetime,
        failure_code: str,
        failure_message_safe: str,
        retryable: bool,
    ) -> OosExecution:
        self._require_live_lease(lease_token, now)
        if not isinstance(retryable, bool):
            raise OosExecutionError("retryable must be boolean", code="OOS_EXECUTION_INVALID")
        now = _aware_datetime(now, "now")
        status = OosExecutionStatus.FAILED if retryable else OosExecutionStatus.BLOCKED
        return replace(
            self,
            status=status,
            updated_at=now,
            finished_at=now,
            failure_code=_text(failure_code, "failure_code", max_length=64),
            failure_message_safe=_safe_failure_message(failure_message_safe),
            failure_retryable=retryable,
            lease_token=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    def reclaim_expired(self, *, now: datetime) -> OosExecution:
        now = _aware_datetime(now, "now")
        if (
            self.status is not OosExecutionStatus.RUNNING
            or not self.lease_expires_at
            or self.lease_expires_at > now
        ):
            raise OosExecutionError(
                "execution lease has not expired", code="OOS_EXECUTION_NOT_STALE"
            )
        return replace(
            self,
            status=OosExecutionStatus.PENDING,
            updated_at=now,
            lease_token=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    def _require_live_lease(self, lease_token: str, now: datetime) -> None:
        if self.status is not OosExecutionStatus.RUNNING:
            raise OosExecutionNotRunningError("execution is not RUNNING")
        if self.lease_token != _text(lease_token, "lease_token"):
            raise OosExecutionLeaseMismatchError("lease token does not match current owner")
        if self.lease_expires_at is None or self.lease_expires_at <= _aware_datetime(now, "now"):
            raise OosExecutionStaleWriterError("execution lease has expired")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_payload,
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "lease_token": self.lease_token,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at.isoformat()
            if self.lease_expires_at
            else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "failure_code": self.failure_code,
            "failure_message_safe": self.failure_message_safe,
            "failure_retryable": self.failure_retryable,
        }

    @classmethod
    def from_dict(cls, payload: object) -> OosExecution:
        if not isinstance(payload, Mapping):
            raise OosExecutionError(
                "execution payload is invalid", code="OOS_EXECUTION_INTEGRITY_ERROR"
            )

        def parsed_datetime(key: str) -> datetime | None:
            value = payload.get(key)
            return datetime.fromisoformat(str(value)) if value is not None else None

        try:
            return cls(
                execution_id=payload.get("execution_id", ""),
                protocol_id=payload.get("protocol_id", ""),
                oos_spec_hash=payload.get("oos_spec_hash", ""),
                selection_decision_id=payload.get("selection_decision_id", ""),
                strategy_freeze_id=payload.get("strategy_freeze_id", ""),
                strategy_version_id=payload.get("strategy_version_id", ""),
                strategy_content_hash=payload.get("strategy_content_hash", ""),
                oos_start=date.fromisoformat(str(payload.get("oos_start", ""))),
                oos_end=date.fromisoformat(str(payload.get("oos_end", ""))),
                warmup_start=date.fromisoformat(str(payload.get("warmup_start", ""))),
                configuration_hash=payload.get("configuration_hash", ""),
                status=payload.get("status", ""),
                attempt_count=payload.get("attempt_count"),
                created_at=datetime.fromisoformat(str(payload.get("created_at", ""))),
                updated_at=datetime.fromisoformat(str(payload.get("updated_at", ""))),
                lease_token=payload.get("lease_token"),
                lease_owner=payload.get("lease_owner"),
                lease_expires_at=parsed_datetime("lease_expires_at"),
                started_at=parsed_datetime("started_at"),
                finished_at=parsed_datetime("finished_at"),
                failure_code=payload.get("failure_code"),
                failure_message_safe=payload.get("failure_message_safe"),
                failure_retryable=payload.get("failure_retryable"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise OosResultIntegrityError(
                "OOS execution payload is malformed", code="OOS_EXECUTION_INTEGRITY_ERROR"
            ) from exc


@dataclass(frozen=True)
class OosExecutionEvent:
    """Append-only audit event for execution control state changes."""

    event_id: str
    execution_id: str
    protocol_id: str
    event_type: str
    attempt_number: int
    occurred_at: datetime
    actor: str
    payload: Mapping[str, Any]
    from_state: OosExecutionStatus | None = None
    to_state: OosExecutionStatus | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id"))
        object.__setattr__(self, "protocol_id", _text(self.protocol_id, "protocol_id"))
        event_type = _text(self.event_type, "event_type", max_length=64)
        if not _SAFE_NAME.fullmatch(event_type):
            raise OosExecutionError("event_type is invalid", code="OOS_EXECUTION_INVALID")
        object.__setattr__(self, "event_type", event_type)
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or self.attempt_number < 0
        ):
            raise OosExecutionError(
                "attempt_number must be non-negative", code="OOS_EXECUTION_INVALID"
            )
        object.__setattr__(self, "occurred_at", _aware_datetime(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "actor", _text(self.actor, "actor"))
        if not isinstance(self.payload, Mapping):
            raise OosExecutionError("event payload must be an object", code="OOS_EXECUTION_INVALID")
        object.__setattr__(self, "payload", MappingProxyType(_safe_value(self.payload)))
        for label in ("from_state", "to_state"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(
                    self,
                    label,
                    value if isinstance(value, OosExecutionStatus) else OosExecutionStatus(value),
                )
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise OosExecutionError("sequence must be positive", code="OOS_EXECUTION_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "protocol_id": self.protocol_id,
            "event_type": self.event_type,
            "attempt_number": self.attempt_number,
            "occurred_at": self.occurred_at.isoformat(),
            "actor": self.actor,
            "payload": dict(self.payload),
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value if self.to_state else None,
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, payload: object) -> OosExecutionEvent:
        if not isinstance(payload, Mapping):
            raise OosExecutionError(
                "event payload is invalid", code="OOS_EXECUTION_INTEGRITY_ERROR"
            )
        try:
            return cls(
                event_id=payload.get("event_id", ""),
                execution_id=payload.get("execution_id", ""),
                protocol_id=payload.get("protocol_id", ""),
                event_type=payload.get("event_type", ""),
                attempt_number=payload.get("attempt_number"),
                occurred_at=datetime.fromisoformat(str(payload.get("occurred_at", ""))),
                actor=payload.get("actor", ""),
                payload=payload.get("payload", {}),
                from_state=payload.get("from_state"),
                to_state=payload.get("to_state"),
                sequence=payload.get("sequence"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise OosExecutionError(
                "event payload is malformed", code="OOS_EXECUTION_INTEGRITY_ERROR"
            ) from exc


__all__ = [
    "OosExecution",
    "OosExecutionEvent",
    "OosExecutionStatus",
    "oos_execution_id_for",
]
