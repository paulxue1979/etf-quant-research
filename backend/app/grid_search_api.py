"""PHASE 11H preflight, explicit execution, cancellation, and progress API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from data.cache import DiskCache
from data.config import TiingoSettings
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from research.execution_service import ExperimentExecutionService
from research.grid_search import GridSearchDefinition
from research.grid_search_service import GridSearchService, GridSearchServiceError
from research.result_finalization_service import ExperimentResultFinalizationService

router = APIRouter(prefix="/research/experiments", tags=["grid-search"])
_grid_service: GridSearchService | None = None


def _service() -> GridSearchService:
    global _grid_service
    if _grid_service is None:
        database = Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        experiments = ExperimentRepository(database)
        strategies = StrategyRepository(database)
        executions = CandidateExecutionRepository(database)
        results = ExperimentResultRepository(database)
        backtests = BacktestRepository(database)
        protocols = ResearchProtocolRepository(database)
        data_service = HistoricalDataService(
            TiingoClient(TiingoSettings.from_environment()), DiskCache()
        )
        executor = ExperimentExecutionService(
            experiment_repository=experiments,
            protocol_repository=protocols,
            strategy_repository=strategies,
            candidate_execution_repository=executions,
            data_service=data_service,
        )
        finalizer = ExperimentResultFinalizationService(
            backtest_repository=backtests,
            experiment_result_repository=results,
            candidate_execution_repository=executions,
            experiment_repository=experiments,
        )
        _grid_service = GridSearchService(
            experiment_repository=experiments,
            strategy_repository=strategies,
            execution_repository=executions,
            result_repository=results,
            grid_repository=GridSearchRepository(database),
            execution_service=executor,
            finalization_service=finalizer,
        )
    return _grid_service


def _definition(experiment_id: str, payload: dict[str, Any]) -> GridSearchDefinition:
    if payload.get("experiment_id") not in (None, experiment_id):
        raise HTTPException(
            status_code=422,
            detail={"code": "EXPERIMENT_ID_MISMATCH", "message": "path and body disagree"},
        )
    try:
        return GridSearchDefinition.from_dict({**payload, "experiment_id": experiment_id})
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_GRID_DEFINITION", "message": str(exc)},
        ) from exc


def _error(exc: GridSearchServiceError) -> HTTPException:
    code = 404 if exc.code in {"EXPERIMENT_NOT_FOUND", "STRATEGY_VERSION_NOT_FOUND"} else 409
    if exc.code in {"GRID_PREFLIGHT_INVALID", "GRID_NOT_PREPARED"}:
        code = 422
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


@router.post("/{experiment_id}/grid/preflight")
def preflight_grid(experiment_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return _service().preflight(experiment_id, _definition(experiment_id, body)).to_dict()
    except GridSearchServiceError as exc:
        raise _error(exc) from exc


@router.get("/{experiment_id}/grid/template")
def grid_template(experiment_id: str) -> dict[str, Any]:
    try:
        return _service().template(experiment_id)
    except GridSearchServiceError as exc:
        raise _error(exc) from exc


@router.post("/{experiment_id}/grid/prepare")
def prepare_grid(experiment_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return _service().prepare(experiment_id, _definition(experiment_id, body))
    except GridSearchServiceError as exc:
        raise _error(exc) from exc


@router.post("/{experiment_id}/grid/execute", status_code=status.HTTP_200_OK)
def execute_grid(
    experiment_id: str, body: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    if body not in (None, {}):
        raise HTTPException(
            status_code=422,
            detail={"code": "REQUEST_MUST_BE_EMPTY", "message": "execution has no overrides"},
        )
    try:
        return _service().execute(experiment_id)
    except GridSearchServiceError as exc:
        raise _error(exc) from exc


@router.post("/{experiment_id}/grid/cancel", status_code=status.HTTP_200_OK)
def cancel_grid(
    experiment_id: str, body: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    if body not in (None, {}):
        raise HTTPException(
            status_code=422,
            detail={"code": "REQUEST_MUST_BE_EMPTY", "message": "cancellation has no overrides"},
        )
    try:
        return _service().cancel(experiment_id)
    except GridSearchServiceError as exc:
        raise _error(exc) from exc


@router.get("/{experiment_id}/grid/progress")
def grid_progress(experiment_id: str) -> dict[str, Any]:
    try:
        return _service().progress(experiment_id)
    except GridSearchServiceError as exc:
        raise _error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "GRID_NOT_FOUND", "message": "grid progress was not found"},
        ) from exc


__all__ = ["router"]
