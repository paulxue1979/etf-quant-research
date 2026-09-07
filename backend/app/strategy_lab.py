"""Minimal PHASE 5A adapter for strategy validation and version snapshots.

The store is deliberately process-local. It exists only to provide a usable
Strategy Lab save/history loop until a later phase introduces durable storage.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from strategies import StrategyDefinition, StrategyStatus, StrategyValidator, StrategyVersion


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


class StrategyVersionStore:
    """Thread-safe, process-local store of immutable StrategyVersion snapshots."""

    def __init__(self) -> None:
        self._versions: dict[str, list[StrategyVersion]] = {}
        self._lock = Lock()

    def create(self, definition: StrategyDefinition) -> StrategyVersion:
        """Create a new immutable snapshot without overwriting prior versions."""
        with self._lock:
            versions = self._versions.setdefault(definition.strategy_id, [])
            version_number = len(versions) + 1
            version = StrategyVersion(
                strategy_id=definition.strategy_id,
                version_id=f"{definition.strategy_id}-v{version_number}-{uuid4().hex[:12]}",
                version_number=version_number,
                created_at=datetime.now(UTC),
                configuration=definition,
                status=StrategyStatus.DRAFT,
            )
            versions.append(version)
            return version

    def list(self, strategy_id: str) -> tuple[StrategyVersion, ...]:
        """Return immutable snapshots in creation order for one strategy."""
        with self._lock:
            return tuple(self._versions.get(strategy_id, ()))

    def get(self, strategy_id: str, version_id: str) -> StrategyVersion | None:
        """Find one immutable version snapshot by identity."""
        with self._lock:
            return next(
                (
                    version
                    for version in self._versions.get(strategy_id, ())
                    if version.version_id == version_id
                ),
                None,
            )

    def clear(self) -> None:
        """Clear process-local state for isolated tests only."""
        with self._lock:
            self._versions.clear()


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
    return _version_response(strategy_version_store.create(definition))


@router.get(
    "/strategies/{strategy_id}/versions",
    response_model=list[StrategyVersionSummaryResponse],
)
def list_strategy_versions(strategy_id: str) -> list[StrategyVersionSummaryResponse]:
    """List process-local immutable version metadata for one strategy."""
    versions = strategy_version_store.list(strategy_id)
    return [_version_summary_response(version) for version in versions]


@router.get(
    "/strategies/{strategy_id}/versions/{version_id}",
    response_model=StrategyVersionResponse,
)
def get_strategy_version(strategy_id: str, version_id: str) -> StrategyVersionResponse:
    """Load one process-local immutable configuration snapshot."""
    version = strategy_version_store.get(strategy_id, version_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="strategy version was not found",
        )
    return _version_response(version)
