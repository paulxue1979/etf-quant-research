from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime

import pytest

from analytics.models import MetricValue
from backend.app.research_protocol import (
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocol,
    SelectionDecision,
    StrategyFreezeRecord,
)
from data.models import PriceField
from research.exceptions import (
    OosAlreadyObservedError,
    OosConfigurationMismatchError,
    OosDomainError,
    OosPreconditionError,
    OosProvenanceError,
    OosRangeMismatchError,
    OosResultIntegrityError,
    OosStrategyIdentityMismatchError,
)
from research.oos import (
    OosBoundarySignalPolicy,
    OosDataProvenance,
    OosEvaluationConfig,
    OosEvaluationIdentity,
    OosEvaluationRange,
    OosEvaluationResult,
    OosEvaluationSpec,
    OosPerformanceSummary,
    PortfolioInitializationMode,
    validate_one_shot_result,
    validate_oos_configuration,
    validate_oos_preconditions,
    validate_oos_range,
    validate_oos_strategy_identity,
)
from strategies import StrategyVersion
from tests.unit.test_strategy_repository import _definition

IS_START = date(2026, 1, 2)
IS_END = date(2026, 1, 30)
OOS_START = date(2026, 2, 3)
OOS_END = date(2026, 2, 10)
WARMUP_START = date(2025, 12, 1)
CREATED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _research_config() -> ResearchEvaluationConfig:
    return ResearchEvaluationConfig(
        price_field_used=PriceField.ADJUSTED_CLOSE.value,
        initial_capital=10_000.0,
        commission={"rate": 0.001, "per_order": 1.0},
        slippage=0.002,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy={"frequency": "weekly", "threshold": 0.05},
        engine_version="phase-3.0",
    )


def _protocol(*, status: str = ProtocolStatus.SELECTION_RECORDED) -> ResearchProtocol:
    return ResearchProtocol(
        protocol_id="protocol-8f1",
        protocol_version=1,
        created_at=CREATED_AT,
        is_start_date=IS_START,
        is_end_date=IS_END,
        oos_start_date=OOS_START,
        oos_end_date=OOS_END,
        selection_rules=("IS metrics only",),
        allowed_metrics=("cagr", "max_drawdown"),
        forbidden_actions=("oos_back_selection",),
        evaluation_config=_research_config(),
        status=status,
    )


def _version() -> StrategyVersion:
    definition = _definition("oos-strategy")
    return StrategyVersion(
        strategy_id="oos-strategy",
        version_id="oos-strategy-v1",
        version_number=1,
        created_at=CREATED_AT,
        configuration=definition,
    )


def _selection(version: StrategyVersion) -> SelectionDecision:
    return SelectionDecision(
        decision_id="selection-8f1",
        protocol_id="protocol-8f1",
        candidate_set_id="candidate-set-8f1",
        selected_strategy_version_id=version.version_id,
        is_backtest_run_ids=("is-run-8f1",),
        selected_metrics={"cagr": 0.12},
        rationale="Selected from IS evidence only.",
        created_at=CREATED_AT,
    )


def _freeze(version: StrategyVersion) -> StrategyFreezeRecord:
    return StrategyFreezeRecord(
        freeze_id="freeze-8f1",
        protocol_id="protocol-8f1",
        strategy_version_id=version.version_id,
        strategy_version_content_hash=version.content_hash or "",
        selection_decision_id="selection-8f1",
        frozen_at=CREATED_AT,
        reason="Freeze exact selected strategy before OOS.",
    )


def _identity(version: StrategyVersion) -> OosEvaluationIdentity:
    return OosEvaluationIdentity(
        protocol_id="protocol-8f1",
        selection_decision_id="selection-8f1",
        strategy_freeze_id="freeze-8f1",
        strategy_version_id=version.version_id,
        strategy_content_hash=version.content_hash or "",
    )


