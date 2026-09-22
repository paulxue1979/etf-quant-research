"""HTTP API for the controlled PHASE 8E-1G experiment research flow.

The module is intentionally a thin HTTP adapter.  It composes the persisted
read model and existing domain services; it does not calculate analytics,
rank candidates, execute OOS, or select a candidate automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_compatibility_service import ExperimentCompatibilityService
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import (
    ExperimentResultsReadModelError,
    ExperimentResultsReadService,
)
from backend.app.experiment_selection_handoff_service import (
    ExperimentSelectionHandoffError,
    ExperimentSelectionHandoffService,
)
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research.exceptions import (
    ExperimentSelectionConflictError,
    ExperimentSelectionPersistenceError,
    InvalidExperimentSelectionError,
    SelectionEligibilityError,
)
from research.experiment_selection import (
    ExperimentSelectionMethod,
    create_experiment_selection_decision,
)
from research.objective_evaluation import (
    CandidateObjectiveEvaluation,
    ObjectiveEvaluationError,
    evaluate_candidate_objective,
)


class ResearcherSelectionRequest(BaseModel):
    """Only the human choice is accepted from the caller.

    All strategy, result, hash, date, configuration, and provenance fields are
    resolved from the frozen experiment on the server.
    """

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str = Field(min_length=1)
    selection_method: ExperimentSelectionMethod
    researcher_rationale: str = Field(min_length=1, max_length=2_000)


# These are module-level on purpose: tests and deployments can provide the
# same repositories backed by an isolated database without changing endpoints.
experiment_repository = ExperimentRepository()
candidate_execution_repository = CandidateExecutionRepository(experiment_repository.db_path)
experiment_result_repository = ExperimentResultRepository(experiment_repository.db_path)
backtest_repository = BacktestRepository(experiment_repository.db_path)
strategy_repository = StrategyRepository(experiment_repository.db_path)
research_protocol_repository = ResearchProtocolRepository(experiment_repository.db_path)
selection_repository = ExperimentSelectionRepository(
    experiment_repository.db_path,
    experiment_repository=experiment_repository,
    result_repository=experiment_result_repository,
    execution_repository=candidate_execution_repository,
    backtest_repository=backtest_repository,
    strategy_repository=strategy_repository,
)
handoff_service = ExperimentSelectionHandoffService(
    experiment_repository=experiment_repository,
    selection_repository=selection_repository,
    result_repository=experiment_result_repository,
    execution_repository=candidate_execution_repository,
    backtest_repository=backtest_repository,
    strategy_repository=strategy_repository,
    protocol_repository=research_protocol_repository,
)

router = APIRouter(prefix="/research", tags=["experiment-research"])


class ResearchExperimentApiError(RuntimeError):
    """Safe structured error for the HTTP boundary."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _raise_http(exc: ResearchExperimentApiError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _safe_error(exc: Exception, *, fallback_code: str, fallback_message: str) -> HTTPException:
    """Map only stable domain codes; never expose implementation details."""
    if isinstance(exc, ResearchExperimentApiError):
        return _raise_http(exc)
    code = getattr(exc, "code", None)
    safe_codes = {
        "EXPERIMENT_NOT_FOUND": (404, "experiment was not found"),
        "EXPERIMENT_PROTOCOL_MISMATCH": (
            409,
            "experiment does not belong to the requested protocol",
        ),
        "CANDIDATE_NOT_FOUND": (404, "candidate was not found"),
        "SELECTION_NOT_FOUND": (404, "experiment selection was not found"),
        "EXPERIMENT_INVALID_STATE": (409, "experiment is not in a valid state for this operation"),
        "EXPERIMENT_NOT_COMPLETED": (409, "experiment is not completed"),
        "OBJECTIVE_CONSTRAINT_FAILED": (422, "selected candidate failed the frozen constraints"),
        "OBJECTIVE_CONSTRAINT_NOT_EVALUABLE": (
            422,
            "selected candidate constraints are not evaluable",
        ),
        "COMPATIBILITY_FAILED": (422, "experiment results are not compatible for selection"),
        "COMPATIBILITY_NOT_EVALUABLE": (422, "experiment compatibility is not evaluable"),
        "RESULT_NOT_AVAILABLE": (422, "selected candidate has no completed result"),
        "CANDIDATE_NOT_TERMINAL": (422, "selected candidate is not terminal"),
        "DERIVED_STRATEGY_INTEGRITY_ERROR": (
            409,
            "derived strategy identity failed integrity checks",
        ),
        "DERIVED_STRATEGY_NOT_FOUND": (404, "derived strategy version was not found"),
        "PROTOCOL_NOT_FOUND": (404, "research protocol was not found"),
        "PROTOCOL_INVALID_STATE": (409, "research protocol is not ready for handoff"),
        "SELECTION_INTEGRITY_ERROR": (409, "experiment selection failed integrity checks"),
        "SELECTION_MISMATCH": (409, "experiment selection does not match the experiment"),
        "STRATEGY_FREEZE_CONFLICT": (409, "strategy freeze conflicts with existing protocol state"),
        "CONFIGURATION_MISMATCH": (
            409,
            "backtest configuration does not match frozen protocol configuration",
        ),
        "PROVENANCE_MISMATCH": (409, "research provenance does not match the frozen experiment"),
    }
    if code in safe_codes:
        mapped_status, message = safe_codes[code]
        return HTTPException(status_code=mapped_status, detail={"code": code, "message": message})
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": fallback_code, "message": fallback_message},
    )


