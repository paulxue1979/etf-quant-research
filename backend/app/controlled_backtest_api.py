"""Browser-only decisions over Codex-prepared backtest configurations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.controlled_backtest_repository import ControlledBacktestStorageError
from backend.app.controlled_backtest_service import (
    ControlledBacktestError,
    ControlledBacktestService,
)

router = APIRouter(prefix="/backtest-operator/configurations", tags=["backtest-operator"])
_BROWSER_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def get_service() -> ControlledBacktestService:
    return ControlledBacktestService()


async def _browser_approval(request: Request) -> None:
    if request.headers.get("origin") not in _BROWSER_ORIGINS:
        raise HTTPException(status_code=403, detail="BROWSER_APPROVAL_REQUIRED")
    if await request.body():
        raise HTTPException(status_code=400, detail="CONFIG_ID_ONLY")


def _failure(exc: Exception) -> HTTPException:
    code = str(exc)
    if code == "PLAN_NOT_FOUND":
        status = 404
    elif code in (
        "PLAN_ALREADY_EXECUTED",
        "PLAN_NOT_PROPOSED",
        "PLAN_VERSION_MISMATCH",
        "PLAN_INTEGRITY_ERROR",
    ):
        status = 409
    elif code.startswith("PLAN_"):
        status = 422
    else:
        status = 503
    return HTTPException(
        status_code=status, detail={"code": code, "message": code.replace("_", " ").title()}
    )


@router.get("")
def list_configurations(
    service: ControlledBacktestService = Depends(get_service),
) -> list[dict[str, Any]]:
    try:
        return service.list()
    except (ControlledBacktestError, ControlledBacktestStorageError) as exc:
        raise _failure(exc) from exc


@router.get("/{config_id}")
def get_configuration(
    config_id: str, service: ControlledBacktestService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.get(config_id)
    except (ControlledBacktestError, ControlledBacktestStorageError) as exc:
        raise _failure(exc) from exc


@router.get("/{config_id}/result")
def get_configuration_result(
    config_id: str, service: ControlledBacktestService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.get_result(config_id)
    except (ControlledBacktestError, ControlledBacktestStorageError) as exc:
        raise _failure(exc) from exc


@router.post("/{config_id}/reject", dependencies=[Depends(_browser_approval)])
def reject_configuration(
    config_id: str, service: ControlledBacktestService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.reject(config_id)
    except (ControlledBacktestError, ControlledBacktestStorageError) as exc:
        raise _failure(exc) from exc


@router.post("/{config_id}/approve-run", dependencies=[Depends(_browser_approval)])
def approve_and_run_configuration(
    config_id: str, service: ControlledBacktestService = Depends(get_service)
) -> dict[str, Any]:
    try:
        return service.approve_and_run(config_id)
    except (ControlledBacktestError, ControlledBacktestStorageError) as exc:
        raise _failure(exc) from exc


__all__ = ["router"]
