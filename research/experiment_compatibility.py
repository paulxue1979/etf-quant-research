"""Read-only compatibility diagnostics for persisted experiment results."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any

from research.canonical import canonical_json

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
    re.IGNORECASE,
)
_UNSAFE_TEXT = re.compile(
    r"(?:TIINGO_API_KEY|API_KEY|SECRET_SENTINEL|BEGIN\s+PRIVATE\s+KEY|bearer\s+|traceback|/users/|/private/|\\)",
    re.IGNORECASE,
)


def _public_value(value: object) -> object:
    """Keep diagnostic values JSON-safe without exposing secrets or host details."""
    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    if isinstance(value, Enum):
        return _public_value(value.value)
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, str):
        return "<redacted>" if _UNSAFE_TEXT.search(value) else value
    if isinstance(value, float) and not math.isfinite(value):
        return "<redacted>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return "<redacted>"


class CompatibilityStatus(StrEnum):
    """Outcome of comparing the compatible subset of candidate results."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    NOT_EVALUABLE = "not_evaluable"


class CompatibilityReasonCode(StrEnum):
    """Stable, non-sensitive diagnostic reason codes."""

    IS_RANGE_MISMATCH = "IS_RANGE_MISMATCH"
    EXPERIMENT_BINDING_MISMATCH = "EXPERIMENT_BINDING_MISMATCH"
    PROTOCOL_BINDING_MISMATCH = "PROTOCOL_BINDING_MISMATCH"
    PRICE_FIELD_MISMATCH = "PRICE_FIELD_MISMATCH"
    INITIAL_CAPITAL_MISMATCH = "INITIAL_CAPITAL_MISMATCH"
    COMMISSION_MISMATCH = "COMMISSION_MISMATCH"
    SLIPPAGE_MISMATCH = "SLIPPAGE_MISMATCH"
    EXECUTION_RULE_MISMATCH = "EXECUTION_RULE_MISMATCH"
    FRACTIONAL_SHARES_MISMATCH = "FRACTIONAL_SHARES_MISMATCH"
    REBALANCE_POLICY_MISMATCH = "REBALANCE_POLICY_MISMATCH"
    ENGINE_VERSION_MISMATCH = "ENGINE_VERSION_MISMATCH"
    ANALYTICS_VERSION_MISMATCH = "ANALYTICS_VERSION_MISMATCH"
    BACKTEST_CONFIGURATION_MISMATCH = "BACKTEST_CONFIGURATION_MISMATCH"
    DATA_PROVENANCE_MISMATCH = "DATA_PROVENANCE_MISMATCH"
    RESULT_NOT_AVAILABLE = "RESULT_NOT_AVAILABLE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                raise ValueError("compatibility diagnostic contains sensitive data")
            frozen[key_text] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, Enum):
        return _freeze(value.value)
    if isinstance(value, str) and _UNSAFE_TEXT.search(value):
        raise ValueError("compatibility diagnostic contains unsafe text")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("compatibility diagnostic contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported compatibility diagnostic value: {type(value).__name__}")
    return value


def _thaw(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class CompatibilityMismatch:
    """One deterministic, safe mismatch between a reference and candidate."""

    candidate_id: str
    dimension: str
    reason_code: CompatibilityReasonCode
    reference_value: object = None
    candidate_value: object = None
    message: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate_id, str)
            or not self.candidate_id.strip()
            or not isinstance(self.dimension, str)
            or not self.dimension.strip()
        ):
            raise ValueError("compatibility mismatch identity must be non-empty")
        object.__setattr__(self, "reason_code", CompatibilityReasonCode(self.reason_code))
        object.__setattr__(self, "reference_value", _freeze(_public_value(self.reference_value)))
        object.__setattr__(self, "candidate_value", _freeze(_public_value(self.candidate_value)))
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("compatibility mismatch message must be non-empty")
        if _UNSAFE_TEXT.search(self.message):
            raise ValueError("compatibility mismatch message is unsafe")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "dimension": self.dimension,
            "reason_code": self.reason_code.value,
            "reference_value": _thaw(self.reference_value),
            "candidate_value": _thaw(self.candidate_value),
            "message": self.message,
        }


@dataclass(frozen=True)
class CompatibilityDiagnostic:
    """Transient compatibility result; it is never persisted or ranked."""

    status: CompatibilityStatus
    experiment_id: str
    protocol_id: str
    reference_candidate_id: str | None
    compared_candidate_ids: tuple[str, ...]
    unavailable_candidate_ids: tuple[str, ...]
    checked_dimensions: tuple[str, ...]
    mismatches: tuple[CompatibilityMismatch, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CompatibilityStatus(self.status))
        object.__setattr__(self, "compared_candidate_ids", tuple(self.compared_candidate_ids))
        object.__setattr__(self, "unavailable_candidate_ids", tuple(self.unavailable_candidate_ids))
        object.__setattr__(self, "checked_dimensions", tuple(self.checked_dimensions))
        object.__setattr__(self, "mismatches", tuple(self.mismatches))

    @property
    def comparable(self) -> bool:
        return self.status is CompatibilityStatus.COMPATIBLE

    @property
    def is_compatible(self) -> bool:
        """Return the explicit compatibility flag used by API consumers."""
        return self.comparable

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_compatible": self.is_compatible,
            "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id,
            "reference_candidate_id": self.reference_candidate_id,
            "compared_candidate_ids": list(self.compared_candidate_ids),
            "unavailable_candidate_ids": list(self.unavailable_candidate_ids),
            "checked_dimensions": list(self.checked_dimensions),
            "mismatches": [item.to_dict() for item in self.mismatches],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


__all__ = [
    "CompatibilityDiagnostic",
    "CompatibilityMismatch",
    "CompatibilityReasonCode",
    "CompatibilityStatus",
]
