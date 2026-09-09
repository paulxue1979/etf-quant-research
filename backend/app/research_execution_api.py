"""HTTP endpoints for one-candidate PHASE 8D-5 execution and recovery."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, status

from backend.app.experiment_execution_orchestrator import (
    ExperimentExecutionApiError,
    ExperimentExecutionOrchestrator,
    create_default_experiment_execution_orchestrator,
)

router = APIRouter(prefix="/research", tags=["experiment-execution"])
_orchestrator: ExperimentExecutionOrchestrator | None = None


def _service() -> ExperimentExecutionOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = create_default_experiment_execution_orchestrator()
    return _orchestrator


def _handle(exc: ExperimentExecutionApiError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post(
    "/experiments/{experiment_id}/candidates/{candidate_id}/execute",
    status_code=status.HTTP_200_OK,
)
def execute_candidate(
    experiment_id: str,
    candidate_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    if body not in (None, {}):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REQUEST_MUST_BE_EMPTY",
                "message": "candidate execution does not accept configuration overrides",
            },
        )
    try:
        return _service().execute(experiment_id, candidate_id)
    except ExperimentExecutionApiError as exc:
        raise _handle(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EXECUTION_SERVICE_UNAVAILABLE",
                "message": "experiment execution service is unavailable",
            },
        ) from exc


@router.get("/experiments/{experiment_id}/candidates/{candidate_id}/execution")
def get_candidate_execution(experiment_id: str, candidate_id: str) -> dict[str, Any]:
    try:
        return _service().get_execution(experiment_id, candidate_id)
    except ExperimentExecutionApiError as exc:
        raise _handle(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EXECUTION_SERVICE_UNAVAILABLE",
                "message": "experiment execution service is unavailable",
            },
        ) from exc


@router.get("/experiments/{experiment_id}/candidates/{candidate_id}/result")
def get_candidate_result(experiment_id: str, candidate_id: str) -> dict[str, Any]:
    try:
        return _service().get_result(experiment_id, candidate_id)
    except ExperimentExecutionApiError as exc:
        raise _handle(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EXECUTION_SERVICE_UNAVAILABLE",
                "message": "experiment execution service is unavailable",
            },
        ) from exc


__all__ = ["router"]
