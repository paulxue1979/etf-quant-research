"""Immutable, read-only projections for PHASE 8E-1A experiment research."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from data.models import PriceField

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY|authorization\s*:)",
    re.IGNORECASE,
)


class ExperimentResultStatus(StrEnum):
    """Fact status of one candidate in the read model."""

    COMPLETED = "completed"
    FAILED = "failed"
    NOT_EVALUABLE = "not_evaluable"
    MISSING_RESULT = "missing_result"
    INCONSISTENT = "inconsistent"


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError("read model contains sensitive data")
            frozen[str(key)] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, str):
        if _SENSITIVE_VALUE.search(value):
            raise ValueError("read model contains sensitive data")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("read model contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value
    raise TypeError(f"unsupported read model value: {type(value).__name__}")


def _thaw(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in value:
        if _SENSITIVE_KEY.search(str(key)):
            raise ValueError("read model contains sensitive data")
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


@dataclass(frozen=True)
class ExperimentCandidateView:
    """One candidate projection in canonical persisted candidate order."""

    experiment_id: str
    protocol_id: str
    candidate_id: str
    candidate_index: int
    parameter_set: Mapping[str, Any]
    parameter_set_hash: str
    candidate_set_hash: str
    parameter_space_hash: str
    objective_spec_hash: str
    execution_status: str | None
    result_status: ExperimentResultStatus
    experiment_result_id: str | None = None
    result_hash: str | None = None
    backtest_run_id: str | None = None
    base_strategy_version_id: str | None = None
    base_strategy_version_hash: str | None = None
    derived_strategy_version_id: str | None = None
    derived_strategy_version_hash: str | None = None
    binding_hash: str | None = None
    materialization_spec_hash: str | None = None
    is_start: date | None = None
    is_end: date | None = None
    warmup_start: date | None = None
    warmup_end: date | None = None
    price_field_used: PriceField | None = None
    backtest_configuration_hash: str | None = None
    engine_version: str | None = None
    analysis_version: str | None = None
    data_snapshot_reference: Mapping[str, Any] = MappingProxyType({})
    configuration_snapshot: Mapping[str, Any] = MappingProxyType({})
    performance_summary: Mapping[str, Any] = MappingProxyType({})
    failure_code: str | None = None
    failure_summary: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.candidate_index < 0:
            raise ValueError("candidate_index must be non-negative")
        object.__setattr__(self, "parameter_set", _mapping(self.parameter_set))
        object.__setattr__(self, "data_snapshot_reference", _mapping(self.data_snapshot_reference))
        object.__setattr__(self, "configuration_snapshot", _mapping(self.configuration_snapshot))
        object.__setattr__(self, "performance_summary", _mapping(self.performance_summary))
        if self.price_field_used is not None and not isinstance(self.price_field_used, PriceField):
            object.__setattr__(self, "price_field_used", PriceField(self.price_field_used))

    def to_dict(self) -> dict[str, Any]:
        return {
            key: _thaw(value)
            for key, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class ExperimentResultsSummary:
    """Counts and completeness metadata; no ranking or score is included."""

    candidate_count: int
    completed_count: int
    failed_count: int
    not_evaluable_count: int
    running_count: int
    pending_count: int
    result_count: int
    complete: bool
    completeness_status: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ExperimentResultsReadModel:
    """Read-only composition of persisted experiment artifacts."""

    experiment_id: str
    protocol_id: str
    experiment_status: str
    is_start: date
    is_end: date
    parameter_space_hash: str
    objective_spec_hash: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    engine_version: str
    analysis_version: str
    candidates: tuple[ExperimentCandidateView, ...]
    summary: ExperimentResultsSummary

    def __post_init__(self) -> None:
        candidates = tuple(self.candidates)
        if tuple(item.candidate_index for item in candidates) != tuple(range(len(candidates))):
            raise ValueError("candidate views must preserve canonical candidate order")
        object.__setattr__(self, "candidates", candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id,
            "experiment_status": self.experiment_status,
            "is_start": self.is_start.isoformat(),
            "is_end": self.is_end.isoformat(),
            "parameter_space_hash": self.parameter_space_hash,
            "objective_spec_hash": self.objective_spec_hash,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "candidates": [item.to_dict() for item in self.candidates],
            "summary": self.summary.to_dict(),
        }


__all__ = [
    "ExperimentCandidateView",
    "ExperimentResultStatus",
    "ExperimentResultsReadModel",
    "ExperimentResultsSummary",
]
