"""HTTP workflow for creating and browsing immutable research experiments."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from backend.app.experiment_repository import ExperimentRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research.canonical import sha256_hash
from research.enums import ExperimentMethod, ExperimentStatus
from research.experiments import (
    Experiment,
    ExperimentProvenance,
    ObjectiveSpecification,
    ParameterSpace,
)
from research.grid_search import GridSearchDefinition, preflight_grid_search
from research.semantic_bindings import resolve_semantic_bindings

router = APIRouter(prefix="/research", tags=["experiment-workflow"])
experiment_repository = ExperimentRepository()
strategy_repository = StrategyRepository(experiment_repository.db_path)
protocol_repository = ResearchProtocolRepository(experiment_repository.db_path)


def _error(code: str, message: str, status_code: int = 422) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _dates(body: dict[str, Any]) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(body["is_start"]))
        end = date.fromisoformat(str(body["is_end"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("INVALID_IS_RANGE", "IS start and end dates are required") from exc
    if start > end:
        raise _error("INVALID_IS_RANGE", "IS start date must not exceed end date")
    return start, end


def _grid_definition(
    body: dict[str, Any], *, experiment_id: str, experiment_hash: str
) -> GridSearchDefinition:
    raw = body.get("grid_definition", {})
    if not isinstance(raw, Mapping):
        raise _error("INVALID_GRID_DEFINITION", "grid_definition must be an object")
    try:
        return GridSearchDefinition.from_dict(
            {**dict(raw), "experiment_id": experiment_id, "experiment_hash": experiment_hash}
        )
    except Exception as exc:
        raise _error("INVALID_GRID_DEFINITION", str(exc)) from exc


def _frozen_strategy_bindings(version: Any, body: dict[str, Any]) -> list[dict[str, str]]:
    """Resolve semantic bindings once for the immutable Experiment provenance."""
    definition = _grid_definition(
        body,
        experiment_id="workflow-binding-preview",
        experiment_hash="0" * 64,
    )
    resolved = resolve_semantic_bindings(version, definition.semantic_bindings)
    bindings = [item.to_dict() for item in definition.strategy_bindings]
    bindings.extend(item.parameter_binding.to_dict() for item in resolved)
    return bindings


def _build_experiment(
    body: dict[str, Any],
    *,
    preview: bool = False,
    strategies: StrategyRepository | None = None,
    protocols: ResearchProtocolRepository | None = None,
) -> Experiment:
    strategies = strategies or strategy_repository
    protocols = protocols or protocol_repository
    version_id = str(body.get("base_strategy_version_id", "")).strip()
    protocol_id = str(body.get("protocol_id", "")).strip()
    version = strategies.get_any_version(version_id)
    if version is None:
        raise _error(
            "STRATEGY_VERSION_NOT_FOUND",
            "the immutable base strategy version was not found",
            404,
        )
    protocol = protocols.get_protocol(protocol_id)
    if protocol is None:
        raise _error("PROTOCOL_NOT_FOUND", "the research protocol was not found", 404)
    start, end = _dates(body)
    strategy_rebalance = version.configuration.rebalance_policy
    if protocol.evaluation_config is not None:
        frozen_rebalance = protocol.evaluation_config.rebalance_policy
        if (
            frozen_rebalance["frequency"] != strategy_rebalance.frequency.value
            or frozen_rebalance["threshold"] != strategy_rebalance.threshold
        ):
            raise _error(
                "PROTOCOL_STRATEGY_REBALANCE_MISMATCH",
                "frozen protocol rebalance policy must match the immutable strategy version",
            )
    try:
        space = ParameterSpace.from_dict(body.get("parameter_space", {}))
        objective = ObjectiveSpecification.from_dict(
            body.get(
                "objective_specification",
                {"primary_metric": "cagr", "metric_directions": {"cagr": "maximize"}},
            )
        )
        price_field = PriceField(str(body.get("price_field", PriceField.ADJUSTED_CLOSE.value)))
        initial_capital = float(body.get("initial_capital", 100_000))
        frozen_bindings = _frozen_strategy_bindings(version, body)
    except (TypeError, ValueError, KeyError) as exc:
        raise _error("INVALID_EXPERIMENT_DEFINITION", str(exc)) from exc

    name = str(body.get("name", "Untitled experiment")).strip() or "Untitled experiment"
    description = str(body.get("description", "")).strip()
    grid_definition = body.get("grid_definition", {})
    if not isinstance(grid_definition, Mapping):
        raise _error("INVALID_GRID_DEFINITION", "grid_definition must be an object")
    ui_metadata = {
        "name": name,
        "description": description,
        "source": "experiment_builder",
        "grid_definition": dict(grid_definition),
    }
    identity = sha256_hash(
        {
            "protocol_id": protocol_id,
            "base_strategy_version_id": version.version_id,
            "base_strategy_version_hash": version.content_hash,
            "parameter_space": space.to_dict(),
            "objective": objective.to_dict(),
            "is_start": start.isoformat(),
            "is_end": end.isoformat(),
            "ui_metadata": ui_metadata,
            "parameter_bindings": frozen_bindings,
        }
    )
    experiment_id = str(body.get("experiment_id") or f"experiment-ui-{identity[:32]}")
    if preview:
        experiment_id = f"preview-{identity[:32]}"
    backtest_frequency = RebalanceFrequency(strategy_rebalance.frequency.value)
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=start,
        end_date=end,
        initial_capital=initial_capital,
        price_field_used=price_field,
        rebalance_policy=RebalancePolicy(
            backtest_frequency, threshold=strategy_rebalance.threshold
        ),
    )
    provenance = ExperimentProvenance(
        protocol_id=protocol_id,
        strategy_definition_id=version.strategy_id,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash,
        parameter_space_hash=space.content_hash,
        objective_spec_hash=objective.content_hash,
        is_start_date=start,
        is_end_date=end,
        price_field_used=price_field,
        initial_capital=initial_capital,
        commission={"rate": 0.0, "per_order": 0.0},
        slippage=0.0,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy={
            "frequency": strategy_rebalance.frequency.value,
            "threshold": strategy_rebalance.threshold,
        },
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        data_snapshot_reference={"ui_metadata": ui_metadata},
        parameter_bindings=tuple(frozen_bindings),
    )
    return Experiment(
        experiment_id=experiment_id,
        protocol_id=protocol_id,
        strategy_definition_id=version.strategy_id,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash,
        parameter_space=space,
        objective_specification=objective,
        is_start_date=start,
        is_end_date=end,
        backtest_configuration=config,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=ExperimentStatus.SPACE_FROZEN,
        created_at=datetime.now(UTC),
        method=ExperimentMethod.GRID,
        provenance=provenance,
    )


def _metadata(experiment: Experiment) -> dict[str, Any]:
    raw = experiment.provenance.data_snapshot_reference if experiment.provenance else {}
    value = raw.get("ui_metadata", {}) if isinstance(raw, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _workflow_payload(experiment: Experiment, *, idempotent: bool | None = None) -> dict[str, Any]:
    payload = {
        **experiment.to_dict(),
        "display_name": _metadata(experiment).get("name", experiment.experiment_id),
        "description": _metadata(experiment).get("description", ""),
    }
    reference = experiment.provenance.data_snapshot_reference if experiment.provenance else {}
    ui_metadata = reference.get("ui_metadata", {}) if isinstance(reference, Mapping) else {}
    if isinstance(ui_metadata, Mapping) and isinstance(ui_metadata.get("grid_definition"), Mapping):
        payload["grid_definition"] = dict(ui_metadata["grid_definition"])
    if idempotent is not None:
        payload["idempotent"] = idempotent
    return payload


def _preflight_for(experiment: Experiment, body: dict[str, Any]) -> dict[str, Any]:
    definition = _grid_definition(
        body,
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
    )
    version = strategy_repository.get_any_version(experiment.base_strategy_version_id)
    if version is None:
        raise _error(
            "STRATEGY_VERSION_NOT_FOUND",
            "the immutable base strategy version was not found",
            404,
        )
    return preflight_grid_search(experiment, definition, version).to_dict(include_candidates=False)


@router.get("/experiments")
def list_experiments(
    protocol_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None),
) -> dict[str, Any]:
    items = []
    needle = query.strip().lower() if query else ""
    read_page = experiment_repository.list_for_read(protocol_id)
    for experiment in read_page.items:
        if status and experiment.status.value != status:
            continue
        metadata = _metadata(experiment)
        haystack = " ".join((experiment.experiment_id, metadata.get("name", ""))).lower()
        if needle and needle not in haystack:
            continue
        items.append(_workflow_payload(experiment))
    response: dict[str, Any] = {"items": items, "total": len(items)}
    if read_page.skipped_count:
        response["skipped_count"] = read_page.skipped_count
        response["warnings"] = [
            "Some historical experiments are unavailable because their immutable "
            "external binding is missing."
        ]
    return response


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict[str, Any]:
    experiment = experiment_repository.get(experiment_id)
    if experiment is None:
        raise _error("EXPERIMENT_NOT_FOUND", "the experiment was not found", 404)
    return _workflow_payload(experiment)


@router.post("/experiments/preflight")
def preflight_experiment(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        experiment = _build_experiment(body, preview=True)
        return _preflight_for(experiment, body)
    except HTTPException:
        raise
    except Exception as exc:
        raise _error("EXPERIMENT_PREFLIGHT_FAILED", str(exc)) from exc


@router.post("/experiments")
def create_experiment(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        experiment = _build_experiment(body)
        preflight = _preflight_for(experiment, body)
        if not preflight["can_execute"]:
            raise _error(
                "EXPERIMENT_PREFLIGHT_REQUIRED",
                "; ".join(preflight["issues"]) or "experiment preflight is not ready",
            )
        existing = experiment_repository.get(experiment.experiment_id)
        if existing is not None:
            return _workflow_payload(existing, idempotent=True)
        experiment_repository.create(experiment)
        return _workflow_payload(experiment, idempotent=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise _error("EXPERIMENT_CREATE_FAILED", str(exc), 409) from exc


__all__ = ["router"]
