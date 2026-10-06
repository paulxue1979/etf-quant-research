"""Controlled PHASE 13K handoff from a frozen selection to official OOS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.app.backtest_repository import BacktestRepository
from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.oos_result_repository import OosResultRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from data.cache import DiskCache
from data.config import TiingoSettings
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from indicators.models import IndicatorHistoryRequirement
from research.data_availability import (
    history_requirement_for_strategy,
    warmup_start_for_strategy,
)
from research.oos import (
    OosDataProvenance,
    OosEvaluationConfig,
    OosEvaluationIdentity,
    OosEvaluationRange,
    OosEvaluationSpec,
    validate_oos_preconditions,
    validate_oos_range,
)
from research.oos_execution import OosExecutionStatus
from research.oos_execution_service import ANALYTICS_VERSION, OosExecutionService
from research.oos_finalization_service import OosFinalizationService

router = APIRouter(prefix="/research", tags=["official-oos-workflow"])
_database = Path(__file__).resolve().parents[2] / "data" / "strategy.db"
_protocols = ResearchProtocolRepository(_database)
_strategies = StrategyRepository(_database)
_backtests = BacktestRepository(_database)
_executions = OosExecutionRepository(_database)
_oos_results = OosResultRepository(_database)


def _error(code: str, message: str, status_code: int = 409) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _governance(protocol_id: str) -> tuple[Any, Any, Any]:
    protocol = _protocols.get_protocol(protocol_id)
    if protocol is None:
        raise _error("PROTOCOL_NOT_FOUND", "research protocol was not found", 404)
    selections = _protocols.list_selections(protocol_id)
    freezes = _protocols.list_freezes(protocol_id)
    if len(selections) != 1 or len(freezes) != 1:
        raise _error("OOS_NOT_FROZEN", "official OOS requires exactly one frozen selection")
    selection = selections[0]
    freeze = freezes[0]
    strategy = _strategies.get_any_version(freeze.strategy_version_id)
    if strategy is None:
        raise _error("FROZEN_STRATEGY_NOT_FOUND", "frozen strategy version was not found", 404)
    try:
        validate_oos_preconditions(
            protocol,
            selection,
            freeze,
            strategy,
            requested_oos_start=protocol.oos_start_date,
            requested_oos_end=protocol.oos_end_date,
        )
    except Exception as exc:
        raise _error(
            getattr(exc, "code", None) or "OOS_PRECONDITION_FAILED",
            "frozen OOS preconditions are not satisfied",
        ) from exc
    return protocol, freeze, strategy


def _spec(protocol: Any, freeze: Any, strategy: Any) -> OosEvaluationSpec:
    if protocol.evaluation_config is None:
        raise _error("OOS_CONFIGURATION_MISSING", "frozen evaluation configuration is missing")
    history_requirement = history_requirement_for_strategy(strategy)
    warmup_start = (
        warmup_start_for_strategy(protocol.is_start_date, strategy)
        if history_requirement is IndicatorHistoryRequirement.ALL_TIME_TO_DATE
        else protocol.is_start_date
    )
    validate_oos_range(
        protocol.is_start_date,
        protocol.is_end_date,
        protocol.oos_start_date,
        protocol.oos_end_date,
        warmup_start=warmup_start,
    )
    config = OosEvaluationConfig.from_research_evaluation_config(
        protocol.evaluation_config, analytics_version=ANALYTICS_VERSION
    )
    identity = OosEvaluationIdentity(
        protocol_id=protocol.protocol_id,
        selection_decision_id=_protocol_selection_id(protocol.protocol_id),
        strategy_freeze_id=freeze.freeze_id,
        strategy_version_id=strategy.version_id,
        strategy_content_hash=strategy.content_hash or "",
    )
    evaluation_range = OosEvaluationRange.from_protocol(protocol, warmup_start=warmup_start)
    provenance = OosDataProvenance(
        source="frozen_research_protocol",
        data_reference={
            "protocol_id": protocol.protocol_id,
            "strategy_version_id": strategy.version_id,
            "price_field": config.price_field_used.value,
            **(
                {"history_requirement": history_requirement.value}
                if history_requirement is IndicatorHistoryRequirement.ALL_TIME_TO_DATE
                else {}
            ),
        },
        requested_start=protocol.oos_start_date,
        requested_end=protocol.oos_end_date,
        warmup_start=warmup_start,
        price_field=config.price_field_used,
    )
    return OosEvaluationSpec(
        identity=identity,
        evaluation_range=evaluation_range,
        configuration=config,
        data_provenance=provenance,
    )


def _protocol_selection_id(protocol_id: str) -> str:
    selections = _protocols.list_selections(protocol_id)
    if len(selections) != 1:
        raise _error("OOS_SELECTION_INVALID", "official OOS requires one selection decision")
    return selections[0].decision_id


def _execution_payload(execution: Any) -> dict[str, Any]:
    payload = execution.to_dict()
    payload.pop("lease_token", None)
    return payload


def _build_execution_service() -> OosExecutionService:
    data_service = HistoricalDataService(
        TiingoClient(TiingoSettings.from_environment()), DiskCache()
    )
    return OosExecutionService(
        protocol_repository=_protocols,
        execution_repository=_executions,
        strategy_repository=_strategies,
        data_service=data_service,
        spec_resolver=lambda *, protocol, execution, identity: _spec(
            protocol,
            _protocols.get_freeze(execution.strategy_freeze_id),
            _strategies.get_any_version(execution.strategy_version_id),
        ),
    )


def _build_finalizer() -> OosFinalizationService:
    return OosFinalizationService(
        protocol_repository=_protocols,
        execution_repository=_executions,
        result_repository=_oos_results,
        backtest_repository=_backtests,
        strategy_repository=_strategies,
    )


@router.post("/protocols/{protocol_id}/oos/prepare")
def prepare_official_oos(
    protocol_id: str, body: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    if body not in (None, {}):
        raise _error("REQUEST_MUST_BE_EMPTY", "OOS preparation has no overrides", 422)
    protocol, freeze, strategy = _governance(protocol_id)
    try:
        spec = _spec(protocol, freeze, strategy)
        execution = _executions.get_or_create_execution(spec, now=datetime.now(UTC))
        return {
            "execution": _execution_payload(execution),
            "identity": spec.identity.to_dict(),
            "evaluation_range": spec.evaluation_range.to_dict(),
            "configuration": spec.configuration.to_dict(),
            "provenance": {"source": "frozen_research_protocol", "one_shot": True},
            "history_requirement": history_requirement_for_strategy(strategy).value,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(
            getattr(exc, "code", None) or "OOS_PREPARE_FAILED",
            "official OOS could not be prepared",
        ) from exc


@router.get("/protocols/{protocol_id}/oos/execution")
def get_official_oos_execution(protocol_id: str) -> dict[str, Any]:
    execution = _executions.get_execution_by_protocol(protocol_id)
    if execution is None:
        raise _error("OOS_EXECUTION_NOT_FOUND", "official OOS execution was not prepared", 404)
    strategy = _strategies.get_any_version(execution.strategy_version_id)
    return {
        "execution": _execution_payload(execution),
        "events": [event.to_dict() for event in _executions.list_events(execution.execution_id)],
        "history_requirement": (
            history_requirement_for_strategy(strategy).value if strategy is not None else None
        ),
    }


@router.post("/protocols/{protocol_id}/oos/execute")
def execute_official_oos(
    protocol_id: str, body: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    if body not in (None, {}):
        raise _error("REQUEST_MUST_BE_EMPTY", "OOS execution has no overrides", 422)
    protocol, freeze, strategy = _governance(protocol_id)
    del freeze, strategy
    execution = _executions.get_execution_by_protocol(protocol_id)
    if execution is None:
        raise _error("OOS_EXECUTION_NOT_PREPARED", "prepare official OOS before execution", 409)
    if execution.status is OosExecutionStatus.COMPLETED:
        return {
            "execution": _execution_payload(execution),
            "status": "completed",
            "idempotent": True,
        }
    try:
        claimed = _executions.claim_execution(
            execution.execution_id,
            owner="official-oos-api",
            now=datetime.now(UTC),
            lease_duration=timedelta(minutes=5),
        )
        outcome = _build_execution_service().execute(
            protocol_id=protocol_id,
            execution_id=claimed.execution_id,
            lease_token=claimed.lease_token or "",
        )
        result = _build_finalizer().finalize_oos_observation(
            protocol_id=protocol_id,
            execution_id=claimed.execution_id,
            lease_token=claimed.lease_token or "",
            outcome=outcome,
        )
        stored = _executions.get_execution(claimed.execution_id)
        return {
            "execution": _execution_payload(stored),
            "official_result": result.to_dict(),
            "status": "completed",
            "idempotent": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(
            getattr(exc, "code", None) or "OOS_EXECUTION_FAILED",
            "official OOS execution failed",
        ) from exc


__all__ = ["router"]
