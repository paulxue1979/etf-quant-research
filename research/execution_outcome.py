"""Immutable in-memory outcome for one PHASE 8D-3 candidate execution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from analytics.models import PerformanceAnalysisResult
from backtest.integration import StrategyBacktestResult
from data.models import PriceField
from research.canonical import canonical_json

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)"
    r"\s*(?:[:=]|\b)",
    re.IGNORECASE,
)


class ExperimentExecutionOutcomeStatus(StrEnum):
    """Terminal status returned by the single-candidate execution service."""

    COMPLETED = "completed"
    FAILED = "failed"


def _text(value: object, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum or _SENSITIVE.search(result):
        raise ValueError(f"{label} is invalid")
    return result


def _hash(value: object, label: str) -> str:
    result = _text(value, label, maximum=64)
    if not _HASH.fullmatch(result):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return result


def _freeze_provenance(value: object) -> object:
    """Recursively validate and freeze JSON-compatible provenance values."""
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SENSITIVE.search(key):
                raise ValueError("provenance contains sensitive data")
            frozen[key] = _freeze_provenance(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_provenance(item) for item in value)
    if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        raise ValueError("provenance contains sensitive data")
    return value


@dataclass(frozen=True)
class ExperimentExecutionOutcome:
    """A non-persisted, provenance-complete candidate execution outcome.

    This object intentionally does not model an ExperimentResult table.  PHASE
    8D-3 returns the existing backtest and analytics objects in memory only.
    """

    experiment_id: str
    candidate_id: str
    candidate_index: int
    parameter_set_hash: str
    parameter_space_hash: str
    candidate_set_hash: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    derived_strategy_version_id: str | None
    derived_strategy_version_hash: str | None
    binding_hash: str
    warmup_start: date | None
    warmup_end: date | None
    is_start: date
    is_end: date
    price_field_used: PriceField
    backtest_configuration_hash: str
    engine_version: str
    analysis_version: str
    status: ExperimentExecutionOutcomeStatus
    provenance: Mapping[str, Any]
    candidate_execution_id: str | None = None
    backtest_result: StrategyBacktestResult | None = None
    performance_analysis_result: PerformanceAnalysisResult | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        for label in (
            "experiment_id",
            "candidate_id",
            "base_strategy_version_id",
            "binding_hash",
            "engine_version",
            "analysis_version",
        ):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        for label in (
            "parameter_set_hash",
            "parameter_space_hash",
            "candidate_set_hash",
            "base_strategy_version_hash",
            "backtest_configuration_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        if isinstance(self.candidate_index, bool) or not isinstance(self.candidate_index, int):
            raise ValueError("candidate_index must be an integer")
        if self.candidate_index < 0:
            raise ValueError("candidate_index must be non-negative")
        if not isinstance(self.is_start, date) or not isinstance(self.is_end, date):
            raise TypeError("IS bounds must be dates")
        if self.is_start > self.is_end:
            raise ValueError("IS bounds must be ordered")
        if self.warmup_start is not None and not isinstance(self.warmup_start, date):
            raise TypeError("warmup_start must be a date or None")
        if self.warmup_end is not None and not isinstance(self.warmup_end, date):
            raise TypeError("warmup_end must be a date or None")
        if self.warmup_start is not None and self.warmup_end is not None:
            if self.warmup_start > self.warmup_end:
                raise ValueError("warmup bounds must be ordered")
            if self.warmup_end >= self.is_start:
                raise ValueError("warmup must end before the IS range")
        if not isinstance(self.price_field_used, PriceField):
            object.__setattr__(self, "price_field_used", PriceField(self.price_field_used))
        if not isinstance(self.status, ExperimentExecutionOutcomeStatus):
            object.__setattr__(
                self, "status", ExperimentExecutionOutcomeStatus(self.status)
            )
        if self.candidate_execution_id is not None:
            object.__setattr__(
                self,
                "candidate_execution_id",
                _text(self.candidate_execution_id, "candidate_execution_id"),
            )
        if self.derived_strategy_version_id is None:
            if self.derived_strategy_version_hash is not None:
                raise ValueError("derived strategy hash requires an id")
        else:
            object.__setattr__(
                self,
                "derived_strategy_version_id",
                _text(self.derived_strategy_version_id, "derived_strategy_version_id"),
            )
            if self.derived_strategy_version_hash is None:
                raise ValueError("derived strategy id requires a hash")
            object.__setattr__(
                self,
                "derived_strategy_version_hash",
                _hash(self.derived_strategy_version_hash, "derived_strategy_version_hash"),
            )
        if not isinstance(self.provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        canonical_json(self.provenance)
        object.__setattr__(self, "provenance", _freeze_provenance(self.provenance))

        complete = self.status is ExperimentExecutionOutcomeStatus.COMPLETED
        if complete:
            if self.backtest_result is None or self.performance_analysis_result is None:
                raise ValueError("completed outcome requires backtest and analytics results")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("completed outcome must not contain failure metadata")
        else:
            if not self.failure_code or not self.failure_message:
                raise ValueError("failed outcome requires failure metadata")
            if _SENSITIVE.search(self.failure_code) or _SENSITIVE.search(self.failure_message):
                raise ValueError("failure metadata contains sensitive data")
            if self.backtest_result is not None or self.performance_analysis_result is not None:
                raise ValueError("failed outcome must not contain partial results")

    @property
    def succeeded(self) -> bool:
        return self.status is ExperimentExecutionOutcomeStatus.COMPLETED


__all__ = ["ExperimentExecutionOutcome", "ExperimentExecutionOutcomeStatus"]
