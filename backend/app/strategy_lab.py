"""Strategy Lab validation and durable strategy-version API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.strategy_repository import (
    StrategyPersistenceError,
    StrategyVersionStore,
)
from strategies import StrategyDefinition, StrategyValidator, StrategyVersion


class ValidationIssueResponse(BaseModel):
    """One structured domain-validation issue exposed to the Strategy Lab."""

    code: str
    path: str
    message: str


class StrategyValidationResponse(BaseModel):
    """Public validation result without leaking domain implementation details."""

    is_valid: bool
    errors: list[ValidationIssueResponse]
    warnings: list[ValidationIssueResponse]


class StrategyPayloadRequest(BaseModel):
    """Wrap the JSON-compatible strategy definition submitted by the UI."""

    strategy: dict[str, Any] = Field(..., description="StrategyDefinition wire payload")


class StrategyVersionSummaryResponse(BaseModel):
    """Compact immutable-version metadata used by the version-history UI."""

    strategy_id: str
    version_id: str
    version_number: int
    created_at: datetime
    content_hash: str
    status: str


class StrategyVersionResponse(StrategyVersionSummaryResponse):
    """An immutable version snapshot plus its serialized configuration."""

    configuration: dict[str, Any]


def _validation_response(payload: Mapping[str, Any]) -> StrategyValidationResponse:
    return StrategyValidationResponse.model_validate(payload)


def _version_response(version: StrategyVersion) -> StrategyVersionResponse:
    payload = version.to_dict()
    return StrategyVersionResponse(
        strategy_id=version.strategy_id,
        version_id=version.version_id,
        version_number=version.version_number,
        created_at=version.created_at,
        content_hash=version.content_hash or "",
        status=version.status.value,
        configuration=payload["configuration"],
    )


def _version_summary_response(version: StrategyVersion) -> StrategyVersionSummaryResponse:
    return StrategyVersionSummaryResponse(
        strategy_id=version.strategy_id,
        version_id=version.version_id,
        version_number=version.version_number,
        created_at=version.created_at,
        content_hash=version.content_hash or "",
        status=version.status.value,
    )


strategy_version_store = StrategyVersionStore()
strategy_validator = StrategyValidator()
router = APIRouter(prefix="/strategy-lab", tags=["strategy-lab"])


@router.post("/validate", response_model=StrategyValidationResponse)
def validate_strategy_payload(request: StrategyPayloadRequest) -> StrategyValidationResponse:
    """Delegate Strategy Lab validation to the existing domain validator."""
    return _validation_response(strategy_validator.validate(request.strategy).to_dict())


@router.post(
    "/versions",
    response_model=StrategyVersionResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": StrategyValidationResponse}},
)
def create_strategy_version(
    request: StrategyPayloadRequest,
) -> StrategyVersionResponse | JSONResponse:
    """Validate and snapshot a strategy configuration as a new immutable version."""
    validation = strategy_validator.validate(request.strategy)
    if not validation.is_valid:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=validation.to_dict(),
        )
    definition = StrategyDefinition.from_dict(request.strategy)
    try:
        return _version_response(strategy_version_store.create(definition))
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="strategy persistence failed",
        ) from exc


@router.get(
    "/strategies/{strategy_id}/versions",
    response_model=list[StrategyVersionSummaryResponse],
)
def list_strategy_versions(strategy_id: str) -> list[StrategyVersionSummaryResponse]:
    """List durable immutable version metadata for one strategy."""
    try:
        versions = strategy_version_store.list(strategy_id)
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="strategy persistence failed",
        ) from exc
    return [_version_summary_response(version) for version in versions]


@router.get(
    "/strategies/{strategy_id}/versions/{version_id}",
    response_model=StrategyVersionResponse,
)
def get_strategy_version(strategy_id: str, version_id: str) -> StrategyVersionResponse:
    """Load one durable immutable configuration snapshot."""
    try:
        version = strategy_version_store.get(strategy_id, version_id)
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="strategy persistence failed",
        ) from exc
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="strategy version was not found",
        )
    return _version_response(version)
