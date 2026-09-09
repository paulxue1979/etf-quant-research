from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType

import pytest

from backend.app.experiment_result_repository import (
    ExperimentResultPersistenceError,
    ExperimentResultRepository,
)
from data.models import PriceField
from research.exceptions import ExperimentResultConflictError
from research.experiment_result import ExperimentResult

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64
CREATED_AT = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _result(
    *,
    created_at: datetime = CREATED_AT,
    candidate_id: str = HASH_B,
    backtest_run_id: str = "backtest-run-1",
) -> ExperimentResult:
    return ExperimentResult.create(
        experiment_id="experiment-result-test",
        candidate_id=candidate_id,
        candidate_index=0,
        parameter_set_hash=HASH_A,
        parameter_space_hash=HASH_B,
        candidate_set_hash=HASH_C,
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_D,
        derived_strategy_version_id="derived-v1",
        derived_strategy_version_hash=HASH_E,
        binding_hash=HASH_F,
        backtest_run_id=backtest_run_id,
        is_start=date(2020, 1, 13),
        is_end=date(2020, 1, 17),
        warmup_start=date(2020, 1, 9),
        warmup_end=date(2020, 1, 12),
        price_field_used=PriceField.ADJUSTED_CLOSE,
        backtest_configuration_hash=HASH_A,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        data_snapshot_reference={"symbol": "QQQ", "frequency": "daily"},
        performance_summary={
            "total_return": {"status": "available", "value": 0.12, "reason": None},
            "cagr": {"status": "not_evaluable", "value": None, "reason": "short range"},
        },
        created_at=created_at,
    )


def test_result_is_immutable_and_keeps_nested_mappings_immutable() -> None:
    result = _result()

    assert result.price_field_used is PriceField.ADJUSTED_CLOSE
    assert result.data_snapshot_reference["symbol"] == "QQQ"
    with pytest.raises(TypeError):
        result.data_snapshot_reference["symbol"] = "SPY"  # type: ignore[index]


def test_result_hash_and_id_are_deterministic_without_created_at() -> None:
    first = _result(created_at=CREATED_AT)
    second = _result(created_at=CREATED_AT + timedelta(days=1))

    assert first.result_hash == second.result_hash
    assert first.experiment_result_id == second.experiment_result_id
    assert first.created_at != second.created_at


def test_result_serialization_round_trip_is_canonical() -> None:
    result = _result()
    payload = result.to_dict()
    restored = ExperimentResult.from_dict(payload)

    assert restored == result
    assert json.loads(json.dumps(payload, sort_keys=True))


@pytest.mark.parametrize("unsafe", [float("nan"), float("inf"), float("-inf")])
def test_result_rejects_non_finite_summary_values(unsafe: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        _result_with_summary({"metric": unsafe})


def test_result_rejects_sensitive_payload_values() -> None:
    with pytest.raises(ValueError, match="sensitive"):
        _result_with_summary({"source": "TIINGO_API_KEY=hidden"})


def test_result_does_not_copy_backtest_accounting_products() -> None:
    result = _result()

    assert set(result.performance_summary) == {"total_return", "cagr"}
    assert "equity_curve" not in result.performance_summary
    assert "trades" not in result.performance_summary
    assert "orders" not in result.performance_summary


def test_repository_round_trip_and_restart_are_immutable(tmp_path) -> None:
    repository = ExperimentResultRepository(tmp_path / "results.db")
    result = _result()

    assert repository.create(result) == result
    restored = ExperimentResultRepository(tmp_path / "results.db").get(result.experiment_result_id)

    assert restored == result
    assert restored is not result
    assert repository.get_by_candidate(result.experiment_id, result.candidate_id) == result
    assert repository.list(result.experiment_id) == (result,)


def test_repository_duplicate_is_idempotent_but_conflict_is_rejected(tmp_path) -> None:
    repository = ExperimentResultRepository(tmp_path / "results.db")
    result = _result()

    assert repository.create(result) == result
    assert repository.create(result) == result
    conflicting = _result(backtest_run_id="backtest-run-2")
    with pytest.raises(ExperimentResultConflictError):
        repository.create(conflicting)


def test_repository_rejects_corrupted_persisted_result(tmp_path) -> None:
    db_path = tmp_path / "results.db"
    repository = ExperimentResultRepository(db_path)
    result = _result()
    repository.create(result)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE research_experiment_results SET result_json = ? WHERE experiment_result_id = ?",
            ("{\"tampered\":true}", result.experiment_result_id),
        )

    with pytest.raises(ExperimentResultPersistenceError, match="integrity|canonical"):
        repository.get(result.experiment_result_id)


def _result_with_summary(summary: dict[str, object]) -> ExperimentResult:
    return ExperimentResult.create(
        experiment_id="experiment-result-test",
        candidate_id=HASH_B,
        candidate_index=0,
        parameter_set_hash=HASH_A,
        parameter_space_hash=HASH_B,
        candidate_set_hash=HASH_C,
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_D,
        derived_strategy_version_id="derived-v1",
        derived_strategy_version_hash=HASH_E,
        binding_hash=HASH_F,
        backtest_run_id="backtest-run-1",
        is_start=date(2020, 1, 13),
        is_end=date(2020, 1, 17),
        warmup_start=None,
        warmup_end=None,
        price_field_used=PriceField.RAW_CLOSE,
        backtest_configuration_hash=HASH_A,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        data_snapshot_reference=MappingProxyType({"source": "fixture"}),
        performance_summary=summary,
        created_at=CREATED_AT,
    )
