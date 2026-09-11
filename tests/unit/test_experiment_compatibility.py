from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from backend.app.experiment_compatibility_service import ExperimentCompatibilityService
from data.models import PriceField
from research.experiment_compatibility import (
    CompatibilityMismatch,
    CompatibilityReasonCode,
    CompatibilityStatus,
)
from research.experiment_read_model import (
    ExperimentCandidateView,
    ExperimentResultsReadModel,
    ExperimentResultsSummary,
    ExperimentResultStatus,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "start_date": "2020-01-02",
        "end_date": "2020-12-31",
        "initial_capital": 10_000.0,
        "price_field_used": "adjusted_close",
        "commission": {"rate": 0.001, "per_order": 1.0},
        "slippage": 0.002,
        "execution_rule": "next_trading_day_open",
        "fractional_shares": False,
        "rebalance_policy": {"frequency": "monthly", "threshold": 0.05},
    }
    value.update(overrides)
    return value


def _candidate(
    index: int,
    *,
    result_status: ExperimentResultStatus = ExperimentResultStatus.COMPLETED,
    experiment_id: str = "experiment-1",
    protocol_id: str = "protocol-1",
    snapshot: dict[str, object] | None = None,
    configuration_hash: str = HASH_A,
    provenance: dict[str, object] | None = None,
) -> ExperimentCandidateView:
    return ExperimentCandidateView(
        experiment_id=experiment_id,
        protocol_id=protocol_id,
        candidate_id=f"candidate-{index}",
        candidate_index=index,
        parameter_set={"period": 20 + index},
        parameter_set_hash=HASH_A,
        candidate_set_hash=HASH_B,
        parameter_space_hash=HASH_A,
        objective_spec_hash=HASH_B,
        execution_status=(
            "completed" if result_status is ExperimentResultStatus.COMPLETED else "failed"
        ),
        result_status=result_status,
        experiment_result_id=(
            f"result-{index}" if result_status is ExperimentResultStatus.COMPLETED else None
        ),
        result_hash=HASH_A if result_status is ExperimentResultStatus.COMPLETED else None,
        backtest_run_id=(
            f"backtest-{index}" if result_status is ExperimentResultStatus.COMPLETED else None
        ),
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_A,
        derived_strategy_version_id=f"derived-v{index}",
        derived_strategy_version_hash=HASH_B,
        binding_hash=HASH_A,
        materialization_spec_hash=HASH_B,
        is_start=date(2020, 1, 2),
        is_end=date(2020, 12, 31),
        warmup_start=date(2019, 12, 1),
        warmup_end=date(2020, 1, 1),
        price_field_used=PriceField.ADJUSTED_CLOSE,
        backtest_configuration_hash=configuration_hash,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        data_snapshot_reference=(
            {"symbol": "QQQ", "frequency": "daily"} if provenance is None else provenance
        ),
        configuration_snapshot=_snapshot() if snapshot is None else snapshot,
        performance_summary={"cagr": {"status": "available", "value": 0.1}},
    )


def _model(*candidates: ExperimentCandidateView) -> ExperimentResultsReadModel:
    return ExperimentResultsReadModel(
        experiment_id="experiment-1",
        protocol_id="protocol-1",
        experiment_status="completed",
        is_start=date(2020, 1, 2),
        is_end=date(2020, 12, 31),
        parameter_space_hash=HASH_A,
        objective_spec_hash=HASH_B,
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_A,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        candidates=tuple(candidates),
        summary=ExperimentResultsSummary(
            candidate_count=len(candidates),
            completed_count=sum(
                item.result_status is ExperimentResultStatus.COMPLETED for item in candidates
            ),
            failed_count=sum(
                item.result_status is ExperimentResultStatus.FAILED for item in candidates
            ),
            not_evaluable_count=0,
            running_count=0,
            pending_count=0,
            result_count=sum(item.experiment_result_id is not None for item in candidates),
            complete=True,
            completeness_status="complete",
        ),
    )


def _reasons(diagnostic) -> set[CompatibilityReasonCode]:
    return {item.reason_code for item in diagnostic.mismatches}


def test_compatible_candidates_ignore_parameter_and_derived_strategy_identity() -> None:
    diagnostic = ExperimentCompatibilityService().diagnose(_model(_candidate(0), _candidate(1)))

    assert diagnostic.status is CompatibilityStatus.COMPATIBLE
    assert diagnostic.is_compatible is True
    assert diagnostic.reference_candidate_id == "candidate-0"
    assert diagnostic.compared_candidate_ids == ("candidate-1",)
    assert diagnostic.mismatches == ()


