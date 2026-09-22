"""Strict read-only API for PHASE 11I optimization result analysis."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.app.experiment_repository import ExperimentRepository
from backend.app.optimization_research_repository import (
    OptimizationResearchPersistenceError,
    OptimizationResearchRepository,
)
from backend.app.optimization_research_service import (
    OptimizationResearchService,
    candidate_filter_from_dict,
)
from research.optimization_analysis import OptimizationAnalysisError


class ParameterFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact: Any | None = None
    minimum: float | None = None
    maximum: float | None = None


class CandidateFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: list[Literal["pending", "running", "completed", "failed"]] = Field(
        default_factory=list
    )
    candidate_ids: list[str] = Field(default_factory=list, max_length=1_000)
    minimum_trade_count: int | None = Field(default=None, ge=0)
    maximum_turnover: float | None = None
    maximum_drawdown_magnitude: float | None = Field(default=None, ge=0)
    minimum_cagr: float | None = None
    minimum_sharpe: float | None = None
    minimum_exposure: float | None = None
    maximum_exposure: float | None = None
    parameters: dict[str, ParameterFilterRequest] = Field(default_factory=dict)


class ResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter: CandidateFilterRequest = Field(default_factory=CandidateFilterRequest)
    metric_ids: list[str] = Field(default_factory=list, max_length=20)


class HeatmapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_parameter: str = Field(min_length=1, max_length=128)
    y_parameter: str = Field(min_length=1, max_length=128)
    metric_id: str = Field(min_length=1, max_length=128)
    fixed_parameter_values: dict[str, Any] = Field(default_factory=dict)
    filter: CandidateFilterRequest = Field(default_factory=CandidateFilterRequest)


class ParetoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_ids: list[str] = Field(min_length=2, max_length=5)
    filter: CandidateFilterRequest = Field(default_factory=CandidateFilterRequest)


class StabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    center_candidate_id: str = Field(min_length=1, max_length=128)
    metric_id: str = Field(min_length=1, max_length=128)
    filter: CandidateFilterRequest = Field(default_factory=CandidateFilterRequest)


class SensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(min_length=1, max_length=128)
    metric_ids: list[str] = Field(min_length=1, max_length=10)
    fixed_parameter_values: dict[str, Any] = Field(default_factory=dict)
    filter: CandidateFilterRequest = Field(default_factory=CandidateFilterRequest)


experiment_repository = ExperimentRepository()
optimization_repository = OptimizationResearchRepository(experiment_repository.db_path)
optimization_service = OptimizationResearchService(experiment_repository, optimization_repository)

router = APIRouter(prefix="/research", tags=["optimization-research"])


def _filter(request: CandidateFilterRequest):
    return candidate_filter_from_dict(request.model_dump())


def _call(operation):
    try:
        return operation()
    except OptimizationAnalysisError as exc:
        status_code = (
            404
            if exc.code == "EXPERIMENT_NOT_FOUND"
            else 409
            if exc.code == "EXPERIMENT_PROTOCOL_MISMATCH"
            else 422
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except OptimizationResearchPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "optimization research data is unavailable"},
        ) from exc


@router.get("/optimization/metrics")
def get_optimization_metric_registry() -> dict[str, Any]:
    return optimization_service.metric_registry()


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/optimization/results")
def get_optimization_results(
    protocol_id: str, experiment_id: str, request: ResultsRequest
) -> dict[str, Any]:
    return _call(
        lambda: optimization_service.results(
            protocol_id,
            experiment_id,
            _filter(request.filter),
            request.metric_ids or None,
        )
    )


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/optimization/heatmap")
def get_optimization_heatmap(
    protocol_id: str, experiment_id: str, request: HeatmapRequest
) -> dict[str, Any]:
    return _call(
        lambda: optimization_service.heatmap(
            protocol_id,
            experiment_id,
            x_parameter=request.x_parameter,
            y_parameter=request.y_parameter,
            metric_id=request.metric_id,
            fixed_parameter_values=request.fixed_parameter_values,
            candidate_filter=_filter(request.filter),
        )
    )


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/optimization/pareto")
def get_optimization_pareto(
    protocol_id: str, experiment_id: str, request: ParetoRequest
) -> dict[str, Any]:
    return _call(
        lambda: optimization_service.pareto(
            protocol_id,
            experiment_id,
            objective_ids=request.objective_ids,
            candidate_filter=_filter(request.filter),
        )
    )


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/optimization/stability")
def get_optimization_stability(
    protocol_id: str, experiment_id: str, request: StabilityRequest
) -> dict[str, Any]:
    return _call(
        lambda: optimization_service.stability(
            protocol_id,
            experiment_id,
            center_candidate_id=request.center_candidate_id,
            metric_id=request.metric_id,
            candidate_filter=_filter(request.filter),
        )
    )


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/optimization/sensitivity")
def get_optimization_sensitivity(
    protocol_id: str, experiment_id: str, request: SensitivityRequest
) -> dict[str, Any]:
    return _call(
        lambda: optimization_service.sensitivity(
            protocol_id,
            experiment_id,
            parameter=request.parameter,
            metric_ids=request.metric_ids,
            fixed_parameter_values=request.fixed_parameter_values,
            candidate_filter=_filter(request.filter),
        )
    )


__all__ = ["router"]
