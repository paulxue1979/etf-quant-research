"""Browser-only decisions on Codex-prepared StrategyVersion requests."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.controlled_version_repository import VersionRequestError
from backend.app.controlled_version_service import ControlledVersionService

router = APIRouter(prefix="/strategy-operator/version-requests", tags=["strategy-operator"])
_BROWSER_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def get_service() -> ControlledVersionService:
    return ControlledVersionService()


async def _browser_decision(request: Request) -> None:
    if request.headers.get("origin") not in _BROWSER_ORIGINS:
        raise HTTPException(status_code=403, detail="BROWSER_APPROVAL_REQUIRED")
    if await request.body():
        raise HTTPException(status_code=400, detail="REQUEST_ID_ONLY")


def _failure(exc: VersionRequestError) -> HTTPException:
    code = str(exc)
    if code in {"VERSION_REQUEST_NOT_FOUND", "STRATEGY_NOT_FOUND", "DRAFT_NOT_FOUND"}:
        status = 404
    elif code in {
        "DRAFT_STALE",
        "VERSION_BASE_STALE",
        "VERSION_REQUEST_NOT_PROPOSED",
        "STRATEGY_INACTIVE",
        "CREATED_VERSION_INTEGRITY_ERROR",
    }:
        status = 409
    elif code.startswith("INVALID_") or code == "DRAFT_INVALID":
        status = 422
    else:
        status = 503
    return HTTPException(status_code=status, detail={"code": code, "message": code})


@router.get("/strategies/{strategy_id}")
def list_requests(
    strategy_id: str, service: ControlledVersionService = Depends(get_service)
) -> list[dict[str, Any]]:
    try:
        return service.list(strategy_id)
    except VersionRequestError as exc:
        raise _failure(exc) from exc


@router.get("/strategies/{strategy_id}/{request_id}")
def get_request(
    strategy_id: str,
    request_id: str,
    service: ControlledVersionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.get_for_strategy(strategy_id, request_id)
    except VersionRequestError as exc:
        raise _failure(exc) from exc


@router.post(
    "/strategies/{strategy_id}/{request_id}/reject",
    dependencies=[Depends(_browser_decision)],
)
def reject_request(
    strategy_id: str,
    request_id: str,
    service: ControlledVersionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        return service.reject(strategy_id, request_id)
    except VersionRequestError as exc:
        raise _failure(exc) from exc


@router.post(
    "/strategies/{strategy_id}/{request_id}/approve",
    dependencies=[Depends(_browser_decision)],
)
def approve_request(
    strategy_id: str,
    request_id: str,
    service: ControlledVersionService = Depends(get_service),
) -> dict[str, Any]:
    try:
        result = service.approve(strategy_id, request_id)
        if result["status"] != "CREATED":
            raise VersionRequestError(result["error_code"] or "VERSION_REQUEST_NOT_PROPOSED")
        return result
    except VersionRequestError as exc:
        raise _failure(exc) from exc


__all__ = ["router"]
