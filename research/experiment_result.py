"""Immutable PHASE 8D-4 result identity and analytics summary."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from data.models import PriceField
from research.canonical import canonical_json, sha256_hash

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY|authorization\s*:)",
    re.IGNORECASE,
)


def _text(value: object, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum or _SENSITIVE_KEY.search(normalized):
        raise ValueError(f"{label} is invalid")
    return normalized


def _hash(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64)
    if not _HASH.fullmatch(normalized):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return normalized


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SENSITIVE_KEY.search(key):
                raise ValueError("result contains sensitive data")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        raise ValueError("result contains sensitive data")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result contains a non-finite number")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"result value of type {type(value).__name__} is not JSON-compatible")


def _thaw(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ExperimentResult:
    """One immutable, provenance-complete result for one experiment candidate.

    The full BacktestRun remains the source of truth for accounting details.
    This record stores only experiment identity, provenance, and the analytics
    summary returned by the existing performance engine.
    """

    experiment_result_id: str
    experiment_id: str
    candidate_id: str
    candidate_index: int
    parameter_set_hash: str
    parameter_space_hash: str
    candidate_set_hash: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    derived_strategy_version_id: str
    derived_strategy_version_hash: str
    binding_hash: str
    backtest_run_id: str
    is_start: date
    is_end: date
    warmup_start: date | None
    warmup_end: date | None
    price_field_used: PriceField
    backtest_configuration_hash: str
    engine_version: str
    analysis_version: str
    data_snapshot_reference: Mapping[str, Any]
    performance_summary: Mapping[str, Any]
    result_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        for label in (
            "experiment_result_id",
            "experiment_id",
            "candidate_id",
            "base_strategy_version_id",
            "derived_strategy_version_id",
            "backtest_run_id",
            "engine_version",
            "analysis_version",
        ):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        for label in (
            "parameter_set_hash",
            "parameter_space_hash",
            "candidate_set_hash",
            "base_strategy_version_hash",
            "derived_strategy_version_hash",
            "binding_hash",
            "backtest_configuration_hash",
            "result_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        if (
            isinstance(self.candidate_index, bool)
            or not isinstance(self.candidate_index, int)
            or self.candidate_index < 0
        ):
            raise ValueError("candidate_index must be non-negative")
        for label in ("is_start", "is_end"):
            if not isinstance(getattr(self, label), date):
                raise TypeError(f"{label} must be a date")
        if self.is_start > self.is_end:
            raise ValueError("IS dates must be ordered")
        if self.warmup_start is not None and not isinstance(self.warmup_start, date):
            raise TypeError("warmup_start must be a date or None")
        if self.warmup_end is not None and not isinstance(self.warmup_end, date):
            raise TypeError("warmup_end must be a date or None")
        if (self.warmup_start is None) != (self.warmup_end is None):
            raise ValueError("warmup bounds must be both present or both absent")
        if self.warmup_start is not None and self.warmup_start > self.warmup_end:
            raise ValueError("warmup dates must be ordered")
        if self.warmup_end is not None and self.warmup_end >= self.is_start:
            raise ValueError("warmup must end before IS starts")
        object.__setattr__(
            self,
            "price_field_used",
            self.price_field_used
            if isinstance(self.price_field_used, PriceField)
            else PriceField(self.price_field_used),
        )
        object.__setattr__(self, "created_at", _datetime(self.created_at, "created_at"))
        for label in ("data_snapshot_reference", "performance_summary"):
            value = getattr(self, label)
            if not isinstance(value, Mapping):
                raise TypeError(f"{label} must be a mapping")
            frozen = _freeze(value)
            canonical_json(frozen)
            object.__setattr__(self, label, frozen)
        expected_id = self.deterministic_result_id(self.hash_payload())
        if self.experiment_result_id != expected_id:
            raise ValueError("experiment_result_id does not match result identity")
        if self.result_hash != sha256_hash(self.hash_payload()):
            raise ValueError("result_hash does not match result identity")

    @classmethod
    def from_outcome(
        cls,
        outcome: Any,
        *,
        backtest_run_id: str,
        created_at: datetime,
    ) -> ExperimentResult:
        """Build a result from an already completed 8D-3 outcome."""
        if not getattr(outcome, "succeeded", False):
            raise ValueError("only successful execution outcomes can become results")
        analysis = outcome.performance_analysis_result
        if analysis is None:
            raise ValueError("completed outcome has no performance analysis")
        provenance = outcome.provenance.get("data_snapshot_reference", {})
        return cls.create(
            experiment_id=outcome.experiment_id,
            candidate_id=outcome.candidate_id,
            candidate_index=outcome.candidate_index,
            parameter_set_hash=outcome.parameter_set_hash,
            parameter_space_hash=outcome.parameter_space_hash,
            candidate_set_hash=outcome.candidate_set_hash,
            base_strategy_version_id=outcome.base_strategy_version_id,
            base_strategy_version_hash=outcome.base_strategy_version_hash,
            derived_strategy_version_id=outcome.derived_strategy_version_id,
            derived_strategy_version_hash=outcome.derived_strategy_version_hash,
            binding_hash=outcome.binding_hash,
            backtest_run_id=backtest_run_id,
            is_start=outcome.is_start,
            is_end=outcome.is_end,
            warmup_start=outcome.warmup_start,
            warmup_end=outcome.warmup_end,
            price_field_used=outcome.price_field_used,
            backtest_configuration_hash=outcome.backtest_configuration_hash,
            engine_version=outcome.engine_version,
            analysis_version=outcome.analysis_version,
            data_snapshot_reference=provenance,
            performance_summary=analysis.to_dict(),
            created_at=created_at,
        )

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        candidate_id: str,
        candidate_index: int,
        parameter_set_hash: str,
        parameter_space_hash: str,
        candidate_set_hash: str,
        base_strategy_version_id: str,
        base_strategy_version_hash: str,
        derived_strategy_version_id: str | None,
        derived_strategy_version_hash: str | None,
        binding_hash: str,
        backtest_run_id: str,
        is_start: date,
        is_end: date,
        warmup_start: date | None,
        warmup_end: date | None,
        price_field_used: PriceField,
        backtest_configuration_hash: str,
        engine_version: str,
        analysis_version: str,
        data_snapshot_reference: Mapping[str, Any],
        performance_summary: Mapping[str, Any],
        created_at: datetime,
    ) -> ExperimentResult:
        if derived_strategy_version_id is None or derived_strategy_version_hash is None:
            raise ValueError("completed result requires a derived strategy version")
        if not isinstance(data_snapshot_reference, Mapping):
            raise TypeError("data_snapshot_reference must be a mapping")
        if not isinstance(performance_summary, Mapping):
            raise TypeError("performance_summary must be a mapping")
        # Validate recursively before hashing so non-finite and sensitive values
        # receive the domain-level error rather than a lower-level JSON error.
        data_snapshot_reference = _freeze(data_snapshot_reference)
        performance_summary = _freeze(performance_summary)
        payload = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "candidate_index": candidate_index,
            "parameter_set_hash": parameter_set_hash,
            "parameter_space_hash": parameter_space_hash,
            "candidate_set_hash": candidate_set_hash,
            "base_strategy_version_id": base_strategy_version_id,
            "base_strategy_version_hash": base_strategy_version_hash,
            "derived_strategy_version_id": derived_strategy_version_id,
            "derived_strategy_version_hash": derived_strategy_version_hash,
            "binding_hash": binding_hash,
            "backtest_run_id": backtest_run_id,
            "is_start": is_start,
            "is_end": is_end,
            "warmup_start": warmup_start,
            "warmup_end": warmup_end,
            "price_field_used": price_field_used,
            "backtest_configuration_hash": backtest_configuration_hash,
            "engine_version": engine_version,
            "analysis_version": analysis_version,
            "data_snapshot_reference": data_snapshot_reference,
            "performance_summary": performance_summary,
        }
        result_hash = sha256_hash(payload)
        return cls(
            **payload,
            experiment_result_id=cls.deterministic_result_id(payload),
            result_hash=result_hash,
            created_at=created_at,
        )

    @staticmethod
    def deterministic_result_id(payload: Mapping[str, Any]) -> str:
        return "experiment-result-" + sha256_hash(payload)

    def hash_payload(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "candidate_index": self.candidate_index,
            "parameter_set_hash": self.parameter_set_hash,
            "parameter_space_hash": self.parameter_space_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "derived_strategy_version_id": self.derived_strategy_version_id,
            "derived_strategy_version_hash": self.derived_strategy_version_hash,
            "binding_hash": self.binding_hash,
            "backtest_run_id": self.backtest_run_id,
            "is_start": self.is_start,
            "is_end": self.is_end,
            "warmup_start": self.warmup_start,
            "warmup_end": self.warmup_end,
            "price_field_used": self.price_field_used,
            "backtest_configuration_hash": self.backtest_configuration_hash,
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "data_snapshot_reference": self.data_snapshot_reference,
            "performance_summary": self.performance_summary,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **{key: _thaw(value) for key, value in self.hash_payload().items()},
            "experiment_result_id": self.experiment_result_id,
            "result_hash": self.result_hash,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExperimentResult:
        if not isinstance(payload, Mapping):
            raise ValueError("experiment result payload must be an object")
        values = dict(payload)
        values["created_at"] = datetime.fromisoformat(str(values["created_at"]))
        values["is_start"] = date.fromisoformat(str(values["is_start"]))
        values["is_end"] = date.fromisoformat(str(values["is_end"]))
        for label in ("warmup_start", "warmup_end"):
            if values.get(label) is not None:
                values[label] = date.fromisoformat(str(values[label]))
        return cls(**values)


__all__ = ["ExperimentResult"]
