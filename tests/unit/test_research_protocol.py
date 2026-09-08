from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backend.app.research_protocol import (
    CandidateSet,
    CandidateSetStatus,
    OOSEvaluationRecord,
    OOSObservationStatus,
    ProtocolStatus,
    ResearchPersistenceError,
    ResearchProtocol,
    ResearchProtocolError,
    ResearchProtocolRepository,
    SelectionDecision,
    StrategyFreezeRecord,
)
from strategies import StrategyVersion
from tests.unit.test_backtest_repository import _run
from tests.unit.test_strategy_repository import _definition


def _protocol() -> ResearchProtocol:
    return ResearchProtocol(
        protocol_id="holdout-2026",
        protocol_version=1,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        is_start_date=date(2026, 1, 2),
        is_end_date=date(2026, 1, 4),
        oos_start_date=date(2026, 1, 6),
        oos_end_date=date(2026, 1, 8),
        selection_rules=("human review of IS metrics",),
        allowed_metrics=("cagr", "max_drawdown"),
        forbidden_actions=("oos_back_selection", "oos_parameter_tuning"),
        data_policy={"snapshot_note": "request provenance only"},
        execution_policy={"execution_rule": "next_trading_day_open"},
        evaluation_policy={"oos_selection_allowed": False},
        provenance={"source": "unit-test"},
    )


def _version() -> StrategyVersion:
    definition = _definition("repo")
    return StrategyVersion(
        strategy_id="repo",
        version_id="repo-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )


def _is_run(version: StrategyVersion):
    return replace(_run(), strategy_version_content_hash=version.content_hash)


def _oos_run(version: StrategyVersion):
    run = _is_run(version)
    result = replace(
        run.backtest_result,
        start_date=date(2026, 1, 6),
        end_date=date(2026, 1, 8),
    )
    return replace(run, backtest_result=result)


def _candidate_set() -> CandidateSet:
    return CandidateSet(
        candidate_set_id="candidate-set-1",
        protocol_id="holdout-2026",
        strategy_version_ids=("repo-v1",),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _advance_to_is(repository: ResearchProtocolRepository) -> CandidateSet:
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set())
    locked = repository.lock_candidate_set("candidate-set-1")
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    repository.transition_protocol("holdout-2026", ProtocolStatus.IS_EVALUATED)
    return locked


def _selection() -> SelectionDecision:
    return SelectionDecision(
        decision_id="selection-1",
        protocol_id="holdout-2026",
        candidate_set_id="candidate-set-1",
        selected_strategy_version_id="repo-v1",
        is_backtest_run_ids=("repo-run",),
        selected_metrics={"cagr": 0.12, "max_drawdown": -0.2},
        rationale="Human researcher selected the candidate using IS evidence only.",
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        data_provenance={"scope": "in_sample"},
    )


def _freeze(version: StrategyVersion) -> StrategyFreezeRecord:
    return StrategyFreezeRecord(
        freeze_id="freeze-1",
        protocol_id="holdout-2026",
        strategy_version_id="repo-v1",
        strategy_version_content_hash=version.content_hash or "",
        selection_decision_id="selection-1",
        frozen_at=datetime(2026, 9, 8, tzinfo=UTC),
        reason="Selected candidate is frozen before OOS observation.",
    )


def test_protocol_rejects_overlapping_or_insufficiently_separated_split() -> None:
    with pytest.raises(ResearchProtocolError, match="overlap"):
        replace(_protocol(), oos_start_date=date(2026, 1, 4))

    with pytest.raises(ResearchProtocolError, match="required separation"):
        replace(_protocol(), gap_days=3)


def test_protocol_status_transition_is_one_way() -> None:
    frozen = _protocol().with_status(ProtocolStatus.FROZEN)

    assert frozen.status == ProtocolStatus.FROZEN
    with pytest.raises(ResearchProtocolError, match="invalid protocol transition"):
        frozen.with_status(ProtocolStatus.DRAFT)


def test_repository_persists_append_only_protocol_state(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set())
    assert repository.get_candidate_set("candidate-set-1").status == CandidateSetStatus.OPEN

    locked = repository.lock_candidate_set("candidate-set-1")
    assert locked.status == CandidateSetStatus.LOCKED
    assert repository.get_protocol("holdout-2026").status == ProtocolStatus.DRAFT

    frozen = repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    assert frozen.status == ProtocolStatus.FROZEN
    assert (
        ResearchProtocolRepository(tmp_path / "research.db").get_protocol("holdout-2026").status
        == ProtocolStatus.FROZEN
    )


def test_protocol_cannot_freeze_without_one_locked_candidate_set(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())

    with pytest.raises(ResearchProtocolError, match="locked candidate set"):
        repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)


