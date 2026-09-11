from __future__ import annotations

from datetime import timedelta

from backend.app.backtest_repository import BacktestRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import (
    ExperimentResultsReadModelError,
    ExperimentResultsReadService,
)
from research import ExperimentResult, ExperimentResultFinalizationService, ExperimentStatus
from research.execution import CandidateExecutionStatus
from research.experiment_read_model import ExperimentResultStatus
from tests.unit.test_experiment_execution_service import _binding, _setup


def _service(tmp_path, experiments, executions, strategies):
    database = tmp_path / "research.db"
    return ExperimentResultsReadService(
        experiments,
        executions,
        ExperimentResultRepository(database),
        BacktestRepository(database),
        strategies,
    )


def test_partial_read_model_preserves_order_and_completed_result(tmp_path) -> None:
    execution_service, experiments, _, executions, _, experiment = _setup(tmp_path)
    strategies = execution_service._strategies
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "read-model-candidates",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "read-model-running",
    )
    outcome = execution_service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(tmp_path / "research.db"),
        experiment_result_repository=ExperimentResultRepository(tmp_path / "research.db"),
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: execution_service._clock(),
    )
    run = finalizer._build_backtest_run(outcome)
    BacktestRepository(tmp_path / "research.db").create(run)
    ExperimentResultRepository(tmp_path / "research.db").create(
        ExperimentResult.from_outcome(
            outcome,
            backtest_run_id=run.backtest_run_id,
            created_at=execution_service._clock(),
        )
    )

    model = _service(tmp_path, experiments, executions, strategies).get(experiment.experiment_id)

    assert [item.candidate_index for item in model.candidates] == [0, 1]
    assert model.candidates[0].result_status is ExperimentResultStatus.COMPLETED
    assert model.candidates[0].warmup_start < model.is_start
    assert model.candidates[0].is_start == model.is_start
    assert model.summary.candidate_count == 2
    assert model.summary.completed_count == 1
    assert model.summary.result_count == 1
    assert model.summary.completeness_status == "partial"
    assert "rank" not in model.to_dict()


def test_failed_candidate_remains_visible_with_safe_summary(tmp_path) -> None:
    execution_service, experiments, _, executions, _, experiment = _setup(tmp_path)
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "failed-candidates",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "failed-running",
    )
    execution_service._data.get_history = lambda request: (_ for _ in ()).throw(
        LookupError("/Users/secret/TIINGO_API_KEY=SECRET_SENTINEL_8E1A")
    )
    execution_service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    model = _service(tmp_path, experiments, executions, execution_service._strategies).get(
        experiment.experiment_id
    )
    candidate = model.candidates[0]
    assert candidate.result_status is ExperimentResultStatus.FAILED
    assert candidate.execution_status == CandidateExecutionStatus.FAILED.value
    assert candidate.failure_code
    assert "SECRET_SENTINEL_8E1A" not in candidate.failure_summary
    assert model.summary.failed_count == 1


def test_protocol_path_is_bound_and_oos_is_not_loaded(tmp_path) -> None:
    execution_service, experiments, _, executions, _, experiment = _setup(tmp_path)
    service = _service(tmp_path, experiments, executions, execution_service._strategies)

    try:
        service.get(experiment.experiment_id, protocol_id="other-protocol")
    except ExperimentResultsReadModelError as exc:
        assert exc.code == "EXPERIMENT_PROTOCOL_MISMATCH"
    else:
        raise AssertionError("protocol mismatch must be rejected")

    model = service.get(experiment.experiment_id, protocol_id=experiment.protocol_id)
    assert model.protocol_id == experiment.protocol_id
    assert "oos" not in model.to_dict()


def test_not_evaluable_status_is_explicit_and_never_zero(tmp_path) -> None:
    execution_service, experiments, _, executions, _, experiment = _setup(tmp_path)
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "not-evaluable-candidates",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "not-evaluable-running",
    )
    execution = execution_service._prepare(
        experiment.experiment_id,
        0,
        (_binding(),),
        claimed_by="test",
        started_at=execution_service._clock(),
    ).execution
    # Persist a terminal failure using the repository's domain transition API.
    executions.create_execution(execution)
    now = execution_service._clock()
    claimed = executions.claim_candidate(
        experiment.experiment_id,
        0,
        "test",
        now,
        now + timedelta(minutes=5),
    )
    failed = claimed.fail(
        actor="test",
        now=execution_service._clock(),
        failed_at=execution_service._clock(),
        failure_code="INDICATOR_NOT_EVALUABLE",
        failure_message="indicator warmup is not evaluable",
        retryable=False,
    )
    executions.mark_failed(
        failed.execution_id,
        "test",
        execution_service._clock(),
        failed.failed_at,
        failed.failure_code or "INDICATOR_NOT_EVALUABLE",
        failed.failure_message or "indicator warmup is not evaluable",
        failed.failure_retryable is True,
    )

    model = _service(tmp_path, experiments, executions, execution_service._strategies).get(
        experiment.experiment_id
    )
    assert model.candidates[0].result_status is ExperimentResultStatus.NOT_EVALUABLE
    assert model.candidates[0].failure_summary != "0"
