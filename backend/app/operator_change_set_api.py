"""Browser-facing, local-only decisions over persisted draft proposals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.operator_change_set_apply_service import (
    ChangeSetDecisionError,
    OperatorChangeSetApplyService,
)
from backend.app.operator_change_set_repository import ChangeSetStorageError

router = APIRouter(
    prefix="/strategy-lab/strategies/{strategy_id}/change-sets", tags=["strategy-lab"]
)
_BROWSER_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
_CONFLICTS = {
    "CHANGESET_STALE",
    "ALREADY_APPLIED",
    "ALREADY_REJECTED",
    "DRAFT_NOT_EDITABLE",
    "CHANGESET_FAILED",
}


def get_apply_service() -> OperatorChangeSetApplyService:
    return OperatorChangeSetApplyService()


async def _browser_origin(request: Request) -> None:
    if request.headers.get("origin") not in _BROWSER_ORIGINS:
        raise HTTPException(status_code=403, detail="BROWSER_APPROVAL_REQUIRED")
    if await request.body():
        raise HTTPException(status_code=400, detail="CHANGESET_ID_ONLY")


def _failure(exc: Exception) -> HTTPException:
    code = str(exc) if isinstance(exc, ChangeSetDecisionError) else "CHANGESET_STORAGE_ERROR"
    if code == "CHANGESET_NOT_FOUND":
        status = 404
    elif code in _CONFLICTS:
        status = 409
    elif code in {"CHANGESET_VALIDATION_FAILED", "CHANGESET_OPERATION_RESOLUTION_FAILED"}:
        status = 422
    else:
        status = 503
    return HTTPException(status_code=status, detail=code)


@router.get("")
def list_change_sets(
    strategy_id: str, service: OperatorChangeSetApplyService = Depends(get_apply_service)
) -> list[dict[str, Any]]:
    try:
        return service.list_for_strategy(strategy_id)
    except (ChangeSetDecisionError, ChangeSetStorageError) as exc:
        raise _failure(exc) from exc


@router.get("/{change_set_id}")
def get_change_set(
    strategy_id: str,
    change_set_id: str,
    service: OperatorChangeSetApplyService = Depends(get_apply_service),
) -> dict[str, Any]:
    try:
        return service.get(strategy_id, change_set_id)
    except (ChangeSetDecisionError, ChangeSetStorageError) as exc:
        raise _failure(exc) from exc


@router.post("/{change_set_id}/approve", dependencies=[Depends(_browser_origin)])
def approve_change_set(
    strategy_id: str,
    change_set_id: str,
    service: OperatorChangeSetApplyService = Depends(get_apply_service),
) -> dict[str, Any]:
    try:
        return service.approve(strategy_id, change_set_id)
    except (ChangeSetDecisionError, ChangeSetStorageError) as exc:
        raise _failure(exc) from exc


@router.post("/{change_set_id}/reject", dependencies=[Depends(_browser_origin)])
def reject_change_set(
    strategy_id: str,
    change_set_id: str,
    service: OperatorChangeSetApplyService = Depends(get_apply_service),
) -> dict[str, Any]:
    try:
        return service.reject(strategy_id, change_set_id)
    except (ChangeSetDecisionError, ChangeSetStorageError) as exc:
        raise _failure(exc) from exc
