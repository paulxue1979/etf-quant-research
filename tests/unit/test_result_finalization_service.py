from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from research import ExperimentResultFinalizationService
from research.exceptions import ExperimentFinalizationError
from research.execution import CandidateExecutionStatus
from research.execution_outcome import ExperimentExecutionOutcomeStatus
from tests.unit.test_experiment_execution_service import (
    NOW,
    _binding,
    _setup,
)


def _finalize(tmp_path):
    service, _, _, executions, _, experiment = _setup(tmp_path)
    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(tmp_path / "research.db"),
        experiment_result_repository=ExperimentResultRepository(tmp_path / "research.db"),
        candidate_execution_repository=executions,
        clock=lambda: NOW,
    )
    return finalizer, outcome, executions


def test_finalization_persists_backtest_source_and_analytics_summary(tmp_path) -> None:
    finalizer, outcome, executions = _finalize(tmp_path)

    result = finalizer.finalize(outcome)
    run = BacktestRepository(tmp_path / "research.db").get(result.backtest_run_id)

    assert result.experiment_id == outcome.experiment_id
    assert result.candidate_id == outcome.candidate_id
    assert result.price_field_used is outcome.price_field_used
    assert result.is_start == outcome.is_start
    assert result.is_end == outcome.is_end
    assert result.warmup_end < result.is_start
    assert run is not None
    assert run.backtest_result == outcome.backtest_result.backtest_result
    assert run.performance_analysis.to_dict() == outcome.performance_analysis_result.to_dict() | {
        "backtest_run_id": result.backtest_run_id,
        "strategy_id": outcome.backtest_result.strategy_id,
    }
    execution = executions.get_execution(outcome.candidate_execution_id or "")
    assert execution is not None and execution.status is CandidateExecutionStatus.COMPLETED


def test_finalization_is_idempotent_and_survives_restart(tmp_path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)

    first = finalizer.finalize(outcome)
    second = finalizer.finalize(outcome)
    reopened = ExperimentResultRepository(tmp_path / "research.db")

    assert second == first
    assert reopened.get(first.experiment_result_id) == first
    assert len(reopened.list(outcome.experiment_id)) == 1


def test_finalization_rejects_non_completed_outcomes(tmp_path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    failed = replace(
        outcome,
        status=ExperimentExecutionOutcomeStatus.FAILED,
        backtest_result=None,
        performance_analysis_result=None,
        failure_code="EXECUTION_FAILED",
        failure_message="candidate execution failed",
    )

    with pytest.raises(ExperimentFinalizationError, match="only completed"):
        finalizer.finalize(failed)


def test_finalization_rejects_is_range_mismatch(tmp_path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    assert outcome.backtest_result is not None
    invalid = replace(
        outcome,
        backtest_result=replace(
            outcome.backtest_result,
            backtest_result=replace(
                outcome.backtest_result.backtest_result,
                end_date=outcome.is_end.replace(day=18),
            ),
        ),
    )

    with pytest.raises(ExperimentFinalizationError, match="IS range"):
        finalizer.finalize(invalid)


def test_finalization_rejects_price_field_mismatch(tmp_path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    assert outcome.performance_analysis_result is not None
    invalid = replace(
        outcome,
        performance_analysis_result=replace(
            outcome.performance_analysis_result,
            price_field_used=outcome.price_field_used.__class__.RAW_CLOSE,
        ),
    )

    with pytest.raises(ExperimentFinalizationError, match="price field"):
        finalizer.finalize(invalid)


def test_finalization_rejects_missing_execution_and_preserves_no_result(tmp_path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    missing = replace(outcome, candidate_execution_id="missing-execution")

    with pytest.raises(ExperimentFinalizationError, match="candidate execution does not exist"):
        finalizer.finalize(missing)
    assert ExperimentResultRepository(tmp_path / "research.db").list() == ()


def test_finalization_keeps_result_identity_stable_when_clock_changes(tmp_path) -> None:
    finalizer, outcome, executions = _finalize(tmp_path)
    first = finalizer.finalize(outcome)
    later = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(tmp_path / "research.db"),
        experiment_result_repository=ExperimentResultRepository(tmp_path / "research.db"),
        candidate_execution_repository=executions,
        clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert later.finalize(outcome).result_hash == first.result_hash