def test_selection_accepts_is_runs_only_and_requires_locked_candidate_set(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository)

    with pytest.raises(ResearchProtocolError, match="IS backtest runs only"):
        repository.create_selection(
            _selection(),
            candidate_set=candidate_set,
            runs=(_oos_run(version),),
        )

    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),)
    )
    assert decision.selected_strategy_version_id == "repo-v1"
    assert repository.get_selection("selection-1") == decision

    with pytest.raises(ResearchProtocolError, match="already has a selection decision"):
        repository.create_selection(
            replace(_selection(), decision_id="selection-2"),
            candidate_set=candidate_set,
            runs=(_is_run(version),),
        )


def test_freeze_requires_selection_and_hash_match(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),)
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)

    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)
    assert repository.get_freeze("freeze-1") == freeze

    with pytest.raises(ResearchProtocolError, match="content hash"):
        repository.create_freeze(
            replace(_freeze(version), freeze_id="freeze-2", strategy_version_content_hash="bad"),
            decision=decision,
            version=version,
        )


def test_observed_oos_is_single_use_and_must_follow_freeze(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),)
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)
    evaluation = OOSEvaluationRecord(
        evaluation_id="oos-1",
        protocol_id="holdout-2026",
        freeze_id="freeze-1",
        strategy_version_id="repo-v1",
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        provenance={"selection_allowed": False},
    )

    repository.create_oos_evaluation(evaluation, freeze=freeze, run=_oos_run(version))
    assert repository.list_oos_evaluations("holdout-2026")[0].untouched_oos is False
    assert repository.transition_protocol("holdout-2026", ProtocolStatus.OOS_EVALUATED).status == (
        ProtocolStatus.OOS_EVALUATED
    )

    with pytest.raises(ResearchProtocolError, match="already been observed"):
        repository.create_oos_evaluation(
            replace(evaluation, evaluation_id="oos-2"), freeze=freeze, run=_oos_run(version)
        )


def test_candidate_set_cannot_change_after_protocol_freezes(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set())
    repository.lock_candidate_set("candidate-set-1")
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)

    with pytest.raises(ResearchProtocolError, match="must be draft"):
        repository.create_candidate_set(
            replace(_candidate_set(), candidate_set_id="candidate-set-2")
        )


def test_selection_run_ids_must_match_and_freeze_uses_persisted_decision(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository)

    with pytest.raises(ResearchProtocolError, match="do not match"):
        repository.create_selection(
            replace(_selection(), is_backtest_run_ids=("missing",)),
            candidate_set=candidate_set,
            runs=(_is_run(version),),
        )

    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),)
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    with pytest.raises(ResearchProtocolError, match="recorded selection"):
        repository.create_freeze(
            replace(_freeze(version), selection_decision_id="missing"),
            decision=decision,
            version=version,
        )


def test_oos_range_and_transition_require_observed_record(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),)
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)

    with pytest.raises(ResearchProtocolError, match="requires an observed OOS record"):
        repository.transition_protocol("holdout-2026", ProtocolStatus.OOS_EVALUATED)

    evaluation = OOSEvaluationRecord(
        evaluation_id="oos-invalid-range",
        protocol_id="holdout-2026",
        freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    with pytest.raises(ResearchProtocolError, match="exactly match"):
        repository.create_oos_evaluation(
            evaluation,
            freeze=freeze,
            run=replace(
                _oos_run(version),
                backtest_result=replace(
                    _oos_run(version).backtest_result,
                    end_date=date(2026, 1, 7),
                ),
            ),
        )


def test_corrupted_persisted_protocol_is_rejected(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    repository.create_protocol(_protocol())
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE research_protocols SET payload_json = ? WHERE protocol_id = ?",
            ('{"protocol_id":"holdout-2026","created_at":"not-a-date"}', "holdout-2026"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ResearchPersistenceError, match="integrity checks"):
        repository.get_protocol("holdout-2026")