def _oos_config() -> OosEvaluationConfig:
    return OosEvaluationConfig.from_research_evaluation_config(
        _research_config(), analytics_version="analytics-4.1"
    )


def _provenance(**changes: object) -> OosDataProvenance:
    values: dict[str, object] = {
        "source": "cache",
        "data_reference": {"symbol": "QQQ", "frequency": "daily"},
        "requested_start": OOS_START,
        "requested_end": OOS_END,
        "warmup_start": WARMUP_START,
        "frequency": "daily",
        "price_field": PriceField.ADJUSTED_CLOSE,
    }
    values.update(changes)
    return OosDataProvenance(**values)


def _summary() -> OosPerformanceSummary:
    return OosPerformanceSummary(
        metrics={
            "cagr": MetricValue.available(0.12),
            "max_drawdown": MetricValue.not_evaluable("not enough observations"),
        }
    )


def _result(**changes: object) -> OosEvaluationResult:
    version = _version()
    values: dict[str, object] = {
        "oos_result_id": "oos-result-8f1",
        "protocol_id": "protocol-8f1",
        "selection_decision_id": "selection-8f1",
        "strategy_freeze_id": "freeze-8f1",
        "strategy_version_id": version.version_id,
        "strategy_content_hash": version.content_hash,
        "backtest_run_id": "oos-run-8f1",
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "warmup_start": WARMUP_START,
        "price_field_used": PriceField.ADJUSTED_CLOSE,
        "configuration_hash": _oos_config().configuration_hash,
        "engine_version": "phase-3.0",
        "analytics_version": "analytics-4.1",
        "data_provenance": _provenance(),
        "performance_summary": _summary(),
        "created_at": CREATED_AT,
    }
    values.update(changes)
    return OosEvaluationResult(**values)


def test_valid_identity_and_preconditions_bind_exact_frozen_version() -> None:
    version = _version()
    identity = _identity(version)

    validate_oos_strategy_identity(_selection(version), _freeze(version), version, identity)
    assert validate_oos_preconditions(
        _protocol(), _selection(version), _freeze(version), version
    ) == (identity)


@pytest.mark.parametrize(
    "changes",
    [{"protocol_id": ""}, {"strategy_content_hash": "not-a-hash"}],
)
def test_identity_rejects_empty_id_and_malformed_hash(changes: dict[str, str]) -> None:
    with pytest.raises(OosDomainError):
        OosEvaluationIdentity(
            protocol_id=changes.get("protocol_id", "protocol"),
            selection_decision_id="selection",
            strategy_freeze_id="freeze",
            strategy_version_id="version",
            strategy_content_hash=changes.get("strategy_content_hash", "a" * 64),
        )


def test_identity_rejects_strategy_version_and_content_hash_mismatch() -> None:
    version = _version()
    with pytest.raises(OosStrategyIdentityMismatchError):
        validate_oos_strategy_identity(
            _selection(version),
            replace(_freeze(version), strategy_version_id="other-version"),
            version,
            _identity(version),
        )
    with pytest.raises(OosStrategyIdentityMismatchError):
        validate_oos_strategy_identity(
            _selection(version),
            replace(_freeze(version), strategy_version_content_hash="b" * 64),
            version,
            _identity(version),
        )


def test_preconditions_require_selection_recorded_and_matching_protocol() -> None:
    version = _version()
    with pytest.raises(OosPreconditionError):
        validate_oos_preconditions(
            _protocol(status=ProtocolStatus.IS_EVALUATED),
            _selection(version),
            _freeze(version),
            version,
        )
    with pytest.raises(OosPreconditionError):
        validate_oos_preconditions(
            _protocol(),
            replace(_selection(version), protocol_id="other-protocol"),
            _freeze(version),
            version,
        )