def _read_service() -> ExperimentResultsReadService:
    return ExperimentResultsReadService(
        experiment_repository=experiment_repository,
        candidate_execution_repository=candidate_execution_repository,
        experiment_result_repository=experiment_result_repository,
        backtest_repository=backtest_repository,
        strategy_repository=strategy_repository,
    )


def _load_model(protocol_id: str, experiment_id: str):
    try:
        return _read_service().get(experiment_id, protocol_id=protocol_id)
    except ExperimentResultsReadModelError as exc:
        raise _safe_error(
            exc,
            fallback_code="RESULT_READ_UNAVAILABLE",
            fallback_message="experiment results are unavailable",
        ) from exc
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="RESULT_READ_UNAVAILABLE",
            fallback_message="experiment results are unavailable",
        ) from exc


def _load_experiment(experiment_id: str):
    experiment = experiment_repository.get(experiment_id)
    if experiment is None:
        raise _raise_http(
            ResearchExperimentApiError(404, "EXPERIMENT_NOT_FOUND", "experiment was not found")
        )
    return experiment


def _model_payload(model: Any) -> dict[str, Any]:
    payload = model.to_dict()
    # The read model has the canonical candidate-set hash on every candidate;
    # expose the identity as a reference without inventing a second identifier.
    candidate_set_hash = next(
        (
            item.get("candidate_set_hash")
            for item in payload.get("candidates", [])
            if item.get("candidate_set_hash")
        ),
        None,
    )
    payload["candidate_set"] = {"candidate_set_hash": candidate_set_hash}
    payload["provenance"] = {
        "source": "persisted_experiment_results_read_model",
        "is_only": True,
        "candidate_order": "candidate_index_ascending",
    }
    return payload


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/results")
def get_experiment_results(protocol_id: str, experiment_id: str) -> dict[str, Any]:
    try:
        return _model_payload(_load_model(protocol_id, experiment_id))
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="RESULT_READ_UNAVAILABLE",
            fallback_message="experiment results are unavailable",
        ) from exc


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/result-summaries")
def get_experiment_result_summaries(
    protocol_id: str,
    experiment_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return optimization-ready result metadata without loading result_json."""
    experiment = _load_experiment(experiment_id)
    if experiment.protocol_id != protocol_id:
        raise _raise_http(
            ResearchExperimentApiError(
                409,
                "EXPERIMENT_PROTOCOL_MISMATCH",
                "experiment does not belong to the requested protocol",
            )
        )
    try:
        total, summaries = experiment_result_repository.list_summaries(
            experiment_id, limit=limit, offset=offset
        )
        return {
            "experiment_id": experiment_id,
            "protocol_id": protocol_id,
            "items": [item.to_dict() for item in summaries],
            "total": total,
            "limit": limit,
            "offset": offset,
            "ordering": "candidate_index_ascending",
            "provenance": {
                "source": "research_experiment_results.performance_summary_json",
                "analytics_recomputed": False,
                "ranking_applied": False,
            },
        }
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="RESULT_SUMMARY_UNAVAILABLE",
            fallback_message="experiment result summaries are unavailable",
        ) from exc


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/comparison")
def get_experiment_comparison(protocol_id: str, experiment_id: str) -> dict[str, Any]:
    model = _load_model(protocol_id, experiment_id)
    try:
        diagnostic = ExperimentCompatibilityService().diagnose(model)
        payload = diagnostic.to_dict()
        payload["provenance"] = {
            "source": "persisted_experiment_results_read_model",
            "is_only": True,
        }
        return payload
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="COMPATIBILITY_ERROR",
            fallback_message="experiment compatibility is unavailable",
        ) from exc


def _objective_payload(experiment: Any, model: Any) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    for candidate in model.candidates:
        evaluation = evaluate_candidate_objective(experiment, candidate)
        evaluations.append(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_index": candidate.candidate_index,
                "evaluation": evaluation.to_dict(),
            }
        )
    return {
        "experiment_id": experiment.experiment_id,
        "protocol_id": experiment.protocol_id,
        "objective_spec_hash": experiment.objective_spec_hash,
        "objective_specification": experiment.objective_specification.to_dict(),
        "evaluations": evaluations,
        "provenance": {"source": "frozen_experiment_objective", "is_only": True},
    }


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/objective-evaluation")
def get_objective_evaluation(protocol_id: str, experiment_id: str) -> dict[str, Any]:
    experiment = _load_experiment(experiment_id)
    model = _load_model(protocol_id, experiment_id)
    try:
        return _objective_payload(experiment, model)
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="OBJECTIVE_EVALUATION_ERROR",
            fallback_message="frozen objective evaluation is unavailable",
        ) from exc


@router.post(
    "/protocols/{protocol_id}/experiments/{experiment_id}/selection",
    status_code=status.HTTP_201_CREATED,
)
def create_selection(
    protocol_id: str, experiment_id: str, request: ResearcherSelectionRequest
) -> dict[str, Any]:
    experiment = _load_experiment(experiment_id)
    model = _load_model(protocol_id, experiment_id)
    try:
        selected = next(
            (
                item
                for item in model.candidates
                if item.candidate_id == request.selected_candidate_id.strip()
            ),
            None,
        )
        if selected is None:
            raise ResearchExperimentApiError(
                422, "CANDIDATE_NOT_SELECTABLE", "selected candidate was not found"
            )
        evaluations: dict[str, CandidateObjectiveEvaluation] = {}
        for candidate in model.candidates:
            evaluations[candidate.candidate_id] = evaluate_candidate_objective(
                experiment, candidate
            )
        selected_evaluation = evaluations[selected.candidate_id]
        compatibility = ExperimentCompatibilityService().diagnose(model)
        derived = strategy_repository.get_any_version(selected.derived_strategy_version_id or "")
        if derived is None:
            raise ResearchExperimentApiError(
                409, "DERIVED_STRATEGY_NOT_FOUND", "derived strategy version was not found"
            )
        decision = create_experiment_selection_decision(
            experiment=experiment,
            selected_candidate_id=selected.candidate_id,
            candidates=model,
            objective_evaluation=selected_evaluation,
            objective_evaluations=evaluations,
            compatibility=compatibility,
            derived_strategy_version=derived,
            selection_method=request.selection_method,
            researcher_rationale=request.researcher_rationale,
            created_at=datetime.now(UTC),
        )
        stored = selection_repository.create(decision)
        return stored.to_dict()
    except ResearchExperimentApiError as exc:
        raise _raise_http(exc)
    except (
        SelectionEligibilityError,
        InvalidExperimentSelectionError,
        ObjectiveEvaluationError,
    ) as exc:
        raise _safe_error(
            exc,
            fallback_code="CANDIDATE_NOT_SELECTABLE",
            fallback_message="candidate is not eligible for selection",
        ) from exc
    except ExperimentSelectionConflictError as exc:
        raise _safe_error(
            exc,
            fallback_code="SELECTION_CONFLICT",
            fallback_message="experiment already has a different selection",
        ) from exc
    except ExperimentSelectionPersistenceError as exc:
        raise _safe_error(
            exc,
            fallback_code="SELECTION_INTEGRITY_ERROR",
            fallback_message="experiment selection could not be persisted",
        ) from exc
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="SELECTION_SERVICE_UNAVAILABLE",
            fallback_message="experiment selection service is unavailable",
        ) from exc


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/selection")
def get_selection(protocol_id: str, experiment_id: str) -> dict[str, Any]:
    _load_model(protocol_id, experiment_id)  # binds the path protocol to the experiment
    try:
        decision = selection_repository.get_by_experiment_id(experiment_id)
        if decision is None:
            raise ResearchExperimentApiError(
                404, "SELECTION_NOT_FOUND", "experiment selection was not found"
            )
        return decision.to_dict()
    except ResearchExperimentApiError as exc:
        raise _raise_http(exc)
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="SELECTION_READ_UNAVAILABLE",
            fallback_message="experiment selection is unavailable",
        ) from exc


@router.post("/protocols/{protocol_id}/experiments/{experiment_id}/handoff")
def handoff_selection(
    protocol_id: str,
    experiment_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    if body not in (None, {}):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REQUEST_MUST_BE_EMPTY",
                "message": "handoff does not accept identity or configuration overrides",
            },
        )
    _load_model(protocol_id, experiment_id)
    try:
        decision, freeze = handoff_service.handoff_experiment_selection(experiment_id)
        return {
            "experiment_id": experiment_id,
            "protocol_id": protocol_id,
            "selection_decision": decision.to_dict(),
            "strategy_freeze": freeze.to_dict(),
            "identity": {
                "strategy_version_id": decision.selected_strategy_version_id,
                "strategy_version_content_hash": freeze.strategy_version_content_hash,
            },
            "provenance": {
                "source": "persisted_experiment_selection",
                "is_only": True,
                "oos_started": False,
            },
        }
    except ExperimentSelectionHandoffError as exc:
        raise _safe_error(
            exc,
            fallback_code="HANDOFF_INTEGRITY_ERROR",
            fallback_message="experiment selection handoff is unavailable",
        ) from exc
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="HANDOFF_SERVICE_UNAVAILABLE",
            fallback_message="experiment selection handoff is unavailable",
        ) from exc


@router.get("/protocols/{protocol_id}/experiments/{experiment_id}/handoff")
def get_handoff(protocol_id: str, experiment_id: str) -> dict[str, Any]:
    _load_model(protocol_id, experiment_id)
    try:
        experiment_selection = selection_repository.get_by_experiment_id(experiment_id)
        if experiment_selection is None:
            raise ResearchExperimentApiError(
                404, "SELECTION_NOT_FOUND", "experiment selection was not found"
            )
        protocol_selections = research_protocol_repository.list_selections(protocol_id)
        matched = tuple(
            item
            for item in protocol_selections
            if dict(item.data_provenance).get("experiment_selection_id")
            == experiment_selection.selection_id
        )
        if not matched:
            raise ResearchExperimentApiError(
                404, "HANDOFF_NOT_FOUND", "experiment selection has not been handed off"
            )
        decision = matched[0]
        freezes = tuple(
            item
            for item in research_protocol_repository.list_freezes(protocol_id)
            if item.selection_decision_id == decision.decision_id
        )
        if len(freezes) != 1:
            raise ResearchExperimentApiError(
                409, "HANDOFF_INTEGRITY_ERROR", "handoff freeze binding is incomplete"
            )
        freeze = freezes[0]
        if (
            decision.selected_strategy_version_id
            != experiment_selection.selected_derived_strategy_version_id
            or freeze.strategy_version_id != decision.selected_strategy_version_id
            or freeze.strategy_version_content_hash
            != experiment_selection.selected_derived_strategy_content_hash
        ):
            raise ResearchExperimentApiError(
                409, "HANDOFF_INTEGRITY_ERROR", "handoff strategy identity is inconsistent"
            )
        return {
            "experiment_id": experiment_id,
            "protocol_id": protocol_id,
            "selection_decision": decision.to_dict(),
            "strategy_freeze": freeze.to_dict(),
            "identity": {
                "strategy_version_id": decision.selected_strategy_version_id,
                "strategy_version_content_hash": freeze.strategy_version_content_hash,
            },
        }
    except ResearchExperimentApiError as exc:
        raise _raise_http(exc)
    except Exception as exc:
        raise _safe_error(
            exc,
            fallback_code="HANDOFF_READ_UNAVAILABLE",
            fallback_message="handoff state is unavailable",
        ) from exc


__all__ = [
    "ResearcherSelectionRequest",
    "ResearchExperimentApiError",
    "router",
]
