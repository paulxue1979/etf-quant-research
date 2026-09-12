"""Pure PHASE 8E-1D researcher-selection domain contracts.

This module records an explicit human choice from an IS-only experiment
universe.  It does not rank candidates, persist selections, hand off to the
PHASE 7 protocol, or inspect OOS artifacts.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from research.canonical import canonical_json, sha256_hash
from research.exceptions import InvalidExperimentSelectionError, SelectionEligibilityError
from research.experiment_compatibility import CompatibilityDiagnostic, CompatibilityStatus
from research.experiment_read_model import (
    ExperimentCandidateView,
    ExperimentResultsReadModel,
    ExperimentResultStatus,
)
from research.objective_evaluation import (
    CandidateObjectiveEvaluation,
    ObjectiveEvaluationState,
)
from strategies import StrategyVersion

_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,255}$")
_CANDIDATE_ID = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_.:-]{0,255}|[0-9a-f]{64})$")
_UNSAFE_TEXT = re.compile(
    r"(?:TIINGO_API_KEY|API_KEY|SECRET_SENTINEL|BEGIN\s+PRIVATE\s+KEY|"
    r"authorization\s*:|bearer\s+|traceback|(?:/Users/|/private/|/etc/)|\\)",
    re.IGNORECASE,
)
_MARKUP = re.compile(r"<\s*/?\s*[a-z][^>]*>|javascript\s*:", re.IGNORECASE)
_OOS_KEY = re.compile(r"(?:^|[_\-.])oos(?:$|[_\-.])", re.IGNORECASE)
_OOS_TEXT = re.compile(r"\boos\b", re.IGNORECASE)


class ExperimentSelectionMethod(StrEnum):
    """Human-directed selection methods; no automated method is supported."""

    RESEARCHER_JUDGMENT = "researcher_judgment"
    CONSTRAINT_FILTERED_RESEARCHER_SELECTION = "constraint_filtered_researcher_selection"
    MANUAL_RESEARCH_SELECTION = "manual_research_selection"


class SelectionEligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class SelectionEligibilityReasonCode(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
    EXPERIMENT_BINDING_MISMATCH = "EXPERIMENT_BINDING_MISMATCH"
    PROTOCOL_BINDING_MISMATCH = "PROTOCOL_BINDING_MISMATCH"
    CANDIDATE_NOT_TERMINAL = "CANDIDATE_NOT_TERMINAL"
    RESULT_NOT_AVAILABLE = "RESULT_NOT_AVAILABLE"
    RESULT_INTEGRITY_ERROR = "RESULT_INTEGRITY_ERROR"
    DERIVED_STRATEGY_INTEGRITY_ERROR = "DERIVED_STRATEGY_INTEGRITY_ERROR"
    CANDIDATE_UNIVERSE_MISMATCH = "CANDIDATE_UNIVERSE_MISMATCH"
    BASE_STRATEGY_BINDING_MISMATCH = "BASE_STRATEGY_BINDING_MISMATCH"
    PARAMETER_SPACE_MISMATCH = "PARAMETER_SPACE_MISMATCH"
    OBJECTIVE_HASH_MISMATCH = "OBJECTIVE_HASH_MISMATCH"
    OBJECTIVE_CONSTRAINT_FAILED = "OBJECTIVE_CONSTRAINT_FAILED"
    OBJECTIVE_CONSTRAINT_NOT_EVALUABLE = "OBJECTIVE_CONSTRAINT_NOT_EVALUABLE"
    COMPATIBILITY_FAILED = "COMPATIBILITY_FAILED"
    COMPATIBILITY_NOT_EVALUABLE = "COMPATIBILITY_NOT_EVALUABLE"
    IS_BOUNDARY_ERROR = "IS_BOUNDARY_ERROR"


def _text(value: object, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidExperimentSelectionError(f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum or not _ID.fullmatch(result):
        raise InvalidExperimentSelectionError(f"{label} is invalid")
    return result


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise InvalidExperimentSelectionError(f"{label} must be a SHA-256 hex digest")
    return value


def _candidate_id(value: object, label: str = "candidate_id") -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidExperimentSelectionError(f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > 256 or not _CANDIDATE_ID.fullmatch(result):
        raise InvalidExperimentSelectionError(f"{label} is invalid")
    return result


def _date(value: object, label: str) -> date:
    if not isinstance(value, date):
        raise InvalidExperimentSelectionError(f"{label} must be a date")
    return value


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidExperimentSelectionError(f"{label} must be timezone-aware")
    return value


def _safe_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidExperimentSelectionError(f"{label} must be an object")

    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            frozen: dict[str, object] = {}
            for key, nested in item.items():
                key_text = str(key)
                if _OOS_KEY.search(key_text) or _UNSAFE_TEXT.search(key_text):
                    raise InvalidExperimentSelectionError(f"{label} contains restricted data")
                frozen[key_text] = freeze(nested)
            return MappingProxyType(frozen)
        if isinstance(item, (list, tuple)):
            return tuple(freeze(nested) for nested in item)
        if isinstance(item, float) and not math.isfinite(item):
            raise InvalidExperimentSelectionError(f"{label} contains a non-finite number")
        if isinstance(item, str) and (_UNSAFE_TEXT.search(item) or _OOS_KEY.search(item)):
            raise InvalidExperimentSelectionError(f"{label} contains restricted data")
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise InvalidExperimentSelectionError(f"{label} contains an unsupported value")

    return MappingProxyType({str(key): freeze(item) for key, item in value.items()})


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


@dataclass(frozen=True)
class SelectionEligibility:
    """Structured, non-sensitive result of candidate eligibility validation."""

    eligible: bool
    status: SelectionEligibilityStatus
    reason_code: SelectionEligibilityReasonCode
    candidate_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _candidate_id(self.candidate_id))
        object.__setattr__(self, "status", SelectionEligibilityStatus(self.status))
        object.__setattr__(self, "reason_code", SelectionEligibilityReasonCode(self.reason_code))
        if bool(self.eligible) != (self.status is SelectionEligibilityStatus.ELIGIBLE):
            raise InvalidExperimentSelectionError("eligibility flag and status disagree")
        if (
            not isinstance(self.reason, str)
            or not self.reason.strip()
            or _UNSAFE_TEXT.search(self.reason)
            or _OOS_TEXT.search(self.reason)
        ):
            raise InvalidExperimentSelectionError("eligibility reason is invalid")
        object.__setattr__(self, "reason", self.reason.strip()[:256])

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExperimentSelectionEvidence:
    """Immutable IS evidence snapshot; no OOS payload is accepted or stored."""

    candidate_count: int
    completed_count: int
    failed_count: int
    eligible_count: int
    selected_candidate_id: str
    selected_candidate_index: int
    selected_parameter_set_hash: str
    selected_experiment_result_id: str
    selected_result_hash: str
    selected_derived_strategy_version_id: str
    selected_derived_strategy_content_hash: str
    objective_hash: str
    candidate_set_hash: str
    parameter_space_hash: str
    objective_evaluation_state: ObjectiveEvaluationState
    compatibility_status: CompatibilityStatus
    compatibility_hash: str
    is_start: date
    is_end: date
    backtest_configuration_hash: str
    engine_version: str
    analysis_version: str
    data_snapshot_reference: Mapping[str, Any]

    def __post_init__(self) -> None:
        for label in ("candidate_count", "completed_count", "failed_count", "eligible_count"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidExperimentSelectionError(f"{label} must be a non-negative integer")
        if self.completed_count > self.candidate_count or self.failed_count > self.candidate_count:
            raise InvalidExperimentSelectionError("evidence counts are inconsistent")
        if self.eligible_count > self.candidate_count:
            raise InvalidExperimentSelectionError("eligible_count exceeds candidate_count")
        for label in (
            "selected_candidate_id",
            "selected_experiment_result_id",
            "selected_derived_strategy_version_id",
            "engine_version",
            "analysis_version",
        ):
            validator = _candidate_id if label == "selected_candidate_id" else _text
            object.__setattr__(self, label, validator(getattr(self, label), label))
        if (
            isinstance(self.selected_candidate_index, bool)
            or not isinstance(self.selected_candidate_index, int)
            or self.selected_candidate_index < 0
        ):
            raise InvalidExperimentSelectionError("selected_candidate_index must be non-negative")
        for label in (
            "selected_parameter_set_hash",
            "selected_result_hash",
            "selected_derived_strategy_content_hash",
            "objective_hash",
            "candidate_set_hash",
            "parameter_space_hash",
            "compatibility_hash",
            "backtest_configuration_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        object.__setattr__(
            self,
            "objective_evaluation_state",
            ObjectiveEvaluationState(self.objective_evaluation_state),
        )
        object.__setattr__(
            self, "compatibility_status", CompatibilityStatus(self.compatibility_status)
        )
        object.__setattr__(self, "is_start", _date(self.is_start, "is_start"))
        object.__setattr__(self, "is_end", _date(self.is_end, "is_end"))
        if self.is_start > self.is_end:
            raise InvalidExperimentSelectionError("IS dates must be ordered")
        object.__setattr__(
            self,
            "data_snapshot_reference",
            _safe_mapping(self.data_snapshot_reference, "data_snapshot_reference"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "eligible_count": self.eligible_count,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate_index": self.selected_candidate_index,
            "selected_parameter_set_hash": self.selected_parameter_set_hash,
            "selected_experiment_result_id": self.selected_experiment_result_id,
            "selected_result_hash": self.selected_result_hash,
            "selected_derived_strategy_version_id": self.selected_derived_strategy_version_id,
            "selected_derived_strategy_content_hash": self.selected_derived_strategy_content_hash,
            "objective_hash": self.objective_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "parameter_space_hash": self.parameter_space_hash,
            "objective_evaluation_state": self.objective_evaluation_state.value,
            "compatibility_status": self.compatibility_status.value,
            "compatibility_hash": self.compatibility_hash,
            "is_start": self.is_start.isoformat(),
            "is_end": self.is_end.isoformat(),
            "backtest_configuration_hash": self.backtest_configuration_hash,
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "data_snapshot_reference": _thaw(self.data_snapshot_reference),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ExperimentSelectionEvidence:
        if not isinstance(payload, Mapping):
            raise InvalidExperimentSelectionError("selection evidence must be an object")
        data = dict(payload)
        data["is_start"] = date.fromisoformat(str(data["is_start"]))
        data["is_end"] = date.fromisoformat(str(data["is_end"]))
        return cls(**data)


@dataclass(frozen=True)
class ExperimentSelectionDecision:
    """Explicit human selection from one frozen experiment candidate universe."""

    selection_id: str
    selection_hash: str
    experiment_id: str
    protocol_id: str
    selected_candidate_id: str
    selected_candidate_index: int
    selected_parameter_set_hash: str
    selected_experiment_result_id: str
    selected_result_hash: str
    selected_derived_strategy_version_id: str
    selected_derived_strategy_content_hash: str
    objective_hash: str
    candidate_set_hash: str
    parameter_space_hash: str
    selection_method: ExperimentSelectionMethod
    researcher_rationale: str
    evidence: ExperimentSelectionEvidence
    created_at: datetime

    def __post_init__(self) -> None:
        for label in ("selection_id", "experiment_id", "protocol_id"):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        object.__setattr__(self, "selection_hash", _hash(self.selection_hash, "selection_hash"))
        for label in (
            "selected_candidate_id",
            "selected_experiment_result_id",
            "selected_derived_strategy_version_id",
        ):
            validator = _candidate_id if label == "selected_candidate_id" else _text
            object.__setattr__(self, label, validator(getattr(self, label), label))
        if (
            isinstance(self.selected_candidate_index, bool)
            or not isinstance(self.selected_candidate_index, int)
            or self.selected_candidate_index < 0
        ):
            raise InvalidExperimentSelectionError("selected_candidate_index must be non-negative")
        for label in (
            "selected_parameter_set_hash",
            "selected_result_hash",
            "selected_derived_strategy_content_hash",
            "objective_hash",
            "candidate_set_hash",
            "parameter_space_hash",
        ):
            object.__setattr__(self, label, _hash(getattr(self, label), label))
        try:
            object.__setattr__(
                self, "selection_method", ExperimentSelectionMethod(self.selection_method)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidExperimentSelectionError("selection_method is not human-directed") from exc
        if (
            not isinstance(self.researcher_rationale, str)
            or not self.researcher_rationale.strip()
            or len(self.researcher_rationale.strip()) > 2000
        ):
            raise InvalidExperimentSelectionError("researcher_rationale must be 1-2000 characters")
        rationale = self.researcher_rationale.strip()
        if (
            _UNSAFE_TEXT.search(rationale)
            or _MARKUP.search(rationale)
            or _OOS_TEXT.search(rationale)
        ):
            raise InvalidExperimentSelectionError("researcher_rationale contains unsafe text")
        object.__setattr__(self, "researcher_rationale", rationale)
        if not isinstance(self.evidence, ExperimentSelectionEvidence):
            raise InvalidExperimentSelectionError("evidence must be ExperimentSelectionEvidence")
        if self.evidence.selected_candidate_id != self.selected_candidate_id:
            raise InvalidExperimentSelectionError(
                "decision and evidence candidate identities differ"
            )
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise InvalidExperimentSelectionError("created_at must be timezone-aware")

    def semantic_payload(self) -> dict[str, Any]:
        """Return immutable meaning only; timestamps and database ids are excluded."""
        return {
            "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate_index": self.selected_candidate_index,
            "selected_parameter_set_hash": self.selected_parameter_set_hash,
            "selected_experiment_result_id": self.selected_experiment_result_id,
            "selected_result_hash": self.selected_result_hash,
            "selected_derived_strategy_version_id": self.selected_derived_strategy_version_id,
            "selected_derived_strategy_content_hash": self.selected_derived_strategy_content_hash,
            "objective_hash": self.objective_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "parameter_space_hash": self.parameter_space_hash,
            "selection_method": self.selection_method.value,
            "researcher_rationale": self.researcher_rationale,
            "evidence": self.evidence.to_dict(),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_payload(),
            "selection_id": self.selection_id,
            "selection_hash": self.selection_hash,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ExperimentSelectionDecision:
        if not isinstance(payload, Mapping):
            raise InvalidExperimentSelectionError("selection decision must be an object")
        data = dict(payload)
        data["evidence"] = ExperimentSelectionEvidence.from_dict(data["evidence"])
        data["created_at"] = datetime.fromisoformat(str(data["created_at"]))
        return cls(**data)


def _ineligible(candidate_id: str, code: SelectionEligibilityReasonCode, reason: str) -> None:
    raise SelectionEligibilityError(
        reason,
        code=code.value,
        details={"candidate_id": candidate_id},
    )


def _candidate_universe(
    candidates: Sequence[ExperimentCandidateView] | ExperimentResultsReadModel,
) -> tuple[ExperimentCandidateView, ...]:
    if isinstance(candidates, ExperimentResultsReadModel):
        result = tuple(candidates.candidates)
    else:
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise InvalidExperimentSelectionError("candidates must be a sequence or read model")
        result = tuple(candidates)
    if not all(isinstance(item, ExperimentCandidateView) for item in result):
        raise InvalidExperimentSelectionError("candidates must contain candidate views")
    if tuple(item.candidate_index for item in result) != tuple(range(len(result))):
        raise InvalidExperimentSelectionError("candidates must preserve canonical candidate order")
    candidate_ids = tuple(item.candidate_id for item in result)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise InvalidExperimentSelectionError("candidate IDs must be unique")
    return result


def assess_selection_eligibility(
    experiment: Any,
    candidate: ExperimentCandidateView,
    *,
    objective_evaluation: CandidateObjectiveEvaluation,
    compatibility: CompatibilityDiagnostic,
    derived_strategy_version: StrategyVersion,
) -> SelectionEligibility:
    """Validate one explicitly selected candidate without selecting it."""
    candidate_id = candidate.candidate_id
    if candidate.experiment_id != experiment.experiment_id:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
            candidate_id,
            "candidate belongs to a different experiment",
        )
    if candidate.protocol_id != experiment.protocol_id:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.PROTOCOL_BINDING_MISMATCH,
            candidate_id,
            "candidate belongs to a different protocol",
        )
    if (
        candidate.base_strategy_version_id != experiment.base_strategy_version_id
        or candidate.base_strategy_version_hash != experiment.base_strategy_version_hash
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.BASE_STRATEGY_BINDING_MISMATCH,
            candidate_id,
            "candidate base strategy identity differs from the experiment",
        )
    if (
        candidate.result_status is not ExperimentResultStatus.COMPLETED
        or candidate.execution_status != "completed"
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.CANDIDATE_NOT_TERMINAL,
            candidate_id,
            "candidate execution is not completed",
        )
    if (
        not candidate.experiment_result_id
        or not candidate.result_hash
        or not candidate.backtest_run_id
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.RESULT_NOT_AVAILABLE,
            candidate_id,
            "completed candidate has no complete result identity",
        )
    if not candidate.derived_strategy_version_id or not candidate.derived_strategy_version_hash:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.DERIVED_STRATEGY_INTEGRITY_ERROR,
            candidate_id,
            "completed candidate has no derived strategy identity",
        )
    if (
        candidate.derived_strategy_version_id != derived_strategy_version.version_id
        or candidate.derived_strategy_version_hash != derived_strategy_version.content_hash
        or derived_strategy_version.strategy_id != experiment.strategy_definition_id
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.DERIVED_STRATEGY_INTEGRITY_ERROR,
            candidate_id,
            "candidate and derived strategy identities differ",
        )
    if (
        objective_evaluation.experiment_id != experiment.experiment_id
        or objective_evaluation.candidate_id != candidate_id
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.OBJECTIVE_HASH_MISMATCH,
            candidate_id,
            "objective evidence is bound to another candidate",
        )
    if (
        objective_evaluation.objective_hash != experiment.objective_spec_hash
        or candidate.objective_spec_hash != experiment.objective_spec_hash
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.OBJECTIVE_HASH_MISMATCH,
            candidate_id,
            "objective hash does not match the experiment",
        )
    if (
        objective_evaluation.experiment_result_id != candidate.experiment_result_id
        or objective_evaluation.result_hash != candidate.result_hash
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.RESULT_INTEGRITY_ERROR,
            candidate_id,
            "objective evidence is bound to another result",
        )
    if objective_evaluation.overall_constraint_state is ObjectiveEvaluationState.FAIL:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.OBJECTIVE_CONSTRAINT_FAILED,
            candidate_id,
            "candidate failed the frozen objective constraints",
        )
    if objective_evaluation.overall_constraint_state is not ObjectiveEvaluationState.PASS:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.OBJECTIVE_CONSTRAINT_NOT_EVALUABLE,
            candidate_id,
            "candidate objective constraints are not evaluable",
        )
    if (
        compatibility.experiment_id != experiment.experiment_id
        or compatibility.protocol_id != experiment.protocol_id
    ):
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.COMPATIBILITY_NOT_EVALUABLE,
            candidate_id,
            "compatibility evidence is bound to another experiment",
        )
    if compatibility.status is CompatibilityStatus.INCOMPATIBLE:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.COMPATIBILITY_FAILED,
            candidate_id,
            "experiment compatibility is incompatible",
        )
    if compatibility.status is not CompatibilityStatus.COMPATIBLE:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.COMPATIBILITY_NOT_EVALUABLE,
            candidate_id,
            "experiment compatibility is not evaluable",
        )
    if candidate.is_start != experiment.is_start_date or candidate.is_end != experiment.is_end_date:
        return SelectionEligibility(
            False,
            SelectionEligibilityStatus.INELIGIBLE,
            SelectionEligibilityReasonCode.IS_BOUNDARY_ERROR,
            candidate_id,
            "candidate IS boundaries do not match the experiment",
        )
    return SelectionEligibility(
        True,
        SelectionEligibilityStatus.ELIGIBLE,
        SelectionEligibilityReasonCode.ELIGIBLE,
        candidate_id,
        "candidate is eligible for explicit researcher selection",
    )


def create_experiment_selection_decision(
    *,
    experiment: Any,
    selected_candidate_id: str,
    candidates: Sequence[ExperimentCandidateView] | ExperimentResultsReadModel,
    objective_evaluation: CandidateObjectiveEvaluation,
    compatibility: CompatibilityDiagnostic,
    derived_strategy_version: StrategyVersion,
    selection_method: ExperimentSelectionMethod | str,
    researcher_rationale: str,
    created_at: datetime,
    objective_evaluations: Mapping[str, CandidateObjectiveEvaluation] | None = None,
) -> ExperimentSelectionDecision:
    """Create a decision for a caller-specified candidate after server-side checks."""
    try:
        method = ExperimentSelectionMethod(selection_method)
    except (TypeError, ValueError) as exc:
        raise InvalidExperimentSelectionError("selection_method is not human-directed") from exc
    universe = _candidate_universe(candidates)
    if not universe:
        raise InvalidExperimentSelectionError("candidate universe must not be empty")
    selected = next((item for item in universe if item.candidate_id == selected_candidate_id), None)
    if selected is None:
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.CANDIDATE_NOT_FOUND,
            "selected candidate was not found",
        )
    if isinstance(candidates, ExperimentResultsReadModel) and (
        candidates.experiment_id != experiment.experiment_id
        or candidates.protocol_id != experiment.protocol_id
        ):
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
            "candidate read model is bound to another experiment",
        )
    experiment_bindings = {(item.experiment_id, item.protocol_id) for item in universe}
    if experiment_bindings != {(experiment.experiment_id, experiment.protocol_id)}:
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
            "candidate universe contains a different experiment binding",
        )
    candidate_hashes = {item.candidate_set_hash for item in universe}
    parameter_hashes = {item.parameter_space_hash for item in universe}
    objective_hashes = {item.objective_spec_hash for item in universe}
    base_strategy_bindings = {
        (item.base_strategy_version_id, item.base_strategy_version_hash) for item in universe
    }
    if len(candidate_hashes) != 1 or selected.candidate_set_hash not in candidate_hashes:
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.CANDIDATE_UNIVERSE_MISMATCH,
            "candidate universe hash is inconsistent",
        )
    if base_strategy_bindings != {
        (experiment.base_strategy_version_id, experiment.base_strategy_version_hash)
    }:
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.BASE_STRATEGY_BINDING_MISMATCH,
            "candidate universe base strategy identity is inconsistent",
        )
    if (
        selected.parameter_space_hash != experiment.parameter_space_hash
        or len(parameter_hashes) != 1
    ):
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.PARAMETER_SPACE_MISMATCH,
            "parameter space hash is inconsistent",
        )
    if objective_hashes != {experiment.objective_spec_hash}:
        _ineligible(
            selected_candidate_id,
            SelectionEligibilityReasonCode.OBJECTIVE_HASH_MISMATCH,
            "candidate universe objective hash is inconsistent",
        )
    eligibility = assess_selection_eligibility(
        experiment,
        selected,
        objective_evaluation=objective_evaluation,
        compatibility=compatibility,
        derived_strategy_version=derived_strategy_version,
    )
    if not eligibility.eligible:
        _ineligible(selected_candidate_id, eligibility.reason_code, eligibility.reason)
    if (
        not isinstance(researcher_rationale, str)
        or not researcher_rationale.strip()
        or len(researcher_rationale.strip()) > 2000
        or _UNSAFE_TEXT.search(researcher_rationale)
        or _MARKUP.search(researcher_rationale)
        or _OOS_TEXT.search(researcher_rationale)
    ):
        raise InvalidExperimentSelectionError("researcher_rationale is invalid")
    completed_count = sum(
        item.result_status is ExperimentResultStatus.COMPLETED for item in universe
    )
    failed_count = sum(item.result_status is ExperimentResultStatus.FAILED for item in universe)
    eligible_count = 1
    if objective_evaluations is not None:
        for candidate_key, evaluation in objective_evaluations.items():
            if (
                candidate_key not in {item.candidate_id for item in universe}
                or evaluation.experiment_id != experiment.experiment_id
            ):
                raise InvalidExperimentSelectionError(
                    "objective_evaluations contain an unknown candidate"
                )
        eligible_count = sum(
            item.overall_constraint_state is ObjectiveEvaluationState.PASS
            for item in objective_evaluations.values()
        )
    compatibility_hash = sha256_hash(compatibility.to_dict())
    evidence = ExperimentSelectionEvidence(
        candidate_count=len(universe),
        completed_count=completed_count,
        failed_count=failed_count,
        eligible_count=eligible_count,
        selected_candidate_id=selected.candidate_id,
        selected_candidate_index=selected.candidate_index,
        selected_parameter_set_hash=selected.parameter_set_hash,
        selected_experiment_result_id=selected.experiment_result_id,
        selected_result_hash=selected.result_hash,
        selected_derived_strategy_version_id=selected.derived_strategy_version_id,
        selected_derived_strategy_content_hash=selected.derived_strategy_version_hash,
        objective_hash=experiment.objective_spec_hash,
        candidate_set_hash=selected.candidate_set_hash,
        parameter_space_hash=selected.parameter_space_hash,
        objective_evaluation_state=objective_evaluation.overall_constraint_state,
        compatibility_status=compatibility.status,
        compatibility_hash=compatibility_hash,
        is_start=selected.is_start,
        is_end=selected.is_end,
        backtest_configuration_hash=selected.backtest_configuration_hash,
        engine_version=selected.engine_version,
        analysis_version=selected.analysis_version,
        data_snapshot_reference=selected.data_snapshot_reference,
    )
    semantic = {
        "experiment_id": experiment.experiment_id,
        "protocol_id": experiment.protocol_id,
        "selected_candidate_id": selected.candidate_id,
        "selected_candidate_index": selected.candidate_index,
        "selected_parameter_set_hash": selected.parameter_set_hash,
        "selected_experiment_result_id": selected.experiment_result_id,
        "selected_result_hash": selected.result_hash,
        "selected_derived_strategy_version_id": selected.derived_strategy_version_id,
        "selected_derived_strategy_content_hash": selected.derived_strategy_version_hash,
        "objective_hash": experiment.objective_spec_hash,
        "candidate_set_hash": selected.candidate_set_hash,
        "parameter_space_hash": selected.parameter_space_hash,
        "selection_method": method.value,
        "researcher_rationale": researcher_rationale.strip(),
        "evidence": evidence.to_dict(),
    }
    selection_hash = sha256_hash(semantic)
    return ExperimentSelectionDecision(
        selection_id=f"selection-{selection_hash[:32]}",
        selection_hash=selection_hash,
        experiment_id=experiment.experiment_id,
        protocol_id=experiment.protocol_id,
        selected_candidate_id=selected.candidate_id,
        selected_candidate_index=selected.candidate_index,
        selected_parameter_set_hash=selected.parameter_set_hash,
        selected_experiment_result_id=selected.experiment_result_id,
        selected_result_hash=selected.result_hash,
        selected_derived_strategy_version_id=selected.derived_strategy_version_id,
        selected_derived_strategy_content_hash=selected.derived_strategy_version_hash,
        objective_hash=experiment.objective_spec_hash,
        candidate_set_hash=selected.candidate_set_hash,
        parameter_space_hash=selected.parameter_space_hash,
        selection_method=method,
        researcher_rationale=researcher_rationale,
        evidence=evidence,
        created_at=created_at,
    )


build_experiment_selection_decision = create_experiment_selection_decision


__all__ = [
    "ExperimentSelectionDecision",
    "ExperimentSelectionEvidence",
    "ExperimentSelectionMethod",
    "SelectionEligibility",
    "SelectionEligibilityReasonCode",
    "SelectionEligibilityStatus",
    "assess_selection_eligibility",
    "build_experiment_selection_decision",
    "create_experiment_selection_decision",
]
