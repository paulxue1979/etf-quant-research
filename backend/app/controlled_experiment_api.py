"""Local-browser-only decisions over Codex-prepared IS grid configurations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.controlled_experiment_repository import ControlledExperimentError
from backend.app.controlled_experiment_service import ControlledExperimentService

router = APIRouter(prefix="/experiment-operator/configurations", tags=["experiment-operator"])
_BROWSER_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def get_service() -> ControlledExperimentService:
    return ControlledExperimentService()


async def _browser_decision(request: Request) -> None:
    if request.headers.get("origin") not in _BROWSER_ORIGINS:
        raise HTTPException(status_code=403, detail="BROWSER_APPROVAL_REQUIRED")
    if await request.body():
        raise HTTPException(status_code=400, detail="CONFIG_ID_ONLY")


def _failure(exc: ControlledExperimentError) -> HTTPException:
    code = str(exc)
    status = 404 if code == "EXPERIMENT_CONFIG_NOT_FOUND" else 409
    if code in {"DATA_PREFLIGHT_BLOCKED", "EXPERIMENT_PREFLIGHT_BLOCKED"}:
        status = 422
    return HTTPException(status_code=status, detail={"code": code, "message": code})


@router.get("")
def list_configurations(
    service: ControlledExperimentService = Depends(get_service),
) -> list[dict[str, Any]]:
    return service.list()


@router.get("/{config_id}")
def get_configuration(
    config_id: str, service: ControlledExperimentService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.get(config_id)
    except ControlledExperimentError as exc:
        raise _failure(exc) from exc


@router.get("/{config_id}/result")
def get_configuration_result(
    config_id: str, service: ControlledExperimentService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.get_result(config_id)
    except ControlledExperimentError as exc:
        raise _failure(exc) from exc


@router.post("/{config_id}/reject", dependencies=[Depends(_browser_decision)])
def reject_configuration(
    config_id: str, service: ControlledExperimentService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.reject(config_id)
    except ControlledExperimentError as exc:
        raise _failure(exc) from exc


@router.post("/{config_id}/approve-run", dependencies=[Depends(_browser_decision)])
def approve_and_run_configuration(
    config_id: str, service: ControlledExperimentService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.approve_and_run(config_id)
    except ControlledExperimentError as exc:
        raise _failure(exc) from exc