def test_reference_selection_and_serialization_are_deterministic() -> None:
    first = ExperimentCompatibilityService().diagnose(_model(_candidate(0), _candidate(1)))
    second = ExperimentCompatibilityService().diagnose(_model(_candidate(0), _candidate(1)))

    assert first.canonical_json() == second.canonical_json()
    assert first.to_dict()["checked_dimensions"] == list(
        ExperimentCompatibilityService.DIMENSIONS
    )
    assert "rank" not in first.to_dict()


def test_all_required_semantic_mismatch_reasons_are_reported() -> None:
    changed = _candidate(
        1,
        experiment_id="experiment-other",
        protocol_id="protocol-other",
        snapshot=_snapshot(
            start_date="2020-01-03",
            end_date="2020-12-30",
            initial_capital=20_000.0,
            price_field_used="raw_close",
            commission={"rate": 0.003, "per_order": 2.0},
            slippage=0.01,
            execution_rule="next_trading_day_open_later",
            fractional_shares=True,
            rebalance_policy={"frequency": "weekly", "threshold": 0.1},
        ),
        configuration_hash=HASH_B,
        provenance={"symbol": "SPY", "frequency": "daily"},
    )
    changed = replace(changed, engine_version="phase-3.1", analysis_version="phase-4i.1")

    reasons = _reasons(ExperimentCompatibilityService().diagnose(_model(_candidate(0), changed)))

    assert reasons >= {
        CompatibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
        CompatibilityReasonCode.PROTOCOL_BINDING_MISMATCH,
        CompatibilityReasonCode.IS_RANGE_MISMATCH,
        CompatibilityReasonCode.PRICE_FIELD_MISMATCH,
        CompatibilityReasonCode.INITIAL_CAPITAL_MISMATCH,
        CompatibilityReasonCode.COMMISSION_MISMATCH,
        CompatibilityReasonCode.SLIPPAGE_MISMATCH,
        CompatibilityReasonCode.EXECUTION_RULE_MISMATCH,
        CompatibilityReasonCode.FRACTIONAL_SHARES_MISMATCH,
        CompatibilityReasonCode.REBALANCE_POLICY_MISMATCH,
        CompatibilityReasonCode.ENGINE_VERSION_MISMATCH,
        CompatibilityReasonCode.ANALYTICS_VERSION_MISMATCH,
        CompatibilityReasonCode.BACKTEST_CONFIGURATION_MISMATCH,
        CompatibilityReasonCode.DATA_PROVENANCE_MISMATCH,
    }


def test_same_configuration_hash_with_different_semantics_is_integrity_mismatch() -> None:
    changed = _candidate(1, snapshot=_snapshot(initial_capital=11_000.0))

    diagnostic = ExperimentCompatibilityService().diagnose(_model(_candidate(0), changed))

    assert diagnostic.status is CompatibilityStatus.INCOMPATIBLE
    assert _reasons(diagnostic) == {CompatibilityReasonCode.INTEGRITY_MISMATCH}


def test_missing_provenance_is_not_evaluable_not_incompatible() -> None:
    diagnostic = ExperimentCompatibilityService().diagnose(
        _model(_candidate(0, provenance={}), _candidate(1, provenance={}))
    )

    assert diagnostic.status is CompatibilityStatus.NOT_EVALUABLE
    assert _reasons(diagnostic) == {CompatibilityReasonCode.DATA_PROVENANCE_MISMATCH}


def test_failed_and_missing_candidates_do_not_enter_comparison() -> None:
    model = _model(
        _candidate(0, result_status=ExperimentResultStatus.FAILED),
        _candidate(1, result_status=ExperimentResultStatus.MISSING_RESULT),
    )

    diagnostic = ExperimentCompatibilityService().diagnose(model)

    assert diagnostic.status is CompatibilityStatus.NOT_EVALUABLE
    assert diagnostic.reference_candidate_id is None
    assert diagnostic.unavailable_candidate_ids == ("candidate-0", "candidate-1")
    assert _reasons(diagnostic) == {
        CompatibilityReasonCode.EXECUTION_FAILED,
        CompatibilityReasonCode.RESULT_NOT_AVAILABLE,
    }


def test_diagnostic_rejects_or_redacts_unsafe_values() -> None:
    mismatch = CompatibilityMismatch(
        candidate_id="candidate-1",
        dimension="configuration",
        reason_code=CompatibilityReasonCode.INTEGRITY_MISMATCH,
        reference_value={"api_key": "SECRET_SENTINEL"},
        candidate_value={"value": float("nan")},
        message="configuration differs safely",
    )

    payload = mismatch.to_dict()
    assert "api_key" not in payload["reference_value"]
    assert payload["candidate_value"] == {"value": "<redacted>"}
    with pytest.raises(ValueError, match="unsafe"):
        CompatibilityMismatch(
            candidate_id="candidate-1",
            dimension="configuration",
            reason_code=CompatibilityReasonCode.INTEGRITY_MISMATCH,
            message="/Users/secret must not be exposed",
        )