def test_ranges_require_non_overlap_and_warmup_before_oos() -> None:
    validate_oos_range(IS_START, IS_END, OOS_START, OOS_END, warmup_start=WARMUP_START)
    with pytest.raises(OosRangeMismatchError):
        validate_oos_range(IS_START, OOS_START, OOS_START, OOS_END)
    with pytest.raises(OosRangeMismatchError):
        validate_oos_range(IS_END, IS_START, OOS_START, OOS_END)
    with pytest.raises(OosRangeMismatchError):
        validate_oos_range(
            IS_START,
            IS_END,
            OOS_START,
            OOS_END,
            warmup_start=date(2026, 2, 4),
        )


def test_spec_freezes_fresh_capital_and_signal_boundary_policy() -> None:
    version = _version()
    spec = OosEvaluationSpec(
        identity=_identity(version),
        evaluation_range=OosEvaluationRange(
            oos_start=OOS_START,
            oos_end=OOS_END,
            warmup_start=WARMUP_START,
            evaluation_start=OOS_START,
            evaluation_end=OOS_END,
        ),
        configuration=_oos_config(),
        data_provenance=_provenance(),
    )
    assert spec.portfolio_initialization is PortfolioInitializationMode.FRESH_CAPITAL
    assert spec.boundary_signal_policy is OosBoundarySignalPolicy.OOS_SIGNALS_ONLY
    assert spec.one_shot is True


@pytest.mark.parametrize(
    "field", ["initial_capital", "commission", "slippage", "analytics_version"]
)
def test_configuration_requires_exact_match(field: str) -> None:
    expected = _oos_config()
    changes = {
        "initial_capital": 20_000.0,
        "commission": 0.002,
        "slippage": 0.01,
        "analytics_version": "analytics-other",
    }
    actual = replace(expected, **{field: changes[field]})
    with pytest.raises(OosConfigurationMismatchError) as error:
        validate_oos_configuration(expected, actual)
    assert field in error.value.details["fields"]


def test_provenance_is_safe_and_rejects_secret_path_nan_and_infinity() -> None:
    safe = _provenance(data_reference={"symbol": "QQQ", "rows": 10})
    assert safe.to_dict()["data_reference"] == {"symbol": "QQQ", "rows": 10}
    with pytest.raises(OosProvenanceError):
        _provenance(data_reference={"api_key": "redacted"})
    with pytest.raises(OosProvenanceError):
        _provenance(data_reference={"note": "SECRET_SENTINEL_8F1"})
    with pytest.raises(OosProvenanceError):
        _provenance(data_reference={"path": "/Users/private/data.json"})
    with pytest.raises(OosDomainError):
        _oos_config().__class__(
            initial_capital=float("nan"),
            commission=0,
            commission_per_order=0,
            slippage=0,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "daily", "threshold": None},
            engine_version="phase-3.0",
            analytics_version="analytics",
        )
    with pytest.raises(OosProvenanceError):
        _provenance(data_reference={"rows": float("inf")})


def test_result_is_immutable_hashed_without_created_at_and_round_trips() -> None:
    result = _result()
    later = replace(result, created_at=CREATED_AT.replace(hour=13))
    assert result.result_hash == later.result_hash
    payload = result.to_dict()
    restored = OosEvaluationResult.from_dict(payload)
    assert restored == result
    assert restored.result_hash == result.result_hash
    with pytest.raises(FrozenInstanceError):
        result.protocol_id = "changed"  # type: ignore[misc]


def test_result_rejects_provenance_mismatch_and_malformed_payload() -> None:
    with pytest.raises(OosResultIntegrityError):
        _result(data_provenance=_provenance(requested_start=date(2026, 2, 4)))
    with pytest.raises(OosResultIntegrityError):
        OosEvaluationResult.from_dict({"data_provenance": {}, "performance_summary": {}})


def test_one_shot_result_allows_exact_replay_but_rejects_replacement() -> None:
    first = _result()
    validate_one_shot_result(first, _result())
    with pytest.raises(OosAlreadyObservedError):
        validate_one_shot_result(first, _result(backtest_run_id="different-run"))
