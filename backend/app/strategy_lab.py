"""Strategy Lab validation and durable strategy-version API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.strategy_repository import (
    StrategyDraft,
    StrategyPersistenceError,
    StrategyRecord,
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


class DraftModeRequest(BaseModel):
    """Typed mode envelope; nested editor fields remain lossless while incomplete."""

    assets: list[str] = Field(default_factory=list)
    editor_state: dict[str, Any] = Field(default_factory=dict)
    source_payload: dict[str, Any] | None = None


class StrategyDraftRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    active_mode: str
    strategy_id: str
    name: str
    description: str
    price_field: str
    rebalance_frequency: str
    rebalance_threshold_percent: str
    rule_based_draft: DraftModeRequest
    regime_draft: DraftModeRequest
    active_strategy_payload: dict[str, Any] | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyRecordResponse(BaseModel):
    strategy_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    status: str
    metadata: dict[str, Any]


class StrategyDraftResponse(BaseModel):
    strategy_id: str
    draft_revision: int
    active_mode: str
    name: str
    description: str
    price_field: str
    rebalance_frequency: str
    rebalance_threshold_percent: str
    rule_based_draft: dict[str, Any]
    regime_draft: dict[str, Any]
    active_strategy_payload: dict[str, Any] | None
    source_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    draft_hash: str


class CreateStrategyRequest(BaseModel):
    strategy_id: str
    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateVersionFromDraftRequest(BaseModel):
    expected_revision: int = Field(ge=1)


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


def _strategy_response(strategy: StrategyRecord) -> StrategyRecordResponse:
    return StrategyRecordResponse(
        strategy_id=strategy.strategy_id,
        name=strategy.name,
        description=strategy.description,
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
        status=strategy.status,
        metadata=dict(strategy.metadata),
    )


def _draft_response(draft: StrategyDraft, strategy: StrategyRecord) -> StrategyDraftResponse:
    return StrategyDraftResponse(
        strategy_id=draft.strategy_id,
        draft_revision=draft.draft_revision,
        active_mode=draft.active_mode,
        name=strategy.name,
        description=strategy.description,
        price_field=str(draft.source_metadata.get("price_field", "adjusted_close")),
        rebalance_frequency=str(draft.source_metadata.get("rebalance_frequency", "daily")),
        rebalance_threshold_percent=str(
            draft.source_metadata.get("rebalance_threshold_percent", "")
        ),
        rule_based_draft=dict(draft.rule_based_draft),
        regime_draft=dict(draft.regime_draft),
        active_strategy_payload=(
            None if draft.active_strategy_payload is None else dict(draft.active_strategy_payload)
        ),
        source_metadata=dict(draft.source_metadata),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        draft_hash=draft.draft_hash,
    )


strategy_version_store = StrategyVersionStore()
strategy_validator = StrategyValidator()
router = APIRouter(prefix="/strategy-lab", tags=["strategy-lab"])


@router.post(
    "/strategies", response_model=StrategyRecordResponse, status_code=status.HTTP_201_CREATED
)
def create_strategy_identity(request: CreateStrategyRequest) -> StrategyRecordResponse:
    try:
        return _strategy_response(
            strategy_version_store.create_strategy(
                request.strategy_id,
                request.name,
                request.description,
                request.metadata,
            )
        )
    except StrategyPersistenceError as exc:
        if exc.code == "STRATEGY_ALREADY_EXISTS":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.code
        ) from exc


@router.get("/strategies", response_model=list[StrategyRecordResponse])
def list_strategy_identities() -> list[StrategyRecordResponse]:
    try:
        return [_strategy_response(item) for item in strategy_version_store.list_strategies()]
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.code
        ) from exc


@router.get("/strategies/{strategy_id}", response_model=StrategyRecordResponse)
def get_strategy_identity(strategy_id: str) -> StrategyRecordResponse:
    try:
        strategy = strategy_version_store.get_strategy(strategy_id)
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.code
        ) from exc
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="STRATEGY_NOT_FOUND")
    return _strategy_response(strategy)


@router.get("/strategies/{strategy_id}/draft", response_model=StrategyDraftResponse)
def get_strategy_draft(strategy_id: str) -> StrategyDraftResponse:
    try:
        strategy = strategy_version_store.get_strategy(strategy_id)
        draft = strategy_version_store.get_draft(strategy_id)
    except StrategyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.code
        ) from exc
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="STRATEGY_NOT_FOUND")
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DRAFT_NOT_FOUND")
    return _draft_response(draft, strategy)


@router.put("/strategies/{strategy_id}/draft", response_model=StrategyDraftResponse)
def save_strategy_draft(strategy_id: str, request: StrategyDraftRequest) -> StrategyDraftResponse:
    if request.strategy_id != strategy_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="DRAFT_INVALID"
        )
    metadata = {
        **request.source_metadata,
        "price_field": request.price_field,
        "rebalance_frequency": request.rebalance_frequency,
        "rebalance_threshold_percent": request.rebalance_threshold_percent,
    }
    try:
        draft = strategy_version_store.save_draft(
            strategy_id=strategy_id,
            expected_revision=request.expected_revision,
            active_mode=request.active_mode,
            rule_based_draft={
                **request.rule_based_draft.model_dump(exclude_none=True),
                "editor_state": request.rule_based_draft.editor_state,
            },
            regime_draft={
                **request.regime_draft.model_dump(exclude_none=True),
                "editor_state": request.regime_draft.editor_state,
            },
            active_strategy_payload=request.active_strategy_payload,
            source_metadata=metadata,
            name=request.name,
            description=request.description,
        )
        strategy = strategy_version_store.get_strategy(strategy_id)
        assert strategy is not None
        return _draft_response(draft, strategy)
    except StrategyPersistenceError as exc:
        code_to_status = {
            "DRAFT_REVISION_CONFLICT": status.HTTP_409_CONFLICT,
            "STRATEGY_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "DRAFT_INVALID": status.HTTP_422_UNPROCESSABLE_CONTENT,
        }
        raise HTTPException(
            status_code=code_to_status.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=exc.code,
        ) from exc


@router.post(
    "/strategies/{strategy_id}/versions/from-draft",
    response_model=StrategyVersionResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": StrategyValidationResponse}},
)
def create_strategy_version_from_draft(
    strategy_id: str, request: CreateVersionFromDraftRequest
) -> StrategyVersionResponse | JSONResponse:
    try:
        draft = strategy_version_store.get_draft(strategy_id)
        if draft is None:
            raise StrategyPersistenceError("strategy draft was not found", code="DRAFT_NOT_FOUND")
        if draft.draft_revision != request.expected_revision:
            raise StrategyPersistenceError(
                "draft revision is stale", code="DRAFT_REVISION_CONFLICT"
            )
        if draft.active_strategy_payload is None:
            raise StrategyPersistenceError("active draft is invalid", code="DRAFT_INVALID")
        validation = strategy_validator.validate(draft.active_strategy_payload)
        if not validation.is_valid:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content=validation.to_dict(),
            )
        definition = StrategyDefinition.from_dict(draft.active_strategy_payload)
        version = strategy_version_store.create_version_from_draft(
            strategy_id=strategy_id,
            expected_revision=request.expected_revision,
            definition=definition,
        )
        return _version_response(version)
    except StrategyPersistenceError as exc:
        code_to_status = {
            "DRAFT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "DRAFT_REVISION_CONFLICT": status.HTTP_409_CONFLICT,
            "DRAFT_INVALID": status.HTTP_422_UNPROCESSABLE_CONTENT,
        }
        raise HTTPException(
            status_code=code_to_status.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=exc.code,
        ) from exc


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
