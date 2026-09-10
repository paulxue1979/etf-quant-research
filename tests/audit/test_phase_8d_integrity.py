from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from research import ExperimentResultFinalizationService
from research.exceptions import ExperimentFinalizationError
from tests.unit.test_result_finalization_service import _finalize


def _finalizer(db_path: Path) -> ExperimentResultFinalizationService:
    return ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(db_path),
        experiment_result_repository=ExperimentResultRepository(db_path),
        candidate_execution_repository=CandidateExecutionRepository(db_path),
        experiment_repository=ExperimentRepository(db_path),
    )


def test_finalization_rejects_tampered_parameter_and_candidate_set_hashes(tmp_path: Path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)

    tampered = replace(
        outcome,
        parameter_space_hash="0" * 64,
        candidate_set_hash="1" * 64,
    )

    with pytest.raises(ExperimentFinalizationError, match="frozen experiment"):
        finalizer.finalize(tampered)

    assert ExperimentResultRepository(tmp_path / "research.db").list() == ()


def test_finalization_rejects_tampered_experiment_configuration_hash(tmp_path: Path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    tampered = replace(outcome, backtest_configuration_hash="2" * 64)

    with pytest.raises(ExperimentFinalizationError, match="frozen experiment"):
        finalizer.finalize(tampered)


def test_finalization_rejects_candidate_identity_from_another_index(tmp_path: Path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    tampered = replace(outcome, candidate_index=1)

    with pytest.raises(ExperimentFinalizationError, match="candidate execution identity"):
        finalizer.finalize(tampered)


def test_finalization_rejects_oos_backtest_and_never_persists_result(tmp_path: Path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    assert outcome.backtest_result is not None
    tampered = replace(
        outcome,
        backtest_result=replace(
            outcome.backtest_result,
            backtest_result=replace(
                outcome.backtest_result.backtest_result,
                end_date=outcome.is_end.replace(day=outcome.is_end.day + 3),
            ),
        ),
    )

    with pytest.raises(ExperimentFinalizationError, match="IS range"):
        finalizer.finalize(tampered)

    assert ExperimentResultRepository(tmp_path / "research.db").list() == ()


def test_finalizer_rejects_stale_candidate_execution_experiment_hash(tmp_path: Path) -> None:
    finalizer, outcome, executions = _finalize(tmp_path)
    execution = executions.get_execution(outcome.candidate_execution_id or "")
    assert execution is not None

    connection = executions._connect()  # audit-only direct tamper fixture
    try:
        connection.execute(
            "UPDATE candidate_executions SET experiment_hash = ? WHERE execution_id = ?",
            ("3" * 64, execution.execution_id),
        )
        connection.execute(
            "UPDATE candidate_executions SET canonical_json = canonical_json "
            "WHERE execution_id = ?",
            (execution.execution_id,),
        )
    finally:
        connection.close()

    with pytest.raises(Exception):
        finalizer.finalize(outcome)


def test_concurrent_finalization_converges_on_one_result_and_one_backtest(tmp_path: Path) -> None:
    _, outcome, _ = _finalize(tmp_path)
    db_path = tmp_path / "research.db"

    def finalize_once(_index: int):
        return _finalizer(db_path).finalize(outcome)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(finalize_once, (0, 1)))

    assert results[0] == results[1]
    assert len(ExperimentResultRepository(db_path).list(outcome.experiment_id)) == 1
    assert len(BacktestRepository(db_path).list()) == 1


def test_restart_reuses_immutable_result_without_reexecuting(tmp_path: Path) -> None:
    finalizer, outcome, _ = _finalize(tmp_path)
    first = finalizer.finalize(outcome)
    second = _finalizer(tmp_path / "research.db").finalize(outcome)

    assert second.experiment_result_id == first.experiment_result_id
    assert second.result_hash == first.result_hash
