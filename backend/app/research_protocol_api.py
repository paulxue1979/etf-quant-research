"""HTTP API for append-only OOS research protocol records."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository
from backend.app.research_protocol import (
    CandidateSet,
    OOSEvaluationRecord,
    OOSObservationStatus,
    ResearchPersistenceError,
    ResearchProtocol,
    ResearchProtocolError,
    ResearchProtocolRepository,
    SelectionDecision,
    StrategyFreezeRecord,
)
from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from strategies import StrategyVersion


class ProtocolCreateRequest(BaseModel):
    """Immutable protocol rules supplied before any OOS observation."""

    model_config = ConfigDict(extra="forbid")

    is_start_date: date
    is_end_date: date
    oos_start_date: date
    oos_end_date: date
    gap_days: int = Field(default=0, ge=0)
    embargo_days: int = Field(default=0, ge=0)
    selection_rules: list[str] = Field(default_factory=list)
    allowed_metrics: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(
        default_factory=lambda: ["oos_back_selection", "oos_parameter_tuning"]
    )
    data_policy: dict[str, Any] = Field(default_factory=dict)
    execution_policy: dict[str, Any] = Field(
        default_factory=lambda: {"execution_rule": "next_trading_day_open"}
    )
    evaluation_policy: dict[str, Any] = Field(
        default_factory=lambda: {"oos_selection_allowed": False}
    )
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("selection_rules", "allowed_metrics", "forbidden_actions")
    @classmethod
    def non_empty_values(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(normalized) != len(value) or len(set(normalized)) != len(normalized):
            raise ValueError("list values must be unique non-empty strings")
        return normalized


class CandidateSetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_ids: list[str] = Field(min_length=1)

    @field_validator("strategy_version_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() if isinstance(item, str) else "" for item in value]
        if not all(normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("strategy version ids must be unique non-empty strings")
        return normalized


class ProtocolTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "frozen", "is_evaluated", "selection_recorded", "oos_evaluated", "closed"
    ]


class SelectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_set_id: str = Field(min_length=1)
    selected_strategy_version_id: str = Field(min_length=1)
    is_backtest_run_ids: list[str] = Field(min_length=1)
    selected_metrics: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=10_000)
    data_provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_set_id", "selected_strategy_version_id", "rationale")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("is_backtest_run_ids")
    @classmethod
    def unique_run_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() if isinstance(item, str) else "" for item in value]
        if not all(normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("IS backtest run ids must be unique non-empty strings")
        return normalized


class FreezeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_decision_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=10_000)


class OOSEvaluationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    freeze_id: str = Field(min_length=1)
    backtest_run_id: str = Field(min_length=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: Literal["observed", "sealed"] = "observed"


class ResearchProtocolDetailResponse(BaseModel):
    protocol: dict[str, Any]
    candidate_sets: list[dict[str, Any]]
    selections: list[dict[str, Any]]
    freezes: list[dict[str, Any]]
    oos_evaluations: list[dict[str, Any]]
    data_provenance_notice: str


strategy_repository = StrategyRepository()
backtest_repository = BacktestRepository()
research_protocol_repository = ResearchProtocolRepository()
router = APIRouter(prefix="/research", tags=["research-protocol"])


def _protocol_detail(protocol_id: str) -> ResearchProtocolDetailResponse:
    protocol = research_protocol_repository.get_protocol(protocol_id)
    if protocol is None:
        raise HTTPException(status_code=404, detail="research protocol was not found")
    return ResearchProtocolDetailResponse(
        protocol=protocol.to_dict(),
        candidate_sets=[
            item.to_dict() for item in research_protocol_repository.list_candidate_sets(protocol_id)
        ],
        selections=[
            item.to_dict() for item in research_protocol_repository.list_selections(protocol_id)
        ],
        freezes=[item.to_dict() for item in research_protocol_repository.list_freezes(protocol_id)],
        oos_evaluations=[
            item.to_dict()
            for item in research_protocol_repository.list_oos_evaluations(protocol_id)
        ],
        data_provenance_notice=(
            "Data provenance is recorded from immutable backtest runs; complete dataset "
            "versioning is not implemented in this phase."
        ),
    )


def _find_version(version_id: str) -> StrategyVersion | None:
    """Resolve an immutable version without assuming a version-id naming convention."""
    for item in strategy_repository.catalog():
        strategy_id = item["strategy_id"]
        if not isinstance(strategy_id, str):
            continue
        for version in strategy_repository.list(strategy_id):
            if version.version_id == version_id:
                return version
    return None


def _require_version(version_id: str) -> StrategyVersion:
    version = _find_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="strategy version was not found")
    return version


def _require_run(run_id: str):
    run = backtest_repository.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run was not found")
    return run


def _domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchProtocolError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(
        exc, (ResearchPersistenceError, StrategyPersistenceError, BacktestPersistenceError)
    ):
        return HTTPException(status_code=503, detail="research protocol persistence is unavailable")
    return HTTPException(status_code=503, detail="research protocol service is unavailable")


@router.post(
    "/protocols",
    response_model=ResearchProtocolDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_protocol(request: ProtocolCreateRequest) -> ResearchProtocolDetailResponse:
    try:
        protocol = ResearchProtocol(
            protocol_id=f"protocol-{uuid4().hex}",
            protocol_version=1,
            created_at=datetime.now(UTC),
            is_start_date=request.is_start_date,
            is_end_date=request.is_end_date,
            oos_start_date=request.oos_start_date,
            oos_end_date=request.oos_end_date,
            gap_days=request.gap_days,
            embargo_days=request.embargo_days,
            selection_rules=tuple(request.selection_rules),
            allowed_metrics=tuple(request.allowed_metrics),
            forbidden_actions=tuple(request.forbidden_actions),
            data_policy=request.data_policy,
            execution_policy=request.execution_policy,
            evaluation_policy=request.evaluation_policy,
            provenance=request.provenance,
        )
        research_protocol_repository.create_protocol(protocol)
        return _protocol_detail(protocol.protocol_id)
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.get("/protocols", response_model=list[dict[str, Any]])
def list_protocols() -> list[dict[str, Any]]:
    try:
        return [protocol.to_dict() for protocol in research_protocol_repository.list_protocols()]
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.get("/protocols/{protocol_id}", response_model=ResearchProtocolDetailResponse)
def get_protocol(protocol_id: str) -> ResearchProtocolDetailResponse:
    try:
        return _protocol_detail(protocol_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post(
    "/protocols/{protocol_id}/candidate-sets",
    response_model=ResearchProtocolDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_candidate_set(
    protocol_id: str, request: CandidateSetCreateRequest
) -> ResearchProtocolDetailResponse:
    try:
        if research_protocol_repository.get_protocol(protocol_id) is None:
            raise HTTPException(status_code=404, detail="research protocol was not found")
        for version_id in request.strategy_version_ids:
            _require_version(version_id)
        candidate_set = CandidateSet(
            candidate_set_id=f"candidate-set-{uuid4().hex}",
            protocol_id=protocol_id,
            strategy_version_ids=tuple(request.strategy_version_ids),
            created_at=datetime.now(UTC),
        )
        research_protocol_repository.create_candidate_set(candidate_set)
        return _protocol_detail(protocol_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post(
    "/candidate-sets/{candidate_set_id}/lock", response_model=ResearchProtocolDetailResponse
)
def lock_candidate_set(candidate_set_id: str) -> ResearchProtocolDetailResponse:
    try:
        candidate_set = research_protocol_repository.lock_candidate_set(candidate_set_id)
        return _protocol_detail(candidate_set.protocol_id)
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post("/protocols/{protocol_id}/transitions", response_model=ResearchProtocolDetailResponse)
def transition_protocol(
    protocol_id: str, request: ProtocolTransitionRequest
) -> ResearchProtocolDetailResponse:
    try:
        research_protocol_repository.transition_protocol(protocol_id, request.status)
        return _protocol_detail(protocol_id)
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post(
    "/protocols/{protocol_id}/selection-decisions",
    response_model=ResearchProtocolDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_selection(
    protocol_id: str, request: SelectionCreateRequest
) -> ResearchProtocolDetailResponse:
    try:
        candidate_set = research_protocol_repository.get_candidate_set(request.candidate_set_id)
        if candidate_set is None or candidate_set.protocol_id != protocol_id:
            raise HTTPException(status_code=404, detail="candidate set was not found")
        _require_version(request.selected_strategy_version_id)
        runs = tuple(_require_run(run_id) for run_id in request.is_backtest_run_ids)
        decision = SelectionDecision(
            decision_id=f"selection-{uuid4().hex}",
            protocol_id=protocol_id,
            candidate_set_id=candidate_set.candidate_set_id,
            selected_strategy_version_id=request.selected_strategy_version_id,
            is_backtest_run_ids=tuple(request.is_backtest_run_ids),
            selected_metrics=request.selected_metrics,
            rationale=request.rationale,
            created_at=datetime.now(UTC),
            data_provenance={"split": "is", **request.data_provenance},
        )
        research_protocol_repository.create_selection(
            decision, candidate_set=candidate_set, runs=runs
        )
        return _protocol_detail(protocol_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post(
    "/protocols/{protocol_id}/freeze",
    response_model=ResearchProtocolDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_freeze(protocol_id: str, request: FreezeCreateRequest) -> ResearchProtocolDetailResponse:
    try:
        decision = research_protocol_repository.get_selection(request.selection_decision_id)
        if decision is None or decision.protocol_id != protocol_id:
            raise HTTPException(status_code=404, detail="selection decision was not found")
        version = _require_version(decision.selected_strategy_version_id)
        freeze = StrategyFreezeRecord(
            freeze_id=f"freeze-{uuid4().hex}",
            protocol_id=protocol_id,
            strategy_version_id=version.version_id,
            strategy_version_content_hash=version.content_hash or "",
            selection_decision_id=decision.decision_id,
            frozen_at=datetime.now(UTC),
            reason=request.reason.strip(),
        )
        research_protocol_repository.create_freeze(freeze, decision=decision, version=version)
        return _protocol_detail(protocol_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.post(
    "/protocols/{protocol_id}/oos-evaluations",
    response_model=ResearchProtocolDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_oos_evaluation(
    protocol_id: str, request: OOSEvaluationCreateRequest
) -> ResearchProtocolDetailResponse:
    try:
        freeze = research_protocol_repository.get_freeze(request.freeze_id)
        if freeze is None or freeze.protocol_id != protocol_id:
            raise HTTPException(status_code=404, detail="strategy freeze was not found")
        run = _require_run(request.backtest_run_id)
        evaluation = OOSEvaluationRecord(
            evaluation_id=f"oos-{uuid4().hex}",
            protocol_id=protocol_id,
            freeze_id=freeze.freeze_id,
            strategy_version_id=freeze.strategy_version_id,
            backtest_run_id=run.backtest_run_id,
            status=(
                OOSObservationStatus.SEALED
                if request.status == OOSObservationStatus.SEALED
                else OOSObservationStatus.OBSERVED
            ),
            observed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            provenance={"split": "oos", "selection_allowed": False, **request.provenance},
        )
        research_protocol_repository.create_oos_evaluation(evaluation, freeze=freeze, run=run)
        return _protocol_detail(protocol_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


@router.get("/protocols/{protocol_id}/oos-evaluations", response_model=list[dict[str, Any]])
def list_oos_evaluations(protocol_id: str) -> list[dict[str, Any]]:
    try:
        if research_protocol_repository.get_protocol(protocol_id) is None:
            raise HTTPException(status_code=404, detail="research protocol was not found")
        return [
            item.to_dict()
            for item in research_protocol_repository.list_oos_evaluations(protocol_id)
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise _domain_error(exc) from exc


__all__ = [
    "backtest_repository",
    "research_protocol_repository",
    "router",
    "strategy_repository",
]
