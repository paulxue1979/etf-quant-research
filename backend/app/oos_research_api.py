"""Read-only API for finalized official OOS research views."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.oos_research_view import (
    OosResearchViewError,
    OosResearchViewService,
    create_default_oos_research_view_service,
)

router = APIRouter(prefix="/research", tags=["oos-research"])
_service_instance: OosResearchViewService | None = None


def _service() -> OosResearchViewService:
    global _service_instance
    if _service_instance is None:
        _service_instance = create_default_oos_research_view_service()
    return _service_instance


@router.get("/protocols/{protocol_id}/oos")
def get_official_oos_research_view(protocol_id: str) -> dict[str, Any]:
    try:
        return _service().get(protocol_id)
    except OosResearchViewError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "OOS_RESEARCH_SERVICE_UNAVAILABLE",
                "message": "official OOS research view is unavailable",
            },
        ) from exc


__all__ = ["router"]
